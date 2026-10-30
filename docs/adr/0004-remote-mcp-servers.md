# ADR 0004: Remote MCP servers

Status: accepted · 2026-10-30 · Requirements: MCP-03, EXT-05, EXT-07, EXT-13 (EXT-06 met by design, see 2)

## Context

MCP-03 asks for streamable HTTP alongside stdio, and EXT-05 for connecting an existing remote server: a third-party
service or an organisation's MCP gateway, i.e. capabilities other teams and agents already expose. Until now the
schema accepted `transport: streamable_http` but the client only spoke stdio. Remote servers threaten three things
every run relies on: a clean starting state, no network in the sandbox, and grading on what the run changed.

## Decision

1. **Declared like local servers.** `catalog/tools.yaml` gets `url`, a pinned `version` and `auth_env` per remote
   server. Tool schemas are frozen as for local servers (drift fails provisioning).
2. **The client connects, not the sandbox.** MCP clients run in the factory process, so a remote server is reached
   from there and the sandbox keeps `--network none`. This is stronger than EXT-06 as first written (egress from the
   sandbox to declared hosts only): the sandbox needs no egress at all.
3. **Read-only until test tenants exist** (EXT-07). A remote side effect can't be reset before a run or read by a
   grader, so a remote tool that isn't `READ` is refused when the catalog loads.
4. **Identity.** `env_hash` includes `url@version` for every remote server a recipe opens, only when one is opened,
   so local-only hashes are unchanged. A new server version is a new experiment.
5. **Opt-in and marked** (ENV-02). Provisioning refuses a recipe that opens a remote server unless
   `HF_ALLOW_REMOTE=1`, and records `snapshot.remote_servers`. `hfx tools freeze` checks remote servers only with the
   opt-in and keeps their declared hashes otherwise, so CI never needs the network.
6. **Credentials** (EXT-13). The token is read from the named environment variable when the session opens and goes
   only into that client's HTTP headers: never into a trace, a transcript, a snapshot or a sandbox. A missing variable
   fails provisioning naming it.
7. **Failures belong to the environment.** Unreachable or unauthorised at the start: a provisioning error with the
   reason. Failing mid-run: that tool call returns an error the model sees, its span is marked `infra`, and the
   trajectory grader excludes it from Q, like an injected fault. Each remote session lives in its own owner task:
   the HTTP transport's task group would otherwise cancel the run when the server disappears. A session that can't
   be closed cleanly (the server is gone) is recorded on the client, not raised.

## Consequences

- Tests run a stand-in server (`tests/remote_kb_server.py`) on 127.0.0.1 behind a bearer token: live call, token kept
  out of records, opt-in, missing and wrong token, mid-run failure, cancellation while opening, freeze skipping.
- Not yet: test tenants (remote side effects), record and replay of remote answers (EXT-08), external agents as
  tools (EXT-10). Record and replay is the next step, so live runs can be repeated offline.
