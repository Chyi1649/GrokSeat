# Commands

Run via `scripts\grctl.cmd` or `python src\grctl.py`. `--json` is supported on the listed commands.

```text
grctl leader start|status|stop
grctl tui start [--cwd DIR] [--resume ID]
grctl tui stop
grctl up [--attach-last]
grctl down [--keep-leader]
grctl seat new --cwd <DIR> [--mkdir] [--title TEXT]
grctl seat open <id>
grctl seat close
grctl sessions [--json]
grctl seats [--json]
grctl attach <id>
grctl current [--json]
grctl status [<id>] [--json]
grctl history [<id>] [--json] [--limit N]
grctl send <text> | --file <path>
grctl result [--json]
grctl watch [--timeout SEC]
grctl stop
grctl detach
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | generic error |
| 2 | no Target (`send` / `watch` / `stop` / `result` / `history` without current) |
| 3 | kitchen leader not running |
| 4 | refused (`not_openable`, ordinary Grok session, unknown id) |

## `sessions --json`

Each row: `id, title, cwd, online, resident_pager, activity, controllable, openable, reason`.

Root: `current, last, online[], openable[], socket, pager_alive`.

| reason | controllable | openable |
|---|---|---|
| `online` | true | true |
| `known_dormant` | false | true |
| `not_grokseat` | false | false |

`controllable` = a GrokSeat window is in that session **now**.
`openable` = `seat open` is allowed (online **or** known id whose window is closed).
`known_dormant` = id is in local `known_seat_ids` and not currently online.

Bot lists **`openable=true`**. Do not treat `controllable` as the only “can open” flag.

## `seats --json`

```json
{
  "online": [{"id": "sess-online", "title": "Demo", "cwd": "C:\\proj\\demo", "activity": "idle"}],
  "openable_dormant": [{"id": "sess-old", "title": "Old", "cwd": "C:\\proj\\other", "activity": "dormant"}]
}
```

Ordinary `--no-leader` sessions are not listed as wakeable.

## Other JSON

`status`: `id, online, state (Idle|Running|Offline), cwd, task, updated_at, awaiting_permission` (false unless a real signal exists).

`history`: `id, turns[{role, text, tool?}]`.

`result`: `id, state, incremental_text`.

`current`: `{ "current": "<id>" | null }`.

`seat open` refusal: `{ "error": "not_openable", "reason": "not_grokseat", "id": "…" }`.
