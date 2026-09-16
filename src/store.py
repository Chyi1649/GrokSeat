"""Local Target state. Never auto-follow resident."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from seat import GRCTL_HOME

STATE_PATH = Path(GRCTL_HOME) / "state.json"


def _blank() -> dict[str, Any]:
    return {
        "current": None,
        "last": None,
        "cwd": None,
        "last_result": None,
        "known_seat_ids": [],
        "titles": {},
    }


def load() -> dict[str, Any]:
    blank = _blank()
    if not STATE_PATH.exists():
        return blank
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return blank
    if not isinstance(data, dict):
        return blank
    for key, value in blank.items():
        data.setdefault(key, value)
    if data.get("current") and data["current"] not in (data.get("known_seat_ids") or []):
        data["known_seat_ids"] = list(data.get("known_seat_ids") or []) + [str(data["current"])]
    if data.get("current") and not data.get("last"):
        data["last"] = data["current"]
    return data


def save(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_id() -> str | None:
    cur = load().get("current")
    if cur:
        return str(cur)
    return None


def set_current(session_id: str | None, cwd: str | None = None) -> dict[str, Any]:
    data = load()
    data["current"] = session_id
    if session_id:
        data["last"] = session_id
        remember_into(data, session_id)
    if cwd:
        data["cwd"] = cwd
    save(data)
    return data


def remember_into(data: dict[str, Any], session_id: str) -> None:
    if not session_id:
        return
    ids = [str(x) for x in (data.get("known_seat_ids") or [])]
    if session_id not in ids:
        ids.append(session_id)
    data["known_seat_ids"] = ids


def remember_seat(session_id: str, cwd: str | None = None, title: str | None = None) -> None:
    data = load()
    remember_into(data, session_id)
    if cwd:
        data["cwd"] = cwd
    if title:
        titles = dict(data.get("titles") or {})
        titles[session_id] = title
        data["titles"] = titles
    save(data)


def is_known_seat(session_id: str) -> bool:
    data = load()
    if session_id and session_id == data.get("last"):
        return True
    return session_id in [str(x) for x in (data.get("known_seat_ids") or [])]


def set_last_result(result: dict[str, Any]) -> None:
    data = load()
    data["last_result"] = result
    save(data)
