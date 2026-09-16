"""Minimal grctl: local CLI → GrokSeat leader + native TUI."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import store
from acpclient import AcpClient, AcpError, sid_of, turns_from_notes
from seat import (
    DEFAULT_CWD,
    SOCK,
    grokseat_socket_procs,
    lock_holders,
    LOCK,
    normalize_cwd,
    ordinary_grok,
    pager_resume_id,
    seat_leader,
    seat_pagers,
    start_leader,
    start_tui,
    stop_leader,
    stop_tui,
)

EX_OK = 0
EX_GENERIC = 1
EX_NO_TARGET = 2
EX_NO_LEADER = 3
EX_REFUSED = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(as_json: bool, payload: Any, text: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def die(code: int, as_json: bool, payload: Any, text: str) -> int:
    emit(as_json, payload, text)
    return code


def require_leader(as_json: bool) -> int | None:
    if seat_leader():
        return None
    return die(
        EX_NO_LEADER,
        as_json,
        {"error": "leader_not_running", "socket": SOCK, "hint": "grctl leader start"},
        "leader not running on leader-grokseat.sock; run: grctl leader start",
    )


def require_target(as_json: bool) -> tuple[str, str] | int:
    data = store.load()
    sid = data.get("current")
    if not sid:
        return die(
            EX_NO_TARGET,
            as_json,
            {"error": "no_target", "current": None},
            "no Target; run: grctl attach <id>",
        )
    return str(sid), str(data.get("cwd") or DEFAULT_CWD)


def snapshot_row(row: dict[str, Any], pager_alive: bool) -> dict[str, Any]:
    sid = sid_of(row)
    resident = bool(row.get("resident"))
    activity = str(row.get("activity") or "")
    online = bool(resident and pager_alive)
    if online:
        controllable, openable, reason = True, True, "online"
    elif store.is_known_seat(sid):
        controllable, openable, reason = False, True, "known_dormant"
    else:
        # Never entered this GrokSeat kitchen (includes ordinary --no-leader).
        controllable, openable, reason = False, False, "not_grokseat"
    return {
        "id": sid,
        "title": row.get("title") or sid,
        "cwd": row.get("cwd") or "",
        "online": online,
        "resident_pager": resident,
        "activity": activity or ("idle" if resident else "dormant"),
        "controllable": controllable,
        "openable": openable,
        "reason": reason,
        "updated_at": row.get("lastChangeUnixMs"),
    }


def state_of(row: dict[str, Any] | None, pager_alive: bool) -> str:
    if not row:
        return "Offline"
    snap = snapshot_row(row, pager_alive)
    if not snap["online"]:
        return "Offline"
    if str(snap["activity"]).lower() in {"working", "running"}:
        return "Running"
    return "Idle"


async def with_acp(fn):
    client = AcpClient()
    try:
        await client.start()
        await client.initialize()
        return await fn(client)
    finally:
        await client.close()


async def cmd_sessions(as_json: bool) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    pager_alive = bool(seat_pagers())

    async def go(client: AcpClient):
        return await client.list_sessions()

    try:
        rows = await with_acp(go)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"sessions failed: {exc}")
    snaps = [snapshot_row(r, pager_alive) for r in rows]
    for s in snaps:
        if s.get("reason") == "online":
            store.remember_seat(s["id"], s.get("cwd") or None)
    data = store.load()
    payload = {
        "sessions": snaps,
        "pager_alive": pager_alive,
        "socket": SOCK,
        "current": data.get("current"),
        "last": data.get("last"),
        "online": [s["id"] for s in snaps if s.get("reason") == "online"],
        "openable": [s["id"] for s in snaps if s.get("openable")],
    }
    if as_json:
        emit(True, payload, "")
        return EX_OK
    if not snaps:
        emit(False, payload, "(no sessions)")
        return EX_OK
    lines = []
    for s in snaps:
        flag = "online" if s["online"] else "offline"
        title = (s["title"] or "")[:40]
        lines.append(
            f"{s['id']}  {flag}  {s['reason']:<14}  openable={str(s['openable']).lower():<5}  "
            f"ctrl={str(s['controllable']).lower():<5}  {title}"
        )
    emit(False, payload, "\n".join(lines))
    return EX_OK


def _seat_item(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": s["id"],
        "title": s.get("title") or s["id"],
        "cwd": s.get("cwd") or "",
        "activity": s.get("activity") or "",
        "reason": s.get("reason"),
    }


async def cmd_seats(as_json: bool) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    pager_alive = bool(seat_pagers())

    async def go(client: AcpClient):
        return await client.list_sessions()

    try:
        rows = await with_acp(go)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"seats failed: {exc}")
    snaps = [snapshot_row(r, pager_alive) for r in rows]
    for s in snaps:
        if s.get("reason") == "online":
            store.remember_seat(s["id"], s.get("cwd") or None)
    online = [_seat_item(s) for s in snaps if s.get("reason") == "online"]
    dormant = [_seat_item(s) for s in snaps if s.get("reason") == "known_dormant"]
    data = store.load()
    payload = {
        "online": online,
        "openable_dormant": dormant,
        "current": data.get("current"),
        "last": data.get("last"),
    }
    if as_json:
        emit(True, payload, "")
        return EX_OK
    lines = ["online:"]
    if not online:
        lines.append("  (none)")
    for s in online:
        lines.append(f"  {s['id']}  {s['cwd']}  {s['activity']}  {s['title']}")
    lines.append("openable_dormant:")
    if not dormant:
        lines.append("  (none)")
    for s in dormant:
        lines.append(f"  {s['id']}  {s['cwd']}  {s['activity']}  {s['title']}")
    emit(False, payload, "\n".join(lines))
    return EX_OK


async def cmd_attach(as_json: bool, session_id: str) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    pager_alive = bool(seat_pagers())

    async def go(client: AcpClient):
        return await client.list_sessions()

    try:
        rows = await with_acp(go)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"attach failed: {exc}")
    row = next((r for r in rows if sid_of(r) == session_id), None)
    if row is None:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "unknown_session", "id": session_id},
            f"unknown session {session_id}",
        )
    snap = snapshot_row(row, pager_alive)
    if not snap["controllable"]:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "not_controllable", "session": snap},
            f"refused attach {session_id}: not an online GrokSeat TUI (ordinary --no-leader or stale)",
        )
    store.set_current(session_id, snap["cwd"] or DEFAULT_CWD)
    payload = {"current": session_id, "cwd": snap["cwd"], "online": True}
    emit(as_json, payload, f"attached {session_id}")
    return EX_OK


def cmd_current(as_json: bool) -> int:
    data = store.load()
    payload = {"current": data.get("current")}
    text = str(data.get("current") or "(none)")
    emit(as_json, payload, text)
    return EX_OK


def cmd_detach(as_json: bool) -> int:
    store.set_current(None)
    emit(as_json, {"current": None}, "detached")
    return EX_OK


def reopenable(session_id: str, snap: dict[str, Any]) -> bool:
    if "openable" in snap:
        return bool(snap.get("openable"))
    if snap.get("controllable"):
        return True
    return store.is_known_seat(session_id)


def cmd_tui_stop(as_json: bool) -> int:
    info = stop_tui()
    if info.get("stopped"):
        emit(as_json, info, f"tui stopped pids={info.get('pids') or []}")
        return EX_OK
    return die(EX_GENERIC, as_json, info, "tui stop failed")


def ensure_leader(as_json: bool) -> dict[str, Any] | int:
    try:
        return start_leader()
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"leader start failed: {exc}")


async def list_snaps() -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    pager_alive = bool(seat_pagers())

    async def go(client: AcpClient):
        return await client.list_sessions()

    rows = await with_acp(go)
    snaps = [snapshot_row(r, pager_alive) for r in rows]
    for s in snaps:
        if s.get("online"):
            store.remember_seat(s["id"], s.get("cwd") or None)
    return rows, snaps, pager_alive


async def wait_online(session_id: str, timeout: float = 25.0) -> dict[str, Any] | None:
    client = AcpClient("wait")
    last: dict[str, Any] | None = None
    try:
        await client.start()
        await client.initialize()
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            pager_alive = bool(seat_pagers())
            rows = await client.list_sessions()
            snaps = [snapshot_row(r, pager_alive) for r in rows]
            last = next((s for s in snaps if s["id"] == session_id), None)
            if last and last.get("controllable"):
                if last.get("cwd"):
                    store.remember_seat(session_id, last.get("cwd"))
                return last
            await asyncio.sleep(0.5)
        return last
    finally:
        await client.close()


def tui_already_on(session_id: str) -> bool:
    for row in seat_pagers():
        if pager_resume_id(row.get("cmd") or "") == session_id:
            return True
    return False


def stop_other_seat_tuis() -> dict[str, Any]:
    if not seat_pagers():
        return {"stopped": True, "pids": []}
    return stop_tui()


async def resume_tui(as_json: bool, session_id: str, cwd: str) -> dict[str, Any] | int:
    cwd_abs = str(normalize_cwd(cwd))
    if not tui_already_on(session_id):
        stopped = stop_other_seat_tuis()
        if not stopped.get("stopped"):
            return die(EX_GENERIC, as_json, stopped, "existing TUI could not be stopped")
        try:
            start_tui(cwd_abs, session_id)
        except Exception as exc:
            return die(EX_GENERIC, as_json, {"error": str(exc)}, f"tui start failed: {exc}")
    online = await wait_online(session_id, 25.0)
    if not online or not online.get("controllable"):
        return die(
            EX_GENERIC,
            as_json,
            {"error": "tui_not_online", "id": session_id, "saw": online},
            f"GrokSeat TUI did not become online for {session_id}",
        )
    store.set_current(session_id, online.get("cwd") or cwd_abs)
    return {
        "id": session_id,
        "cwd": online.get("cwd") or cwd_abs,
        "current": session_id,
        "online": True,
        "tui": seat_pagers(),
    }


async def cmd_up(as_json: bool, attach_last: bool) -> int:
    started = ensure_leader(as_json)
    if isinstance(started, int):
        return started
    data = store.load()
    target = None
    if attach_last:
        target = data.get("current") or data.get("last")
    elif data.get("current"):
        target = data.get("current")
    if not target:
        payload = {
            "leader": started,
            "current": None,
            "socket": SOCK,
        }
        emit(as_json, payload, "leader up; current=null")
        return EX_OK
    try:
        _rows, snaps, _pager = await list_snaps()
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"up failed: {exc}")
    snap = next((s for s in snaps if s["id"] == target), None)
    if snap is None:
        payload = {"leader": started, "current": None, "missing": target}
        emit(as_json, payload, f"leader up; session {target} gone; current unchanged")
        return EX_OK
    if attach_last:
        if not reopenable(str(target), snap):
            payload = {"leader": started, "current": data.get("current"), "refused": target}
            emit(as_json, payload, f"leader up; last session not a GrokSeat conversation")
            return EX_OK
        cwd = snap.get("cwd") or data.get("cwd") or DEFAULT_CWD
        resumed = await resume_tui(as_json, str(target), cwd)
        if isinstance(resumed, int):
            return resumed
        resumed["leader"] = started
        emit(as_json, resumed, f"up attach {target}")
        return EX_OK
    if not snap.get("controllable"):
        payload = {
            "leader": started,
            "current": target,
            "online": False,
            "hint": "use --attach-last to resume TUI",
        }
        emit(as_json, payload, f"leader up; current {target} not online")
        return EX_OK
    cwd = snap.get("cwd") or data.get("cwd") or DEFAULT_CWD
    resumed = await resume_tui(as_json, str(target), cwd)
    if isinstance(resumed, int):
        return resumed
    resumed["leader"] = started
    emit(as_json, resumed, f"up {target}")
    return EX_OK


def cmd_down(as_json: bool, keep_leader: bool) -> int:
    store.set_current(None)
    tui = stop_tui()
    leader = None
    if not keep_leader:
        leader = stop_leader()
    leftover = grokseat_socket_procs()
    payload = {
        "current": None,
        "tui": tui,
        "leader": leader if not keep_leader else {"kept": True, **(seat_leader() or {})},
        "grokseat_left": leftover,
        "ordinary": ordinary_grok(),
    }
    leader_failed = bool(leader and not leader.get("stopped") and leader.get("reason") != "not running")
    if not tui.get("stopped") or leader_failed:
        return die(EX_GENERIC, as_json, payload, "down incomplete; one or more GrokSeat processes remain")
    emit(as_json, payload, "down" if not keep_leader else "down (leader kept)")
    return EX_OK


async def cmd_seat_new(as_json: bool, cwd_raw: str, mkdir: bool, title: str | None) -> int:
    try:
        cwd_path = normalize_cwd(cwd_raw)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"bad cwd: {exc}")
    if cwd_path.exists() and not cwd_path.is_dir():
        return die(EX_GENERIC, as_json, {"error": "not_a_directory", "cwd": str(cwd_path)}, f"not a directory: {cwd_path}")
    if not cwd_path.exists():
        if not mkdir:
            return die(
                EX_GENERIC,
                as_json,
                {"error": "cwd_missing", "cwd": str(cwd_path), "hint": "--mkdir"},
                f"cwd does not exist: {cwd_path}",
            )
        cwd_path.mkdir(parents=True, exist_ok=True)
    started = ensure_leader(as_json)
    if isinstance(started, int):
        return started
    cwd = str(cwd_path)

    async def create(client: AcpClient):
        return await client.session_new(cwd, title)

    try:
        created = await with_acp(create)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"session/new failed: {exc}")
    sid = str(created.get("sessionId") or "")
    if not sid:
        return die(EX_GENERIC, as_json, {"error": "no_session_id", "raw": created}, "session/new returned no id")
    store.remember_seat(sid, cwd, title)
    resumed = await resume_tui(as_json, sid, cwd)
    if isinstance(resumed, int):
        return resumed
    payload = {
        "id": sid,
        "cwd": cwd,
        "current": sid,
        "online": True,
        "title": title,
        "leader": started,
        "tui": resumed.get("tui"),
    }
    emit(as_json, payload, f"seat new {sid} cwd={cwd}")
    return EX_OK


async def cmd_seat_open(as_json: bool, session_id: str) -> int:
    started = ensure_leader(as_json)
    if isinstance(started, int):
        return started
    try:
        rows, snaps, _pager = await list_snaps()
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"seat open failed: {exc}")
    snap = next((s for s in snaps if s["id"] == session_id), None)
    if snap is None:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "unknown_session", "id": session_id},
            f"unknown session {session_id}",
        )
    if not snap.get("openable"):
        return die(
            EX_REFUSED,
            as_json,
            {
                "error": "not_openable",
                "reason": snap.get("reason") or "not_grokseat",
                "id": session_id,
                "session": snap,
            },
            f"refused seat open {session_id}: {snap.get('reason') or 'not_openable'}",
        )
    cwd = snap.get("cwd") or store.load().get("cwd") or DEFAULT_CWD
    resumed = await resume_tui(as_json, session_id, cwd)
    if isinstance(resumed, int):
        return resumed
    resumed["leader"] = started
    emit(as_json, resumed, f"seat open {session_id}")
    return EX_OK


async def cmd_seat_close(as_json: bool) -> int:
    data = store.load()
    sid = data.get("current")
    stopped_turn = False
    if sid and seat_leader() and seat_pagers():
        try:
            row = await find_row(str(sid))
            pager_alive = bool(seat_pagers())
            if row and state_of(row, pager_alive) == "Running":
                cwd = str(row.get("cwd") or data.get("cwd") or DEFAULT_CWD)

                async def go(client: AcpClient):
                    await client.load(str(sid), cwd)
                    await client.cancel(str(sid))
                    await asyncio.sleep(0.3)

                await with_acp(go)
                stopped_turn = True
        except Exception:
            stopped_turn = False
    store.set_current(None)
    tui = stop_tui()
    payload = {
        "current": None,
        "stopped_turn": stopped_turn,
        "tui": tui,
        "leader": seat_leader(),
    }
    if not tui.get("stopped"):
        return die(EX_GENERIC, as_json, payload, "seat close incomplete; TUI is still running")
    emit(as_json, payload, "seat closed")
    return EX_OK


async def find_row(session_id: str) -> dict[str, Any] | None:
    async def go(client: AcpClient):
        return await client.list_sessions()

    rows = await with_acp(go)
    return next((r for r in rows if sid_of(r) == session_id), None)


async def cmd_status(as_json: bool, session_id: str | None) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    if not session_id:
        got = require_target(as_json)
        if isinstance(got, int):
            return got
        session_id, _cwd = got
    pager_alive = bool(seat_pagers())
    try:
        row = await find_row(session_id)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"status failed: {exc}")
    snap = snapshot_row(row, pager_alive) if row else {
        "id": session_id,
        "title": session_id,
        "cwd": "",
        "online": False,
        "resident_pager": False,
        "activity": "offline",
        "controllable": False,
        "openable": store.is_known_seat(session_id),
        "reason": "known_dormant" if store.is_known_seat(session_id) else "not_grokseat",
    }
    st = state_of(row, pager_alive)
    payload = {
        "id": session_id,
        "online": snap["online"],
        "state": st,
        "cwd": snap.get("cwd") or "",
        "task": None,
        "updated_at": utc_now(),
        "activity": snap.get("activity"),
        "awaiting_permission": False,
        "openable": snap.get("openable"),
        "reason": snap.get("reason"),
    }
    emit(as_json, payload, f"{session_id}  {st}  online={snap['online']}  {snap.get('cwd')}")
    return EX_OK


async def cmd_history(as_json: bool, session_id: str | None, limit: int) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    if not session_id:
        got = require_target(as_json)
        if isinstance(got, int):
            return got
        session_id, cwd = got
    else:
        cwd = store.load().get("cwd") or DEFAULT_CWD
    pager_alive = bool(seat_pagers())
    try:
        row = await find_row(session_id)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"history failed: {exc}")
    if not row or not snapshot_row(row, pager_alive)["controllable"]:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "offline", "id": session_id},
            f"session {session_id} Offline; will not session/load",
        )
    cwd = str(row.get("cwd") or cwd)

    async def go(client: AcpClient):
        await client.load(session_id, cwd)
        await asyncio.sleep(0.4)
        return turns_from_notes(client.notes, live_only=False)

    try:
        turns = await with_acp(go)
    except AcpError as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"history load failed: {exc}")
    if limit > 0:
        turns = turns[-limit:]
    payload = {"id": session_id, "turns": turns}
    if as_json:
        emit(True, payload, "")
        return EX_OK
    if not turns:
        emit(False, payload, "(empty)")
        return EX_OK
    lines = []
    for t in turns:
        tool = f" tool={t['tool']}" if t.get("tool") else ""
        body = (t.get("text") or "").replace("\n", " ")
        if len(body) > 160:
            body = body[:157] + "..."
        lines.append(f"{t['role']}:{tool} {body}")
    emit(False, payload, "\n".join(lines))
    return EX_OK


async def cmd_send(as_json: bool, text: str) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    got = require_target(as_json)
    if isinstance(got, int):
        return got
    session_id, cwd = got
    pager_alive = bool(seat_pagers())
    try:
        row = await find_row(session_id)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"send failed: {exc}")
    if not row or not snapshot_row(row, pager_alive)["controllable"]:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "offline", "id": session_id},
            f"Target Offline; will not session/load {session_id}",
        )
    cwd = str(row.get("cwd") or cwd)

    async def go(client: AcpClient):
        await client.load(session_id, cwd)
        result = await client.prompt(session_id, text, timeout=180)
        agent = "".join(client.agent_chunks)
        return result, agent

    try:
        prompt_result, agent = await with_acp(go)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"send failed: {exc}")
    stop = (prompt_result or {}).get("stopReason")
    state = "Idle" if str(stop or "").lower() != "cancelled" else "Idle"
    last = {"id": session_id, "state": state, "incremental_text": agent, "stop_reason": stop}
    store.set_last_result(last)
    emit(as_json, last, agent or f"(done stopReason={stop})")
    return EX_OK


async def cmd_result(as_json: bool) -> int:
    got = require_target(as_json)
    if isinstance(got, int):
        return got
    session_id, _cwd = got
    last = store.load().get("last_result") or {}
    if last.get("id") != session_id:
        last = {"id": session_id, "state": "Idle", "incremental_text": ""}
    emit(as_json, last, last.get("incremental_text") or "(none)")
    return EX_OK


async def cmd_watch(as_json: bool, timeout: float) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    got = require_target(as_json)
    if isinstance(got, int):
        return got
    session_id, cwd = got
    pager_alive = bool(seat_pagers())
    try:
        row = await find_row(session_id)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"watch failed: {exc}")
    if not row or not snapshot_row(row, pager_alive)["controllable"]:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "offline", "id": session_id},
            f"Target Offline; will not session/load {session_id}",
        )
    cwd = str(row.get("cwd") or cwd)
    events: list[dict[str, Any]] = []

    def on_note(note: dict[str, Any]) -> None:
        if not note.get("live"):
            return
        events.append(note)
        kind = note.get("kind")
        chunk = note.get("chunk") or ""
        if as_json:
            sys.stdout.write(json.dumps(note, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        elif chunk and kind in {
            "user_message_chunk",
            "agent_message_chunk",
            "agent_thought_chunk",
        }:
            prefix = "user" if "user" in str(kind) else ("thought" if "thought" in str(kind) else "agent")
            sys.stdout.write(f"{prefix}: {chunk}")
            if not chunk.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

    client = AcpClient("watch")
    client.on_note = on_note
    try:
        await client.start()
        await client.initialize()
        await client.load(session_id, cwd)
        deadline = None if timeout <= 0 else asyncio.get_event_loop().time() + timeout
        while True:
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.2)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"watch failed: {exc}")
    finally:
        await client.close()
    live_turns = turns_from_notes(events, live_only=False)
    agent = "".join(n.get("chunk") or "" for n in events if n.get("kind") in {"agent_message_chunk", "agent_message"})
    if agent or live_turns:
        store.set_last_result({"id": session_id, "state": "Idle", "incremental_text": agent})
    if as_json:
        # already streamed events; print summary last
        sys.stdout.write(json.dumps({"id": session_id, "events": len(events)}, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"(watch end events={len(events)})\n")
    return EX_OK


async def cmd_stop(as_json: bool) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    got = require_target(as_json)
    if isinstance(got, int):
        return got
    session_id, cwd = got
    pager_alive = bool(seat_pagers())
    try:
        row = await find_row(session_id)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"stop failed: {exc}")
    if not row or not snapshot_row(row, pager_alive)["controllable"]:
        return die(
            EX_REFUSED,
            as_json,
            {"error": "offline", "id": session_id},
            f"Target Offline; will not session/load {session_id}",
        )
    cwd = str(row.get("cwd") or cwd)

    async def go(client: AcpClient):
        await client.load(session_id, cwd)
        await client.cancel(session_id)
        await asyncio.sleep(0.4)

    try:
        await with_acp(go)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"stop failed: {exc}")
    payload = {"id": session_id, "stopped": True}
    emit(as_json, payload, f"stopped {session_id}")
    return EX_OK


def cmd_leader(as_json: bool, action: str) -> int:
    if action == "start":
        try:
            info = start_leader()
        except Exception as exc:
            return die(EX_GENERIC, as_json, {"error": str(exc)}, f"leader start failed: {exc}")
        emit(
            as_json,
            {"socket": SOCK, **info},
            f"leader pid={info.get('pid')} already={info.get('already')}",
        )
        return EX_OK
    if action == "status":
        row = seat_leader()
        payload = {
            "socket": SOCK,
            "running": bool(row),
            "leader": row,
            "pagers": seat_pagers(),
            "ordinary": ordinary_grok(),
            "lock_holders": lock_holders(LOCK),
        }
        if row:
            emit(as_json, payload, f"leader running pid={row['pid']}")
        else:
            emit(as_json, payload, "leader not running")
        return EX_OK
    if action == "stop":
        try:
            info = stop_leader()
        except Exception as exc:
            return die(EX_GENERIC, as_json, {"error": str(exc)}, f"leader stop failed: {exc}")
        if info.get("stopped"):
            emit(as_json, info, "leader stopped")
            return EX_OK
        if info.get("reason") == "not running":
            emit(as_json, info, "leader not running")
            return EX_OK
        return die(EX_GENERIC, as_json, info, "leader stop failed")
    return EX_GENERIC


def cmd_tui(as_json: bool, cwd: str, resume: str | None) -> int:
    err = require_leader(as_json)
    if err is not None:
        return err
    try:
        info = start_tui(str(normalize_cwd(cwd)), resume)
    except Exception as exc:
        return die(EX_GENERIC, as_json, {"error": str(exc)}, f"tui start failed: {exc}")
    emit(as_json, info, f"tui pid={info['pid']}")
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grctl", description="GrokSeat local controller")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_leader = sub.add_parser("leader")
    p_leader.add_argument("action", choices=["start", "status", "stop"])
    p_leader.add_argument("--json", action="store_true")

    p_tui = sub.add_parser("tui")
    p_tui_sub = p_tui.add_subparsers(dest="tui_cmd", required=True)
    p_tui_start = p_tui_sub.add_parser("start")
    p_tui_start.add_argument("--cwd", default=DEFAULT_CWD)
    p_tui_start.add_argument("--resume")
    p_tui_start.add_argument("--json", action="store_true")
    p_tui_stop = p_tui_sub.add_parser("stop")
    p_tui_stop.add_argument("--json", action="store_true")

    p_up = sub.add_parser("up")
    p_up.add_argument("--attach-last", action="store_true")
    p_up.add_argument("--json", action="store_true")

    p_down = sub.add_parser("down")
    p_down.add_argument("--keep-leader", action="store_true")
    p_down.add_argument("--json", action="store_true")

    p_seat = sub.add_parser("seat")
    p_seat_sub = p_seat.add_subparsers(dest="seat_cmd", required=True)
    p_seat_new = p_seat_sub.add_parser("new")
    p_seat_new.add_argument("--cwd", required=True)
    p_seat_new.add_argument("--mkdir", action="store_true")
    p_seat_new.add_argument("--title")
    p_seat_new.add_argument("--json", action="store_true")
    p_seat_open = p_seat_sub.add_parser("open")
    p_seat_open.add_argument("session_id")
    p_seat_open.add_argument("--json", action="store_true")
    p_seat_close = p_seat_sub.add_parser("close")
    p_seat_close.add_argument("--json", action="store_true")

    p_sessions = sub.add_parser("sessions")
    p_sessions.add_argument("--json", action="store_true")

    p_seats = sub.add_parser("seats")
    p_seats.add_argument("--json", action="store_true")

    p_attach = sub.add_parser("attach")
    p_attach.add_argument("session_id")
    p_attach.add_argument("--json", action="store_true")

    p_current = sub.add_parser("current")
    p_current.add_argument("--json", action="store_true")

    p_status = sub.add_parser("status")
    p_status.add_argument("session_id", nargs="?")
    p_status.add_argument("--json", action="store_true")

    p_hist = sub.add_parser("history")
    p_hist.add_argument("session_id", nargs="?")
    p_hist.add_argument("--json", action="store_true")
    p_hist.add_argument("--limit", type=int, default=0)

    p_send = sub.add_parser("send")
    p_send.add_argument("text", nargs="*")
    p_send.add_argument("--file")
    p_send.add_argument("--json", action="store_true")

    p_result = sub.add_parser("result")
    p_result.add_argument("--json", action="store_true")

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--timeout", type=float, default=0)
    p_watch.add_argument("--json", action="store_true")

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--json", action="store_true")
    p_detach = sub.add_parser("detach")
    p_detach.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    as_json = "--json" in argv
    args = build_parser().parse_args(argv)

    if args.cmd == "leader":
        return cmd_leader(as_json, args.action)
    if args.cmd == "tui":
        if args.tui_cmd == "stop":
            return cmd_tui_stop(as_json)
        return cmd_tui(as_json, args.cwd, args.resume)
    if args.cmd == "up":
        return asyncio.run(cmd_up(as_json, bool(args.attach_last)))
    if args.cmd == "down":
        return cmd_down(as_json, bool(args.keep_leader))
    if args.cmd == "seat":
        if args.seat_cmd == "new":
            return asyncio.run(cmd_seat_new(as_json, args.cwd, bool(args.mkdir), args.title))
        if args.seat_cmd == "open":
            return asyncio.run(cmd_seat_open(as_json, args.session_id))
        if args.seat_cmd == "close":
            return asyncio.run(cmd_seat_close(as_json))
        return EX_GENERIC
    if args.cmd == "sessions":
        return asyncio.run(cmd_sessions(as_json))
    if args.cmd == "seats":
        return asyncio.run(cmd_seats(as_json))
    if args.cmd == "attach":
        return asyncio.run(cmd_attach(as_json, args.session_id))
    if args.cmd == "current":
        return cmd_current(as_json)
    if args.cmd == "status":
        return asyncio.run(cmd_status(as_json, args.session_id))
    if args.cmd == "history":
        return asyncio.run(cmd_history(as_json, args.session_id, args.limit))
    if args.cmd == "send":
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        else:
            text = " ".join(args.text).strip()
        if not text:
            return die(EX_GENERIC, as_json, {"error": "empty_text"}, "send requires text or --file")
        return asyncio.run(cmd_send(as_json, text))
    if args.cmd == "result":
        return asyncio.run(cmd_result(as_json))
    if args.cmd == "watch":
        return asyncio.run(cmd_watch(as_json, args.timeout))
    if args.cmd == "stop":
        return asyncio.run(cmd_stop(as_json))
    if args.cmd == "detach":
        return cmd_detach(as_json)
    return EX_GENERIC


if __name__ == "__main__":
    raise SystemExit(main())
