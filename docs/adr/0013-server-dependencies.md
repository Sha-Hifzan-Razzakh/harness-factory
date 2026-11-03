# ADR 0013: MCP servers with dependencies beyond the standard library

Status: accepted · 2026-11-02 · Requirements: ENV-01 (documents part) · PR #47

## Context

Tasks can't involve office documents today: no tool reads a `.docx`, `.xlsx` or `.pdf`, and no check can grade
one. Parsing them well needs libraries (python-docx, openpyxl, pypdf). The rule so far was that a server module
"may import only `mcp` and the standard library", because a server runs inside the sandbox, which has no network,
so whatever it imports must already be in the sandbox image, and the image is built without network inside the
build (the host vendors `mcp`'s closure and the Dockerfile only copies it in; see `env/sandbox_image.py`).

Three things must hold whatever we choose:

1. **Same experiment, same code.** A library upgrade can change what a tool returns (pypdf's text extraction
   changes between releases). Results from two versions must not pool into one cell unnoticed.
2. **Local and Docker sandboxes behave the same.** A local sandbox runs servers with the factory's own Python; a
   Docker one runs them from the image.
3. **A missing library is a setup error, never a scored run.** A server that dies during the MCP handshake, or a
   grader that can't open the file, must not turn into a FAIL that lands in a cell mean.

Options considered:

- **Stdlib-only parsers.** docx and xlsx are zipped XML and could be read with `zipfile` and `xml.etree`, but
  editing them while keeping styles, and extracting PDF text (compressed streams, font encodings), is a library's
  worth of work with worse fidelity. Rejected.
- **A `RUN pip install` in `sandbox.Dockerfile`.** Simple, but the image build would need network inside
  containers (blocked behind some proxies, and the reason the build has no `RUN` step), and versions would float
  unless pinned in a second place. Rejected.
- **Any dependency a server wants.** Every server module could then pull in anything, and the image, the
  environment identity and the review burden would grow without anyone deciding it. Rejected.

## Decision

1. **A small, pinned allow-list,** `src/harness_factory/env/mcp/deps.py`. Each library has one exact version and an
   import name, and each server module lists the libraries it may import. Today: `python-docx==1.2.0`,
   `openpyxl==3.1.5`, `pypdf==6.20.1`, all for `docs_server`. A library on the list is small, maintained, makes no
   network or subprocess calls, and ships pure Python or manylinux wheels for x86_64 and aarch64. python-docx
   brings `lxml` (wheels) and `typing_extensions`; openpyxl brings `et-xmlfile` (pure Python); pypdf needs nothing
   on Python 3.12+. Authored servers (`hfx new tool`, ADR 0009) stay stdlib-only: the list is per built-in module.
2. **Installed locally by an optional extra,** `docs` in `pyproject.toml`, with the same exact pins, locked with
   their dependencies in `uv.lock`. A test checks the extra and the allow-list agree. CI installs the extra in the
   test, gates and docker-gate jobs; the factory image installs it by default (`EXTRAS="postgres docs"`).
3. **Vendored into the sandbox image** the way `mcp` is: `env/sandbox_image._vendor` resolves `mcp` plus the
   allow-list on the host for the image's Python and CPU, constrained to the versions installed in the factory's
   environment (so `lxml` and the rest come from `uv.lock`), and the Dockerfile copies them in. `sandbox.Dockerfile`
   is unchanged and still has no `RUN` step. Building the image requires the extra, so the transitive versions are
   always the locked ones. The image probe already imports every server module, so a missing library fails the
   build.
4. **Identity.**
   - The **sandbox image tag** hashes the allow-list's pins (and, as before, every server file, so the new
     `docs_server.py` changes it too). Every sandbox image tag changes once with this PR.
   - **`env_hash`** gets `libraries: {server: [pins]}` for the servers a recipe opens that have any, and only then
     (the same rule as `remote` in ADR 0004). So recipes that don't open `docs` keep their env hash (`v2-base` is
     still `a55c2fb739f1`), and a pin change is a new experiment for exactly the runs that use the server.
   - Check fields added for the new kinds (`sheet`, `cell`, `expected`, `tolerance`, `style`) are left out of the
     task hash while unset, so every existing task keeps its hash.
