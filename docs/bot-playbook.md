# Bot playbook

`grctl` is a thin pipe. The Bot is not a second agent.

- **Management** (one line, then stop): kitchen up/down, list seats, open/close a seat.
- **Ask the Seat**: paste the user’s question **verbatim** into `grctl send --file`. Do not rewrite, do not add a system prompt, do not summarize first.

## List and open

```text
grctl seats --json
```

Open only if `openable` is true (see `sessions --json` too).
`controllable=false` + `openable=true` + `reason=known_dormant` means: window closed, still a GrokSeat conversation — use `seat open`, do not say “nothing to open”.

## Wake / clock out

```text
grctl up --attach-last --json
grctl down --json
```

`up` without `--attach-last` starts the kitchen only, unless `current` is already online.

## New project folder

Always an **absolute** path:

```text
grctl seat new --cwd C:\proj\demo --mkdir --json
```

## Open an old seat

```text
grctl seat open sess-old --json
```

This `--resume`s the TUI first, then attach. Do not `session/load` a pager-less id.

## Send / stop

```text
grctl send --file prompt.txt --json
grctl stop --json
```

`send` waits until the turn ends. No Target → exit 2. Leader down → exit 3.

## Close the window, keep the kitchen

```text
grctl seat close --json
```
