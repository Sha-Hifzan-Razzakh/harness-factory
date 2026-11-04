# ADR 0018: Recorded corpora and their versioning

Status: proposed · 2026-11-04 · Requirements: ENV-01 (web search and fetch), KNW-01, MLO-04 (part)

## Context

ENV-01 lists "web search and fetch over a recorded corpus" among the environments a task can run in. KNW-01 says
corpora are versioned (with DVC, as MLO-04 says for data generally), indexed once per version, and that the index
id is recorded with every run. Until now the only corpus was a task's own `corpus/` folder, searched from the
harness side by `retrieve.search` (ADR 0017). That is a retrieval *dimension* of the harness: a recipe chooses to
give the model better search over files it could read anyway. It is not the web: a place outside the task with
pages the model can only reach through a search engine and a browser, where pages go stale, move, disappear and
contradict each other.

The constraints are the usual ones. Every run must be reproducible offline and in CI with no network (the
sandbox has none). A sandbox server imports only `mcp` and the standard library (ADR 0013 allows pinned
libraries, but nothing here needs one). Nothing may change an existing task, recipe or environment hash.

## Decision

1. **A `web` MCP server in the sandbox**, not a harness-side tool: the model calls `web.search(query, page)` and
   `web.fetch(url, offset)` by name, like any MCP tool, and only recipes that open the server get them
   (`v2-web` = `v2-base` + `web`). Both are READ, R0. Tasks list the web tools directly in `allowed_tools`, so a
   recipe without `web` doesn't reach them and campaigns leave the pair out (`provisioner.reaches`). The tools
   reveal information nothing else does, so they have no `granted_by`.
2. **Realistic, bounded behaviour.** `search` returns 5 ranked results per page, at most 5 pages, each with URL,
   title, snippet, fetched date and published date (when the page has one). `fetch` returns at most 6,000
   characters, cut at a line break, with `next_offset` to read on: well under the tool executor's 16,000-character
   inline cap, so a long page is usable whole and never truncated or spilled (ADR 0015). Redirects (301, 302,
   307, 308) are followed and reported; a recorded 404 or 410, or a path the site never had, is an HTTP error
   (`ToolError`); a host outside the snapshot can't be reached. URLs are normalised as a browser would (http and
   https alike, host case, trailing slash, fragment).
3. **Deterministic ranking, standard library only** (`env/mcp/impl/webindex.py`): BM25 (k1 1.2, b 0.75) over
   lower-case alphanumeric tokens with a small stopword list and plural-s stripping, the title counted twice,
   URL path words once. Scores are rounded before sorting and ties go to the URL in order. The same module
   builds the index (factory side) and ranks queries (server side), so both tokenise alike. Its parameters are
   `ENGINE`; `ENGINE_ID` (their hash) names the engine.
4. **A corpus is a folder in the catalog** (`catalog/corpora/<id>/`): `corpus.yaml` (id, title, description,
   redirects, gone) and one Markdown file per page with a YAML header (`url`, `title`, `fetched`, optional
   `published`). Loading validates it: normalised URLs, each URL once, redirect chains that end at a page or a
   recorded 404 and never loop.
5. **Versioning: content-addressed in the repository, not DVC, for now.** A corpus's version is the hash of what
   the server can serve: every page's URL, title, dates and text, every redirect and every recorded 404/410.
   File names, the description and other labels are not part of it, and line endings are normalised, so a
   Windows checkout has the same version as Linux. `hfx corpus freeze` writes the version and page count into
   `corpus.yaml` (the manifest); provisioning refuses a corpus whose files no longer match its manifest (as tool
   drift fails provisioning), and `hfx validate` and the authoring snapshot report it.
   *Why not DVC now:* the corpora that tasks need are small (tens of pages, kilobytes), CI and every offline gate
   must run with no remote and no credentials, and a DVC pointer in git says nothing about a corpus until it is
   pulled. Content addressing gives what KNW-01 asks of DVC — an immutable, verifiable version per corpus —
   without a remote.
   *The path to DVC* for a corpus too big for git (rough line: over 5 MB or 2,000 pages): track `pages/` with DVC
   (`dvc add catalog/corpora/<id>/pages`, committing `pages.dvc`, pages ignored by git), and run `dvc pull`
   before runs. Nothing else changes: the version is computed from the materialised pages, so the same corpus has
   the same version wherever it is stored, and a pulled corpus is checked against its manifest. A missing
   `pages/` with a `pages.dvc` next to it is refused with the `dvc pull` command to run. DVC-only corpora stay out
   of CI tasks, so the repository stays runnable offline.