5. **Missing or wrong versions fail provisioning, clearly** (`ErrorKind.PROVISION`, so ERROR, never scored):
   - a local sandbox checks a server's libraries before starting it (`LocalSandbox.launch`): not installed, or a
     version other than the pin, names the libraries and says `uv sync --extra docs`;
   - the provisioner checks the libraries of a task's check kinds before it creates a sandbox, whichever sandbox
     kind is used, because the graders read office files in the factory process;
   - the server module itself exits with the same advice instead of a traceback if an import fails (for paths that
     don't go through a sandbox, such as an ad-hoc `MCPSessionPool`).
6. **A recipe that can't reach a task's tools doesn't run it.** Office tasks name `docs.*` tools; the base recipes
   don't open the docs server (opening it would change their env hash). Campaign plans (`runner.plan`), and so
   `hfx smoke` and the Inspect adapter, leave out a task × recipe pair when the recipe can't call a tool the task
   names: the selector already prunes that recipe for that task (`reaches_task_tools`), so the run would measure
   nothing. `smoke` prints the pairs it left out. No existing pair is affected: every existing recipe opens `fs`
   and `sql`, and every existing task names only those. A matrix recipe `v2-docs` (v2-base plus the docs server)
   runs the office tasks and is in the CI matrix and docker gates.

## The docs server

`env/mcp/impl/docs_server.py`, eight tools, confined to `HF_WORKSPACE` like `fs`, refusals as `ToolError`, every
output bounded:

| Tool | Side effect, risk | What it does |
|---|---|---|
| `docx_read(path)` | READ, R0 | Body paragraphs with index and style (at most 300, 1000 characters each), then each table's size and first 10 rows |
| `docx_replace(path, old, new)` | REVERSIBLE, R1 | Every exact match in paragraphs and table cells, also across runs; the replacement takes the formatting of the run the match starts in |
| `docx_append_paragraph(path, text, style=None)` | REVERSIBLE, R1 | A paragraph at the end of the body, in one of the document's paragraph styles |
| `xlsx_sheets(path)` | READ, R0 | Each sheet's used range |
| `xlsx_read(path, sheet, range="A1:Z50")` | READ, R0 | Values as JSON rows, cut to the used area and 2000 cells; formula cells show their last saved result and are listed with their formula |
| `xlsx_write(path, sheet, cell, value)` | REVERSIBLE, R1 | One cell: text, number, boolean or null; formulas and merged-cell interiors are refused |
| `pdf_text(path, pages="1-3")` | READ, R0 | Text of up to 10 pages and 20000 characters per call |
| `pdf_info(path)` | READ, R0 | Page count, page size, document information |

PDFs are read only. Saving a workbook through openpyxl keeps formulas but drops their saved results (openpyxl
doesn't calculate), so a task should grade values the model writes, not formula results.

New check kinds (`grading/state.py`, `tasks/model.py`): `xlsx_cell` (path, sheet, cell, `expected`; omitted means
empty; numbers compare within `tolerance` and text is never a number), `docx_contains` (text anywhere in the
paragraphs and table cells) and `docx_paragraph_regex` (some paragraph matches, optionally in a given `style`;
`negate`: none does). PDF checks aren't needed while PDFs are read only.

## Consequences

- New seed `catalog/seeds/office` (a quotation PDF, a price list, a letter), written by `scripts/taskgen/office.py`
  with the standard library only: fixed metadata and zip timestamps and stored entries, so the bytes, and the seed
  hash, are the same on every machine; `tests/test_taskgen.py` rebuilds it and compares. Two tasks use it:
  `office-01` (TEST: price list from the quotation; traps VAT, an item not on the list, the withdrawn quotation's
  prices, a price typed as text, the expiry date) and `office-02` (DEV: fill in a letter; traps the quote date for
  the expiry, replacing the withdrawn quotation's number, signing without the style).
- A second server with libraries adds them to the allow-list and its module's entry, and to the `docs` extra (or a
  new extra, if it has nothing to do with documents; `deps.EXTRA` would then become per library).
- Known limit, unchanged from `mcp`: the image tag hashes the allow-list's pins, not the versions of their
  dependencies (`lxml`), which come from `uv.lock`. A `uv.lock` change to `lxml` alone reuses an existing image
  until something else changes the tag.
