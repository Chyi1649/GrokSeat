# Lessons (verified / forbidden)

## Verified

- Real sync = dedicated leader socket + every client `session/load`s the same session id, then `session/prompt` / `session/update`.
- Ordinary Grok must start with `--no-leader`. GrokSeat TUI must pass `--leader --leader-socket <dedicated sock>`.
- Target is explicit attach / `seat new` / `seat open`. Do not switch by “most recently active”.
- `resident=true` after `grctl` itself loaded does **not** mean the TUI is online. Online = pager process on the GrokSeat socket **before** that load.
- Do not `session/load` a stale session with no pager (can block about 20 seconds).
- Enter an existing session with official `--resume <id>`.
- On Windows the TUI needs a real console (`CREATE_NEW_CONSOLE`). Hidden-window TUI exits immediately.
- `session/cancel` can stop the current turn.
- A known GrokSeat id remains `openable` after the window closes (`known_dormant`). Ordinary `--no-leader` sessions are not openable.
- `send` from ACP shows up live in the native TUI; typing in the TUI shows up as ACP `session/update`.

## Forbidden as the product path

- `grok -p` as fake sync
- Screenshots or injected keystrokes as the control path
- Session `updates.jsonl` as the main path
- Adopting the ordinary Grok leader / default socket
- Auto `session/load` of dormant catalog rows
- Exposing `grctl` on the public internet
- Treating `controllable=true` as the only “can open” condition
