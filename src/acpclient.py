"""One-shot ACP stdio client on the GrokSeat leader socket."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from seat import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    DATA,
    DEFAULT_CWD,
    SOCK,
    SOCK_NAME,
    agent_argv,
    always_approve,
    child_env,
)

NotifyFn = Callable[[dict[str, Any]], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AcpError(RuntimeError):
    pass


class AcpClient:
    def __init__(self, name: str = "grctl") -> None:
        self.name = name
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.rid = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.notes: list[dict[str, Any]] = []
        self.agent_chunks: list[str] = []
        self.user_chunks: list[str] = []
        self.on_note: NotifyFn | None = None
        self._load_done = False
        self._workspace_root: Path | None = None
        DATA.mkdir(parents=True, exist_ok=True)
        self.raw_path = DATA / f"acp_{name}.jsonl"

    async def start(self) -> None:
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        self.proc = await asyncio.create_subprocess_exec(
            *agent_argv(),
            "--leader",
            "--leader-socket",
            SOCK,
            "stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env(),
            cwd=DEFAULT_CWD,
            creationflags=flags,
        )
        asyncio.create_task(self._stderr())
        asyncio.create_task(self._stdout())

    async def _stderr(self) -> None:
        stderr = self.proc.stderr if self.proc else None
        if stderr is None:
            return
        err_path = DATA / f"acp_{self.name}.err.log"
        while True:
            line = await stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace")
            with err_path.open("a", encoding="utf-8") as fh:
                fh.write(text)

    async def _stdout(self) -> None:
        stdout = self.proc.stdout if self.proc else None
        if stdout is None:
            return
        while True:
            line = await stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            with self.raw_path.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and "method" in msg:
            method = msg.get("method")
            if method == "session/request_permission":
                outcome = await self._permission_outcome(msg.get("params") or {})
                await self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "result": {"outcome": outcome},
                    }
                )
                return
            if method in {"fs/read_text_file", "fs/readTextFile"}:
                try:
                    path = self._resolve_workspace_path((msg.get("params") or {}).get("path"))
                    content = path.read_text(encoding="utf-8", errors="replace")
                    await self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": content}})
                except (OSError, ValueError, PermissionError):
                    await self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32000, "message": "file read denied or unavailable"},
                        }
                    )
                return
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "error": {"code": -32601, "message": f"not implemented: {method}"},
                }
            )
            return
        if "id" in msg:
            fut = self.pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
            return
        method = msg.get("method")
        params = msg.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate") or method
        chunk = ""
        content = update.get("content")
        if isinstance(content, dict):
            chunk = content.get("text") or ""
        elif isinstance(content, str):
            chunk = content
        tool = None
        if kind == "tool_call":
            tool = update.get("title") or update.get("kind") or update.get("toolCallId")
        note = {
            "ts": utc_now(),
            "method": method,
            "kind": kind,
            "chunk": chunk,
            "tool": tool,
            "sid": params.get("sessionId"),
            "stopReason": params.get("stopReason") or update.get("stopReason"),
            "live": self._load_done,
        }
        self.notes.append(note)
        if chunk:
            if kind in {"agent_message_chunk", "agent_message"}:
                self.agent_chunks.append(chunk)
            if kind in {"user_message_chunk", "user_message", "user_message_update"}:
                self.user_chunks.append(chunk)
        if self.on_note:
            self.on_note(note)

    @staticmethod
    def _option(options: list[dict[str, Any]], *kinds: str) -> str | None:
        for kind in kinds:
            for option in options:
                if option.get("kind") == kind and option.get("optionId"):
                    return str(option["optionId"])
        return None

    async def _permission_outcome(self, params: dict[str, Any]) -> dict[str, Any]:
        options = [item for item in (params.get("options") or []) if isinstance(item, dict)]
        allow = self._option(options, "allow_always", "allow_once") if always_approve() else None
        if allow:
            return {"outcome": "selected", "optionId": allow}

        reject = self._option(options, "reject_once", "reject_always")
        allow_once = self._option(options, "allow_once")
        if sys.stdin.isatty() and allow_once:
            sys.stderr.write("ACP tool permission requested. Approve once? [y/N] ")
            sys.stderr.flush()
            try:
                answer = (await asyncio.to_thread(input)).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in {"y", "yes"}:
                return {"outcome": "selected", "optionId": allow_once}
        if reject:
            return {"outcome": "selected", "optionId": reject}
        return {"outcome": "cancelled"}

    def _resolve_workspace_path(self, raw_path: Any) -> Path:
        if self._workspace_root is None:
            raise PermissionError("workspace is not set")
        text = str(raw_path or "").strip()
        if not text:
            raise ValueError("path is empty")
        requested = Path(text)
        if not requested.is_absolute():
            requested = self._workspace_root / requested
        resolved = requested.resolve(strict=False)
        if resolved != self._workspace_root and self._workspace_root not in resolved.parents:
            raise PermissionError("path is outside workspace")
        return resolved

    async def _write(self, msg: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def req(self, method: str, params: dict[str, Any] | None = None, timeout: float = 40) -> dict[str, Any]:
        self.rid += 1
        rid = self.rid
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[rid] = fut
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self.pending.pop(rid, None)
            raise AcpError(f"ACP {method} timeout {timeout}s") from exc
        if "error" in msg:
            raise AcpError(f"ACP {method} error: {msg['error']}")
        return msg.get("result") or {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def initialize(self) -> dict[str, Any]:
        return await self.req(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "grctl", "title": "grctl", "version": "0.3.0"},
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            },
            timeout=30,
        )

    async def list_sessions(self) -> list[dict[str, Any]]:
        raw = await self.req("_x.ai/sessions/list", {}, timeout=12)
        rows = extract_sessions(raw)
        if not rows:
            rows = extract_sessions({"result": raw})
        return rows

    async def session_new(self, cwd: str, title: str | None = None) -> dict[str, Any]:
        self._workspace_root = Path(cwd).resolve(strict=False)
        params: dict[str, Any] = {"cwd": cwd, "mcpServers": []}
        if title:
            params["title"] = title
            params["_meta"] = {"title": title}
        return await self.req("session/new", params, timeout=50)

    async def load(self, session_id: str, cwd: str) -> dict[str, Any]:
        self._load_done = False
        self._workspace_root = Path(cwd).resolve(strict=False)
        result = await self.req(
            "session/load",
            {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
            timeout=20,
        )
        self._load_done = True
        return result

    async def prompt(self, session_id: str, text: str, timeout: float = 180) -> dict[str, Any]:
        self.agent_chunks.clear()
        return await self.req(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )

    async def cancel(self, session_id: str) -> None:
        await self.notify("session/cancel", {"sessionId": session_id})

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        if not proc:
            return
        pid = proc.pid
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        if pid:
            from seat import is_no_leader, list_grok

            cmd = ""
            for row in list_grok():
                if row["pid"] == pid:
                    cmd = row.get("cmd") or ""
                    break
            if cmd and not is_no_leader(cmd) and SOCK_NAME in cmd and "stdio" in cmd:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
        try:
            await asyncio.wait_for(proc.wait(), timeout=1)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass


def extract_sessions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    rows = raw.get("sessions")
    if rows is None and isinstance(raw.get("result"), dict):
        inner = raw["result"]
        rows = inner.get("sessions")
        if rows is None and isinstance(inner.get("result"), dict):
            rows = inner["result"].get("sessions")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def sid_of(row: dict[str, Any]) -> str:
    return str(row.get("sessionId") or row.get("id") or row.get("session_id") or "")


def turns_from_notes(notes: list[dict[str, Any]], *, live_only: bool = False) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    role: str | None = None
    text: list[str] = []
    tool: str | None = None

    def flush() -> None:
        nonlocal role, text, tool
        if role is None:
            return
        body = "".join(text)
        if body or tool:
            item: dict[str, Any] = {"role": role, "text": body}
            if tool:
                item["tool"] = tool
            turns.append(item)
        role, text, tool = None, [], None

    for n in notes:
        if live_only and not n.get("live"):
            continue
        kind = n.get("kind")
        chunk = n.get("chunk") or ""
        if kind in {"user_message_chunk", "user_message", "user_message_update"}:
            if role != "user":
                flush()
                role = "user"
            text.append(chunk)
        elif kind in {"agent_message_chunk", "agent_message"}:
            if role != "assistant":
                flush()
                role = "assistant"
            text.append(chunk)
        elif kind == "tool_call" and n.get("tool"):
            flush()
            turns.append({"role": "assistant", "text": "", "tool": n.get("tool")})
    flush()
    return turns
