# GrokSeat (`grctl`)

GrokSeat is an unofficial, local control layer for native Grok CLI sessions. Its
command-line tool, `grctl`, starts a dedicated Grok CLI leader, opens native TUI
windows, selects a session explicitly, and sends prompts to that selected session.

The project solves a narrow problem: controlling the same native Grok session
from scripts without simulating keyboard input or confusing it with ordinary
Grok CLI windows.

> [!WARNING]
> If `grctl send` succeeds, the text becomes a real prompt to the local Grok
> agent. Treat the command as if you typed directly into the TUI.

## Scope and boundaries

This repository contains only the local `grctl` CLI control layer and supporting
documentation/examples. It intentionally does **not** contain:

- phone Bot wiring;
- a complete Web Dashboard;
- a Relay service; or
- a multi-machine remote-control system.

It is not a complete mobile Bot project. Do not expose `grctl` directly to the
internet. A separate, security-reviewed integration would be required to place
any remote interface in front of it.

## How it works

```text
scripts/grctl.cmd or scripts/grctl.ps1
                 |
                 v
          grctl local CLI
                 |
          ACP over local stdio
                 |
                 v
    dedicated GrokSeat leader socket
        |                       |
        v                       v
 native Grok TUI         selected session
 (visible console)       state on this PC
```

GrokSeat uses its own leader socket so it does not adopt or control ordinary
Grok windows started with `grok.exe --no-leader`.

| Mode | How it starts | Controlled by `grctl` |
|---|---|---|
| Ordinary Grok | `grok.exe --no-leader` | No; never load, stop, or resume it |
| GrokSeat | dedicated leader socket and `--leader` | Yes, after explicit selection |

See [docs/architecture.md](docs/architecture.md) for the detailed process model.

## Requirements

### Windows

- Windows 10 or 11.
- PowerShell and Windows process-management APIs.
- A console-capable desktop session: the native TUI is opened in a real console
  window.

This implementation is Windows-specific. Other operating systems are not
currently supported or tested.

### Python

- Python 3.10 or newer available as `python` or via the Windows `py -3` launcher.
- No third-party Python packages are required; the code uses the standard
  library.

### Grok CLI

- Grok CLI installed and already authenticated locally.
- The executable at `%USERPROFILE%\.grok\bin\grok.exe`, or `GROK_BIN` set to
  the executable path.
- A compatible Grok CLI build. The project has been verified with Grok CLI
  1.0.x; other versions may change internal commands or ACP behavior.

The default dedicated socket is
`%USERPROFILE%\.grok\leader-grokseat.sock`. Override it with
`GROKSEAT_SOCKET`. Local state defaults to
`%USERPROFILE%\.grok\grctl\state.json`; override its directory with
`GRCTL_HOME`.

## Quick start

Run these commands from the repository root in Command Prompt:

```bat
scripts\grctl.cmd up --json
scripts\grctl.cmd seat new --cwd C:\work\demo --mkdir --json
scripts\grctl.cmd send "Summarize this project" --json
scripts\grctl.cmd status --json
scripts\grctl.cmd down --json
```

PowerShell users can replace `scripts\grctl.cmd` with
`scripts\grctl.ps1`. You can also run `python src\grctl.py ...` directly.

To send a longer prompt without shell quoting issues:

```bat
scripts\grctl.cmd send --file prompt.txt --json
```

## Common commands

```text
grctl up [--attach-last]             Start the dedicated leader
grctl down [--keep-leader]           Stop the GrokSeat TUI/leader
grctl seat new --cwd DIR [--mkdir]   Create and select a seat
grctl seat open ID                   Reopen a known dormant seat
grctl seat close                     Close the current TUI window
grctl seats --json                   List online and reopenable seats
grctl sessions --json                Show the detailed session catalog
grctl attach ID                      Select an online GrokSeat session
grctl current --json                 Show the selected session ID
grctl status [ID] --json             Show session status
grctl history [ID] --json            Read session history
grctl send TEXT                      Send a prompt and wait for completion
grctl send --file PATH               Send prompt text from a file
grctl result --json                  Show the last locally stored result
grctl watch [--timeout SEC]          Stream session updates
grctl stop                           Cancel the current turn
grctl detach                         Clear the local selection
```

