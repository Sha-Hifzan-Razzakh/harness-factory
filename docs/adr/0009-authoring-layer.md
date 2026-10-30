# ADR 0009: Authoring layer behind the CLI, the API and the UI

Status: accepted · 2026-10-30 · PRs: #29 (core + CLI), #30 (API), #31 (API campaigns), #32 (UI)

## Context

Adding a prompt, a skill, a matrix recipe, a tool server or a fault profile meant editing files by hand and then
running `hfx validate`, `hfx tools freeze` and a smoke run to find out what broke. Each kind has rules that live
only in CLAUDE.md (new capability on a new server, `ToolError` not `ValueError`, stdlib-only server modules, the
model slot left empty, one dimension per matrix recipe), and editing a shared item (a base recipe, a skill, a
prompt) silently changes the identity of every recipe that uses it. Experiments should be quick to set up from a
terminal, an HTTP client or a browser, without weakening any of those rules.

## Decision

1. **One service, three front doors.** `src/harness_factory/authoring/` is the only code that writes catalog
   items. `hfx new` / `hfx catalog` (this PR), the API and the UI all call it. Files stay the source of truth and
   git the history: authoring writes files and never commits.
2. **Staged and checked by the factory itself.** A change becomes a list of file operations, applied to a
   temporary copy of `catalog/` and `prompts/`. Both catalogs are then read by `authoring/snapshot.py` with the
   factory's own loaders and `validate_recipe`, so authoring can't drift from `hfx validate`. Any load error or
   recipe issue in the staged catalog that the current one doesn't have rejects the change. Nothing is written.
3. **In a child process.** The snapshot reads whatever `settings` points at. Re-pointing `settings` inside a
   process that may be running a campaign would be module-level mutable state (CLAUDE.md, "Concurrency"), so the
   snapshot runs as `python -m harness_factory.authoring.snapshot` with `HF_ROOT` set to the catalog to read. The
   current catalog's snapshot is cached until a file under `catalog/`, `prompts/` or the package changes.
4. **Impact before writing.** The two snapshots give the recipes whose hash changes, the recipes added or removed,
   the servers whose environment hash changes (and which recipes open them), and how many stored runs belong to
   the old identities. A change that touches an existing identity is written only with `confirm` (`--yes`). A new
   item never does: a new server is outside every existing recipe's `env_hash`.
5. **Kind rules.**
   - Prompts: Jinja syntax, and only the variables the layer is rendered with (`system`: tools, recipe; `task`: task;
     `stage`: task, stage, tools; `skills`: skills), since templates render with `StrictUndefined`.
   - Recipes: the model slot stays empty; a matrix recipe must change something and is flagged when it changes more
     than one dimension.
   - Local tool servers: a new capability is a new server. The module (pasted, or a generated stub) must import only
     `mcp` and the standard library, never raise `ValueError`, define `app = MCPServer(...)` and end in `app.run()`,
     and pass the repository's ruff rules. It is then started as a stdio server in a scratch workspace and its tools
     listed. Declared and served tools must match exactly, and the schema hashes written to `tools.yaml` come from
     that discovery (the same hash `hfx tools freeze` writes).
   - Remote servers: `auth_env` is the name of a variable, never a token; a tool that changes state needs a tenant
     (the catalog's own rule). Schema hashes wait for `hfx tools freeze` with the remote opt-in.
   - The built-in servers (`fs`, `sql`, `search`, `skills`) are code the factory depends on: edited in the repo.
   - Deleting is refused while anything uses the item: a recipe (prompt, skill, base, delegate, server), a task's
     allowance, another tool's `granted_by`, or `selector.yaml`.
6. **All or nothing, one at a time.** Writes are atomic per file, a failure restores every file already written,
   and a lock file (`var/authoring.lock`, O_EXCL so it works on every OS) serialises changes from the CLI and the
   API. The current snapshot is taken under the same lock as the write.

## Consequences

- No hash, task or scoring rule changes: authoring only writes files the catalog already allows.
- A write costs one or two snapshot processes (a few seconds). Fine for authoring, and the reason the snapshot is
  cached.
- Pasted tool code is executed during discovery, on the machine running authoring. The API that exposes this binds
  to localhost unless a token is set (#30).
- Tasks are not authored here: a task also needs a reference solution, a scripted wrong answer per trap and a
  do-nothing check (CLAUDE.md), which a form can't check. That stays a repository change.

## Alternatives

- **Point `settings` at the staged copy in-process:** simplest, but unsafe next to a running campaign.
- **Give every loader and validator explicit paths:** cleaner long-term, but touches files other PRs were changing
  at the same time (#28, #34). The child process gets the same result without editing them.
- **Let the UI write files directly:** three implementations of the same rules, guaranteed to drift.
