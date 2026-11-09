# ADR 0021: Catalog add-ons and read-only reference data

Status: proposed · 2026-11-09 · Requirements: ENV-01 (task environments), EXT (extensibility)

## Context

Everything the factory runs comes from `catalog/` in this repository: servers, tasks, recipes, conformance
fixtures. Some catalogs shouldn't live here. A team may want to measure harnesses on its own tools and data
without publishing them, or keep a catalog versioned with the project it belongs to. Today the only way is a fork.

Such a catalog usually brings a server with its own library (often not on PyPI) and data the server reads that is
the same for many tasks: rules, a schema, a lookup table. The data isn't the workspace (the agent must not edit it)
and isn't the task database.

The usual constraints hold. Nothing may change an existing task, recipe or environment hash. A server imports only
`mcp`, the standard library and pinned libraries (ADR 0013). A server reads only the run's state.

## Decision

1. **`HF_ADDONS` names add-on folders** (os.pathsep-separated; environment or `.env`). An add-on has `addon.yaml`
   and a `catalog/` with the built-in layout: `tools.yaml`, `tasks/`, `recipes/`, `conformance/`.
   `harness_factory/addons.py` loads them; the existing loaders add their content where they read the built-in
   catalog: `load_tool_catalog(settings.TOOLS_FILE)`, `TaskRegistry()`, `load_recipes(settings.RECIPES_DIR)`,
   `conformance.fixture_path` and `stale_fixtures`. Its `prompts/` folder joins the built-in one in
   `PromptRegistry()` and `default_prompt_sources()`, searched after it. A loader given another root or file sees
   no add-ons.
2. **An add-on only adds.** A server id that is already declared, a task, recipe or prompt ref that repeats (a
   shadowing template would change a recipe silently), a stdio server
   whose module `addon.yaml` doesn't list, a module inside `harness_factory`, or a library pin the built-in
   allow-list already holds is refused, as are two add-ons that share an id, a module or a library.
3. **Libraries follow ADR 0013.** `addon.yaml` lists its libraries and which server imports which. They join the
   allow-list for those servers only (`deps.library`, `deps.server_libraries`): same pins, same `env_hash` rule,
   same check before a run. `Library` gains an optional `source`, a git direct reference pinned by commit (never a
   branch or a tag, which can move), for a library that isn't on PyPI; `deps.problems` checks the installed commit
   (PEP 610 `direct_url.json`). The `docs` extra and the sandbox image stay built-in only.
4. **Add-on servers run in the local sandbox.** Their `python_path` is put on `PYTHONPATH` for that server's
   process only (`addons.server_env`), from both launchers. The Docker sandbox refuses them with a setup error: its
   image vendors only the built-in servers and their libraries.
5. **`hfx tools freeze` writes each add-on's own `tools.yaml`.** It now rewrites only files where a tool changed,
   so the repository's file isn't touched when only an add-on moved.
6. **`env_seed.reference: <file>`** mounts one read-only file for a task's servers, like a web corpus (ADR 0018):
   each reset copies it to `reference/reference.json` in the state folder, never the workspace; `HF_REFERENCE`
   names it; Docker mounts the folder `:ro`; the folder is kept across resets so the bind mount stays valid. The
   file is part of the seed hash. The field is in `_SEED_OMIT_WHEN_NONE`, so existing tasks keep their hashes. It
   is generic: built-in tasks can use it too.

## Consequences

- With `HF_ADDONS` unset (CI, every existing setup) the factory behaves exactly as before. CI never needs access
  to an add-on, so a private add-on stays private.
- An add-on's tests and checks run where the add-on lives. `hfx validate`, `hfx conformance --server <id>`,
  `hfx reference <task>` and `hfx tools freeze --check` all see it once `HF_ADDONS` is set.
- Results don't record which add-on a task came from. Identity is the content hashes, as for built-in tasks, so
  two add-ons with identical content measure the same experiment.
- A recipe's hash holds the source of each template it names, wherever the file lives, so an add-on prompt edit
  is a new recipe hash and built-in recipes keep theirs.
- Not covered yet: add-on skills, fault profiles and corpora, and add-on servers in Docker (vendoring
  add-on modules and libraries into a per-add-on image). They can come later without changing this format.