6. **Indexed once per version.** The factory compiles a version (pages, redirects, 404s and the index) into
   `var/corpora/<id>/<index id>/corpus.json` on first use and reuses it after. The index id is the hash of the
   corpus id, version and engine id, so a new corpus version or a new engine is a new index, and an old one is
   never served by mistake. The file is written atomically (temporary file, rename), so concurrent runs and
   processes share it safely; there is no in-process cache (no module-level state a run writes to).
7. **Mounted read-only into the run's state folder, never the workspace.** At reset the sandbox copies the
   compiled file to `<state>/web/corpus.json` (`HF_WEB`), file mode 0444, folder 0555, and the web server only
   reads it. In Docker the folder is its own bind mount, `/state/web:ro`, inside the writable `/state` mount: a
   write fails in the container whatever the file modes. The folder exists for the sandbox's whole life (emptied
   for a task without a corpus), so the mount stays valid across resets. `run_args()` always has the mount, and
   `tests/test_docker_sandbox.py` fails without its `:ro`. The state diff never sees it (it isn't in the workspace,
   the database or the business state), and a task's workspace guard is unaffected.
8. **Identity.** A task names its corpus with a new optional seed field, `env_seed.corpus: <id>`, resolved in
   `corpora/` next to the task catalog's `tasks/` folder. The field is left out of the task hash while unset
   (`_SEED_OMIT_WHEN_NONE`), so every existing task keeps its hash. When set, the corpus *version* (not just its
   name) is part of the task hash: editing a page is a new version and a new task identity, like editing a seed.
   The ranking engine's id is part of `env_hash` for recipes that open `web` (`SERVER_ENGINES` in
   `env/mcp/servers.py`), and only for them, like pinned libraries (ADR 0013).
   *Why the corpus is not in `env_hash`* (the review's plan said it would be): `env_hash` is the identity of a
   recipe's tool environment for a model, computed without a task (the selector's evidence check, analysis,
   calibration's `tools_hash` pools). Putting a task's corpus in it would make one recipe's environment differ
   per task and break those uses. The corpus is task data, like the seed files, the database and the business
   state, and is versioned with the task; what ranks it is environment, and is versioned with the environment.
   Every run's key covers both.
9. **Recorded with every run.** The run snapshot's `web_corpus` holds the corpus id, version, index id, engine id,
   and page, redirect and 404 counts.
10. **Tasks and grading.** Two research tasks: `web-01` (DEV, find the current value: a vendor's rate limit, where
    the retired v1 page ranks first and the launch post has an older number) and `web-02` (TEST, reconcile
    sources: a ferry route whose start date the operator moved twice, and a newspaper article newer than the
    release it repeats). Both answer with the value and the URL: `answer` and `source` are `output_regex` checks
    anchored at `\A` that refuse every trap's wrong value; `grounded` is `tool_called web.fetch` with
    `touches: [url=...]` globs (scheme, trailing slash and fragment variants, and the redirect aliases listed), so
    an answer from search snippets, from a 404 or from another page keeps only the answer's weight; a zero-weight
    guard keeps the seeded `brief.md` untouched. A do-nothing run scores 0. The references take 3 calls.

## Consequences

- The web is a measurable environment: research tasks where finding the right page, judging its date and citing
  it is the work, reproducible on any machine with no network.
- A corpus edit is visible: a new version, a stale manifest until frozen, a new hash for every task that uses it.
- `touches` reads the call's arguments, not what came back (as for every tool): a fetch of a redirect alias counts
  only if the task lists the alias. Both tasks list theirs.
- The engine is lexical; it doesn't understand synonyms. That is deliberate (deterministic, offline, explainable),
  and part of the environment's identity: a better engine is a new `ENGINE` version and a new environment hash.
- Not built: DVC-tracked corpora (the path is above), recording a live site into a corpus (a crawler that writes
  this format would be a script under `scripts/`), and more tasks (the large task batch, #52).
