# ADR 0021: Degree audit server: a pinned engine and read-only reference data

Status: proposed · 2026-11-09 · Requirements: HF-1 (DegreeAudit in the lab), ENV-01 (task environments)

## Context

DegreeAudit is a degree audit engine (`degree_audit_core`) with a frozen, hand-written dataset (release
2026.11.0: 248 synthetic student records, the policy documents and an expected answer per record). The lab should
measure how well a harness audits a student with that engine's tools: read the SIS, call the engine, check the
approval documents and write the audit. HF-1 is the first step: the tools, as an MCP server in the sandbox.

The engine needs three things at run time. The SIS: the run's task database (`HF_DB`), seeded from DegreeAudit's
SQL dump, which the `sql` server can also query. The compiled rules (`reference.json`: study plans, courses,
relations, policies): about 130 KB of JSON that is the same for every task of one dataset release. And the engine
code itself, which isn't on PyPI.

The constraints are the usual ones. A sandbox server imports only `mcp`, the standard library and libraries the
allow-list pins (ADR 0013). A server reads only the run's state; nothing outside it. Nothing may change an existing
task, recipe or environment hash.

## Decision

1. **An `audit` MCP server** (`env/mcp/impl/audit_server.py`) with the engine's three tools: `validate_record`,
   `evaluate`, `explain`. All READ, R0, no `granted_by` (they reveal what no other tool computes). The names, the
   descriptions (`degree_audit_core.descriptions`) and the refusal codes are the core's own contracts C01–C03, so
   the agent sees the tools a DegreeAudit service would offer. The server only adapts: it opens `HF_DB` read-only
   through the core's `SqliteSis`, passes the reference, and turns a core refusal into a `ToolError` that starts
   with the code (`UNKNOWN_STUDENT: S20299999 is not in the SIS`). `mode` is an enum in the schema, so a wrong
   mode is an invalid argument, not a refusal.
2. **The engine is pinned by commit.** `deps.Library` gains an optional `source`: a direct reference,
   `git+https://...@<commit>`, for a library that isn't on PyPI. `degree-audit` 0.1.0 is pinned to the commit of
   the tag `core-v0.1.0` (a commit, because a tag can move). The pin is in the `docs` extra, the extra that
   installs the allow-list, and in `uv.lock`; `pyproject.toml` allows direct references for it. `deps.problems`
   also checks the installed commit (PEP 610 `direct_url.json`), so a core installed from anywhere else is refused
   like a wrong version. The pin is in the `env_hash` of every run that opens `audit` (as any server library), so a
   new core is a new experiment. The image vendors a git-sourced library in its own step, built from that commit
   with `--no-deps`: the engine is stdlib-only and has no dependencies.
3. **Reference data is mounted read-only, like a web corpus (ADR 0018).** A new seed field,
   `env_seed.reference: <file>`, names one file relative to the task folder. Each reset copies it, read-only, to
   `reference/reference.json` in the state folder (never the workspace); `HF_REFERENCE` names it; Docker mounts the
   folder `:ro`. The folder is kept across resets, so the bind mount stays valid. The file is part of the task's
   seed hash. The field is in `_SEED_OMIT_WHEN_NONE`, so every existing task keeps its hash. A task without it
   gets an empty folder, and the audit server says that no reference data is mounted.
4. **The dataset is vendored, byte for byte, by release.** `catalog/seeds/degree-audit-2026.11.0/` holds the
   frozen `reference.json` and `SOURCE.yaml` (repository, tags, commit, bundle hash, file hashes). A
   `.gitattributes` there turns off line-ending conversion. A new DegreeAudit release is a new folder, never an
   edit. HF-3 adds the SIS dump and the expected answers there when it adds the tasks.
5. **Conformance uses DegreeAudit's own cases.** `catalog/conformance/audit.yaml` is `conformance/cases.yaml` at
   `core-v0.1.0`: 7 happy calls and 11 refusals, plus the invalid mode. Its SIS (`seed/audit/sis.sql`) is what
   DegreeAudit's compiler builds for the three D1 records the happy calls use and that file's two extra records.

## Consequences

- Runs that don't open `audit` are unchanged: their tool environment, `env_hash` and task hashes are the same.
- The sandbox image tag changes once, since the allow-list it vendors grew (`image_tag` hashes it). The image build
  needs network access to GitHub for the core, as it already needs PyPI.
- Installing the `docs` extra (`uv sync --extra docs`) fetches the core from GitHub. While the DegreeAudit
  repository is private, that needs credentials for it, in CI too. If that is a problem the repository (or the
  core alone) can be made public, or a built wheel vendored with its sha256; only the `source` changes.
- The largest result over the whole 2026.11.0 dataset is 7,395 characters (`evaluate`, indented JSON), under the
  tool executor's 16,000-character inline cap, so nothing spills.
- The agent can also read the SIS with `sql` and the documents with `fs`. That is the point: the audit tasks
  (HF-3) grade whether it checks the engine's reading of approvals against the documents.
