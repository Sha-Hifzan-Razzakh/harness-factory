# Dev container

Develop inside a container that sees only this project folder. Everything else it writes (Linux venv,
`web/node_modules`, uv cache, Claude Code's login and memory) lives in Docker volumes, not on your machine.

## Open it

VS Code: **Ctrl+Shift+P → Dev Containers: Reopen in Container**. The first build takes about 2 minutes: it pulls
the image, runs `uv sync --extra dev --extra mlops` and `npm install` in `web/`.

CLI alternative: `npx @devcontainers/cli up --workspace-folder .` then
`npx @devcontainers/cli exec --workspace-folder . bash`.

Claude Code is installed as a VS Code extension inside the container. Sign in once; the login is kept in the
`hf-claude-*` volume across rebuilds.

## What stays where

| Item | Inside the container | Host |
|---|---|---|
| Project folder | `/workspaces/harness-factory-latest` (bind mount, read-write) | the same files |
| Python venv | `/home/vscode/.venvs/main` (volume) | your Windows `.venv` is not touched |
| `web/node_modules` | volume over the folder | your Windows copy is hidden, not touched |
| Exports (reports, xlsx) | `var/exports/` (gitignored) | visible in the project folder |
| Claude scratch files | container `/tmp` | nothing |

No other host folder (Downloads, your user profile, `~/.ssh`) is mounted.

## Telemetry

Keep the stack running on the host: `docker compose up -d` from a Windows terminal. The container sends traces
to `host.docker.internal:14318` and MLflow to `host.docker.internal:15000`. Those values are set in
`devcontainer.json` and override `.env`'s `localhost` URLs. If you change `HF_OTLP_HTTP_PORT` or
`HF_MLFLOW_PORT` in `.env`, change them there too. Phoenix stays at http://localhost:16006 in your browser.

## Signed commits (one-time host setup)

`main` needs signed commits. Your private key stays on Windows; the container signs through VS Code's forwarded
ssh-agent, using the copy of your **public** key in `.devcontainer/local/signing.pub`. The Windows agent is
currently disabled. Enable it once in an **administrator** PowerShell:

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
ssh-add $env:USERPROFILE\.ssh\id_ed25519
```

Then rebuild or reopen the container. `ssh-add -l` inside it should list the key. Until then, commit from Windows.
Git identity is unchanged: VS Code copies your global `~/.gitconfig` in.

`.devcontainer/local/` (public key, allowed signers, Claude memory seed) is per-machine and gitignored.

## Known limits

- **Existing worktrees don't open here.** Their `.git` files point at Windows paths
  (`gitdir: C:/Users/...`). Use them from Windows, or create new worktrees inside the container. Those, in turn,
  won't open on Windows. Don't run `git worktree repair` from either side: it rewrites the paths for the other.
- **Older runs' traces.** The results store keeps absolute Windows paths (`trace_path`, `transcript_path`) for
  runs made on Windows. Commands that open those files (regrades, trace analysis) won't find them from here.
  Runs made in the container store Linux paths, which Windows tools won't find either.
- **Docker sandboxes (`HF_SANDBOX=docker`)** aren't available here: the host's Docker socket isn't mounted (that
  would give the container control of your Docker). The container uses `HF_SANDBOX=local`, the same isolation CI
  uses for the P0 gate. Run Docker-sandbox smoke tests from Windows with `docker compose run --rm factory-docker ...`.
- **File modes.** Windows has no executable bit, so the setup script sets modes from git's index (ruff's EXE
  rules depend on them). Git ignores modes here (`core.filemode=false`), so this never shows up as a change.
