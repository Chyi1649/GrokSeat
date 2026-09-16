"""GrokSeat process helpers. Never touch --no-leader Grok."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

HOME = Path.home()
GROK = Path(os.environ.get("GROK_BIN") or (HOME / ".grok" / "bin" / "grok.exe"))
SOCK = str(Path(os.environ.get("GROKSEAT_SOCKET") or (HOME / ".grok" / "leader-grokseat.sock")))
LOCK = str(Path(SOCK).with_name(Path(SOCK).stem + ".lock"))
GRCTL_HOME = Path(os.environ.get("GRCTL_HOME") or (HOME / ".grok" / "grctl"))
ROOT = GRCTL_HOME
DATA = GRCTL_HOME / "data"
DEFAULT_CWD = str(Path.cwd())
SOCK_NAME = Path(SOCK).name

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def always_approve() -> bool:
    return os.environ.get("GRCTL_ALWAYS_APPROVE", "").strip().lower() in {"1", "true", "yes", "on"}


def agent_argv() -> list[str]:
    cmd = [str(GROK), "agent"]
    if always_approve():
        cmd.append("--always-approve")
    return cmd


def child_env() -> dict[str, str]:
    """Inherit the caller environment. Do not inject a proxy unless already set."""
    env = os.environ.copy()
    env.setdefault("GROK_DISABLE_AUTOUPDATER", "1")
    return env


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def list_grok() -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='grok.exe'\" | "
            "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    text = (completed.stdout or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    rows = []
    for item in data:
        rows.append(
            {
                "pid": int(item.get("ProcessId") or 0),
                "ppid": int(item.get("ParentProcessId") or 0),
                "cmd": item.get("CommandLine") or "",
            }
        )
    return rows


def is_no_leader(cmd: str) -> bool:
    return "--no-leader" in (cmd or "")


def is_seat_leader(cmd: str) -> bool:
    c = cmd or ""
    return SOCK_NAME in c and "agent" in c and " leader" in c and "stdio" not in c


def is_seat_pager(cmd: str) -> bool:
    c = cmd or ""
    if SOCK_NAME not in c:
        return False
    if is_no_leader(c):
        return False
    if " agent " in f" {c} " and ("stdio" in c or " leader" in c):
        return False
    return "--leader" in c


def is_seat_stdio(cmd: str) -> bool:
    c = cmd or ""
    return SOCK_NAME in c and "stdio" in c


def seat_leader() -> dict[str, Any] | None:
    for row in list_grok():
        if is_seat_leader(row["cmd"]) and pid_alive(row["pid"]):
            return row
    return None


def seat_pagers() -> list[dict[str, Any]]:
    return [row for row in list_grok() if is_seat_pager(row["cmd"]) and pid_alive(row["pid"])]


def ordinary_grok() -> list[dict[str, Any]]:
    return [row for row in list_grok() if is_no_leader(row["cmd"]) and pid_alive(row["pid"])]


def lock_holders(path: str) -> list[int]:
    if not Path(path).exists():
        return []
    rstrtmgr = ctypes.WinDLL("rstrtmgr")
    session = wintypes.DWORD()
    key = (wintypes.WCHAR * 256)()
    rstrtmgr.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPCWSTR]
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        rstrtmgr.RmRegisterResources.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            ctypes.POINTER(wintypes.LPCWSTR),
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_void_p,
        ]
        pth = ctypes.c_wchar_p(path)
        arr = (wintypes.LPCWSTR * 1)(pth)
        rstrtmgr.RmRegisterResources(session.value, 1, arr, 0, None, 0, None)

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ("Process", RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * 256),
                ("strServiceShortName", wintypes.WCHAR * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL),
            ]

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot = wintypes.DWORD()
        rstrtmgr.RmGetList.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        rstrtmgr.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reboot))
        infos = (RM_PROCESS_INFO * max(int(needed.value), 1))()
        count = wintypes.UINT(needed.value)
        rstrtmgr.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reboot))
        return [int(infos[i].Process.dwProcessId) for i in range(int(count.value))]
    finally:
        rstrtmgr.RmEndSession(session.value)


def start_leader() -> dict[str, Any]:
    existing = seat_leader()
    if existing:
        return {"started": False, "already": True, **existing}
    DATA.mkdir(parents=True, exist_ok=True)
    holders = lock_holders(LOCK)
    ordinary_pids = {r["pid"] for r in ordinary_grok()}
    if ordinary_pids.intersection(holders):
        raise RuntimeError(f"grokseat lock held by ordinary Grok {sorted(ordinary_pids.intersection(holders))}")
    if holders:
        still = [h for h in holders if pid_alive(h)]
        if still:
            raise RuntimeError(f"grokseat lock already held by {still}; will not adopt")
    log_path = DATA / "leader.log"
    stdout = log_path.open("a", encoding="utf-8")
    cmd = [
        *agent_argv(),
        "--leader-socket",
        SOCK,
        "leader",
        "--no-exit-on-disconnect",
        "--no-auto-update",
    ]
    flags = (
        DETACHED_PROCESS
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_BREAKAWAY_FROM_JOB
        | CREATE_NO_WINDOW
        | CREATE_UNICODE_ENVIRONMENT
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        env=child_env(),
        cwd=DEFAULT_CWD,
        creationflags=flags,
        close_fds=True,
    )
    for _ in range(20):
        time.sleep(0.2)
        if not pid_alive(proc.pid):
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
            raise RuntimeError(f"leader exited pid={proc.pid}: {tail}")
    after = lock_holders(LOCK)
    if ordinary_pids.intersection(after):
        raise RuntimeError("ordinary Grok took grokseat lock; abort")
    return {"started": True, "already": False, "pid": proc.pid, "ppid": 0, "cmd": " ".join(cmd), "lock_holders": after}


def stop_leader() -> dict[str, Any]:
    row = seat_leader()
    if not row:
        return {"stopped": False, "reason": "not running"}
    if is_no_leader(row["cmd"]):
        raise RuntimeError("refusing to stop a --no-leader process")
    completed = subprocess.run(
        ["taskkill", "/PID", str(row["pid"]), "/F"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    deadline = time.time() + 4
    while time.time() < deadline and pid_alive(row["pid"]):
        time.sleep(0.2)
    stopped = completed.returncode == 0 and not pid_alive(row["pid"])
    return {
        "stopped": stopped,
        "pid": row["pid"],
        "returncode": completed.returncode,
        **({"error": "taskkill_failed"} if not stopped else {}),
    }


def grokseat_socket_procs() -> list[dict[str, Any]]:
    return [row for row in list_grok() if SOCK_NAME in (row.get("cmd") or "") and pid_alive(row["pid"])]


def pager_resume_id(cmd: str) -> str | None:
    parts = (cmd or "").split()
    for i, part in enumerate(parts):
        if part == "--resume" and i + 1 < len(parts):
            return parts[i + 1].strip('"')
    return None


def normalize_cwd(raw: str) -> Path:
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("cwd is empty")
    path = Path(text)
    if not path.is_absolute():
        base = Path.cwd()
        if len(base.parts) <= 1 or str(base) == base.anchor:
            base = Path.home()
        path = base / path
    return path.resolve()


def stop_tui() -> dict[str, Any]:
    """Stop GrokSeat TUI pagers only. Never --no-leader, never the leader process."""
    attempted: list[tuple[int, int]] = []
    killed: list[int] = []
    failed: list[dict[str, Any]] = []
    for row in list_grok():
        cmd = row.get("cmd") or ""
        if is_no_leader(cmd):
            continue
        if not pid_alive(row["pid"]):
            continue
        if not is_seat_pager(cmd):
            continue
        completed = subprocess.run(
            ["taskkill", "/PID", str(row["pid"]), "/F"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        attempted.append((row["pid"], completed.returncode))
    deadline = time.time() + 4
    while time.time() < deadline and seat_pagers():
        time.sleep(0.2)
    remaining = seat_pagers()
    remaining_pids = {row["pid"] for row in remaining}
    for pid, returncode in attempted:
        if returncode == 0 and pid not in remaining_pids:
            killed.append(pid)
        else:
            failed.append({"pid": pid, "returncode": returncode, "error": "taskkill_failed"})
    remaining_safe = [{"pid": row["pid"]} for row in remaining]
    return {"stopped": not failed and not remaining, "pids": killed, "failed": failed, "remaining": remaining_safe}


def start_tui(cwd: str, resume: str | None) -> dict[str, Any]:
    if not Path(cwd).is_dir():
        raise RuntimeError(f"cwd does not exist: {cwd}")
    cmd = [str(GROK), "--leader", "--leader-socket", SOCK, "--cwd", cwd]
    if resume:
        cmd.extend(["--resume", resume])
    flags = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB | CREATE_UNICODE_ENVIRONMENT
    proc = subprocess.Popen(cmd, cwd=cwd, env=child_env(), creationflags=flags, close_fds=False)
    time.sleep(0.8)
    if not pid_alive(proc.pid):
        raise RuntimeError("Bot TUI exited immediately")
    return {"pid": proc.pid, "cmd": " ".join(cmd)}
