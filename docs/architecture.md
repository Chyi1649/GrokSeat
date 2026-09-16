# Architecture

GrokSeat is a **kitchen** (one leader socket) plus **windows** (native TUI processes). `grctl` is a short-lived ACP stdio client on that kitchen.

```
ordinary Grok window
  grok.exe --no-leader
  isolated; never a grctl Target

GrokSeat kitchen
  grok.exe agent [--always-approve] --leader-socket <sock> leader --no-exit-on-disconnect --no-auto-update

GrokSeat window
  grok.exe --leader --leader-socket <sock> --cwd <abs> [--resume <id>]
  real console (CREATE_NEW_CONSOLE on Windows). Splash screen is not “in session”.

grctl
  grok.exe agent [--always-approve] --leader --leader-socket <sock> stdio
  session/load only after Target is an online GrokSeat TUI (or after TUI --resume for a known id)
```

`<sock>` defaults to `%USERPROFILE%\.grok\leader-grokseat.sock`.

Target lives only in local state (`%USERPROFILE%\.grok\grctl\state.json`, gitignored). Writers: `attach`, `seat new`, `seat open`. `up --attach-last` uses `last`, not “most recently active”.

Online = that session is `resident` on the kitchen **and** a GrokSeat pager process exists. After `grctl` itself loads, `resident=true` does **not** mean the TUI is still there.

Do not `session/load` a stale id with no pager (can block ~20s). `seat open` starts the TUI with official `--resume` first, then attach.

One GrokSeat TUI per kitchen. Opening another id stops the current pager first.

Proxy: inherit `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` if the user set them. `grctl` does not invent a proxy.
