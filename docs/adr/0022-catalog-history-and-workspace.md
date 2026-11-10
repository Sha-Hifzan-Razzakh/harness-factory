# ADR 0022: Catalog history, spec cards and the web workspace

Status: accepted · 2026-11-10 · builds on ADR 0009 (authoring layer)

## Context

Authoring (ADR 0009) writes catalog files safely but keeps no record of how they got there: who changed a skill,
why, what it said before, and which runs were made with which version. Git answers some of that only after a
commit, and most catalog edits are experiments that are never committed. Building an experiment also meant
reading recipes, tasks, providers and tool specs separately before knowing what a run would actually see.

## Decision

1. **A version history next to the files, not instead of them.** `authoring/versions.py` keeps an append-only
   journal (`journal.jsonl`) and content-addressed blobs under `settings.HISTORY_DIR` (default
   `var/catalog_history/`, git-ignored). Every change made through `AuthoringService` (create, update, archive,
   restore) appends one entry: author, time, note, the item's files before and after, its hash before and after,
   and the recipes whose identity moved. Files stay the source of truth and nothing is committed.
2. **Changes made elsewhere are found, not lost.** `AuthoringService.sync()` compares the catalog with the last
   recorded version of every item and records differences as `external` entries (an editor, `git pull`). An item
   seen for the first time gets a `baseline` entry.
3. **Delete archives.** The item's files leave the catalog, its history stays, and `restore` brings back any
   version. `purge` drops an archived item's history for good.
4. **Every history write is an authoring write.** Edit-as-text (`update_parts`) and `restore` build file
   operations and go through the same lock, staging, validation, impact and confirm steps as any change. The
   history is recorded only after the files are written.
5. **Models are an authoring kind; tasks are read-only.** A model entry of `catalog/providers.yaml` is edited in
   place as text (`authoring/providers_edit.py`), so the file's hand-written comments survive. A change to a
   provider model id moves the `env_hash` of every run on that model, so it needs confirm like any identity
   change. Tasks are versioned and shown but never written by authoring: a task needs a reference solution, trap
   scripts and a do-nothing check (CLAUDE.md).
6. **Spec cards read the loaded catalog and run nothing.** `cards/build.py` describes a model, harness, task, tool
   or fault, and the final card of one experiment (task x harness x model x provider x seed x fault): its RunKey,
   the exact tools and prompts the model will see, validation, a cost estimate, earlier results and a READY or
   BLOCKED verdict.
7. **Experiments are saved by RunKey.** `api/experiments.py` saves a named campaign with its question and the exact
   RunKeys it planned under `settings.EXPERIMENTS_DIR`. Campaigns still live in memory; an experiment finds its
   runs in the results store by key after a restart.
8. **The React workspace is another API client.** `web/` (Vite, React, TypeScript) calls only the API, like the
   Streamlit UI. `hfx web` builds it once into `src/harness_factory/web_dist` (git-ignored, needs Node and npm)
   and serves it from the API at `/app`, so running it needs no Node. CI type-checks and builds it.

## Consequences

- No hash, task, scoring or results-store change. History and experiments live under `var/`, outside every
  identity.
- The history is per machine (it lives in `var/`). Two people editing the same catalog each have their own
  history; git remains the shared record.
- `hfx web` starts with `sync()`, so the first start records a `baseline` entry for every item.
- The container image has no Node, so `hfx web` there needs a built `web_dist` first.

## Alternatives

- **Commit every change to git:** gives a shared history, but turns every experiment into a commit and fights
  ADR 0009's "authoring writes files, the person commits".
- **Store versions in the results database:** one store for everything, but the results store changes only by
  Alembic revisions and is about runs, not catalog files.