All command forms and exit codes are documented in
[docs/commands.md](docs/commands.md).

## Session model

A **seat** is a Grok session known to the dedicated GrokSeat leader. `grctl`
keeps one explicit local target in its state file:

- `seat new`, `seat open`, and `attach` select the target;
- `send`, `watch`, `stop`, and related commands act only on that target;
- `detach` clears the target;
- no command automatically follows the most recently active ordinary session;
- `controllable=true` means its GrokSeat TUI is online now;
- `openable=true` also includes a known seat whose TUI window is closed.

Use `seats --json` for the concise list. A dormant seat with
`openable=true` can be resumed with `seat open ID`.

## Repository layout

```text
ForGit/
|-- src/                  Python implementation
|   |-- grctl.py          CLI entry point and command handling
|   |-- seat.py           Windows process and GrokSeat lifecycle helpers
|   |-- acpclient.py      ACP stdio client
|   `-- store.py          Local target/state persistence
|-- scripts/              Windows command wrappers
|-- docs/                 Architecture, commands, and operating notes
|-- examples/             Fictional JSON output examples
|-- LICENSE               MIT license
`-- README.md
```

## Safety and security

- Keep the leader socket and `grctl` local to a trusted Windows account.
- Never expose this CLI or its state through an unauthenticated network service.
- `grctl` runs with the permissions of the Windows account that launches it. It
  can inspect local Grok process command lines, start or stop the dedicated
  GrokSeat processes, and write runtime state and ACP logs under `GRCTL_HOME`.
- Review prompt files before sending them. Prompts can cause the agent to use
  tools and modify local data according to Grok CLI permissions.
- By default, an ACP tool permission request is **not** automatically approved.
  When `grctl` has an interactive terminal, it asks there for an explicit
  one-time approval. It does not delegate that prompt to the separate native
  TUI. In non-interactive use, the request is rejected (or cancelled if the
  agent offers no reject option).
- Setting `GRCTL_ALWAYS_APPROVE=1` is an explicit opt-in that passes
  `--always-approve` to Grok CLI and automatically selects an offered
  `allow_always` permission (falling back to `allow_once`). This substantially
  weakens the confirmation boundary and should be used only in a controlled
  environment.
- The ACP client advertises `readTextFile=false` and `writeTextFile=false`.
  A compatibility handler for an unexpected `fs/read_text_file` request remains
  available, but it rejects paths outside the active session working directory,
  including resolved `..` and symlink escapes. It never implements ACP file
  writes. Separately, `grctl send --file PATH` reads the path explicitly supplied
  by the local user and is not an agent-initiated file request.
- Do not commit local state, logs, credentials, real configuration files, or
  session identifiers. The supplied `.gitignore` excludes common variants.
- Protect Grok authentication tokens and other credentials using normal Windows
  account and filesystem controls. This project does not provide a credential
  vault or claim to sandbox the Grok CLI process itself.
- Files under `examples/` contain fictional session IDs, paths, and timestamps;
  replace them only with similarly fictional data.
- `grctl` deliberately refuses to control ordinary `--no-leader` Grok windows.

## Limitations

- Windows-only and verified only with Grok CLI 1.0.x.
- One GrokSeat TUI is expected per dedicated leader at a time.
- This is an unofficial integration and depends on Grok CLI process/ACP
  behavior that may change.
- Session discovery distinguishes live GrokSeat windows from known dormant
  seats; arbitrary ordinary Grok sessions are not adoptable.
- There is no phone integration, complete Web Dashboard, Relay, hosted service,
  or multi-machine orchestration in this repository.

## License

Released under the [MIT License](LICENSE).
