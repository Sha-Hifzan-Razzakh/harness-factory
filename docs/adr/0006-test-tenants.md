# ADR 0006: Test tenants for remote side effects

Status: accepted · 2026-10-30 · Requirements: EXT-07 (builds on ADR 0004 and its record/replay addendum)

## Context

Local servers change files and a database in the run's sandbox, which is reset before the run and read by the
graders after it. A remote server's changes land in a service the factory doesn't own, so until now remote tools
had to be `READ`. That leaves out most of the real work an agent is asked to do against a company's systems: refund
an order, update a ticket, file a request. EXT-07 asks for remote side effects in a test tenant the factory can
reset and grade.

## Decision

1. **The server owns the tenants; the factory names them.** A remote server declares `tenant: {header, reset,
   export, drop}`. Each run gets a fresh id (`hf-` + 16 hex), sent as a request header on the run's session only.
   The model can't set or see the id, so it can't reach another run's tenant. The server keeps one state per id.
2. **Three admin tools, never the model's.** `reset(state)` sets the tenant to the task's seed, `export()` returns its
   state as JSON, and `drop()` forgets it. The client removes them from discovery (so drift checks and the model's
   tool list never include them), and the catalog refuses a tool declared under one of their names.
3. **Seeded by the task.** The seed is `env_seed.mock_api_state[<server>]`, a field that already existed and was
   unused. No task's hash changes. A server without a seed starts from `{}`.
4. **Reset at provisioning, export before grading, drop at close.** A failed reset is a provisioning error. The
   export becomes `GradeContext.remote` and `snapshot.tenant_state`. If it can't be read (server gone, error, not
   JSON, replay divergence), the run is `INCOMPLETE`: the environment failed, the run is not scored and is excluded
   from cell means. The drop runs before the session closes, best effort with a 10 s limit.
5. **Graded with a new check kind.** `remote_equals` has `path: "<server>:<JSON pointer>"` and `text`, compared as a
   string or as JSON. It reuses the existing check fields, so no task hash changes. Guards ("leave the others alone")
   are zero-weight critical `remote_equals` checks, as file guards are, so a do-nothing run still scores 0.
6. **Non-READ remote tools need a tenant.** The catalog rule is now "a tool that isn't `READ` needs `tenant:`", not
   "`READ` only". `tenant:` on a stdio server is refused: a local server is reset with its sandbox.
7. **Replay stays honest.** Recordings now key the n-th repeat of a call apart (n = 0 keeps the old key, so existing
   recordings still load; a READ call asked more often than recorded falls back to the first answer). The export's
   recording keeps the ordered keys of the calls that changed state before it. A replay that changed state
   differently gets `replay_diverged` instead of the recorded end state, so skipping the work can't earn the live
   run's score.

## Consequences

- Identity: the tenant spec isn't part of `env_hash`. It changes how the environment is reset, not what the model
  can do, and `url@version` already pins the server. Tasks with `mock_api_state` seeds hash as they always did.
- The factory trusts the server to isolate tenants and to export truthfully. The stand-in
  (`tests/remote_tenant_server.py`) shows the contract in about 80 lines. A real service needs a sandbox environment
  that implements it, which is the service owner's job.
- No remote-tenant task is in the catalog: there is no shared server to point at. The tests cover reset, isolation
  between concurrent runs, guards, drop on close, an unreadable tenant, and replay (repeats and divergence).
