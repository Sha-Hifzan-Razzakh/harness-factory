# ADR 0012: Running code in the sandbox

Status: accepted · 2026-11-02 · Requirements: ENV-01 (part), GRD-01 (tests pass) · Review: §2.2 scenario 3

## Context

The tools could read and write files, query SQLite, search and load skills. Nothing could run code, run tests or
use git, so the catalog couldn't pose the most common agent task there is: run the tests, read the failure, fix
it, run them again, commit. The graders had no "tests pass" check either. Both need code from the workspace to run
somewhere, and that code is the model's: it is untrusted, it can loop forever, print without end, read the
environment, and write wherever the process can.

## Decision

1. **Two new servers, standard library and `mcp` only.** `code` (`run_tests`, `run_python`) and `git` (`status`,
   `diff`, `log`, `commit`, `branch`, `checkout`). They never import pytest or a git library: they start
   `python -m pytest` and `git` as child processes. The process handling lives in one stdlib module,
   `env/mcp/impl/proc.py`, which the servers, the git seed and the graders share. It is in `impl/`, so the sandbox
   image carries it and its tag covers it.
2. **Every child process is bounded.**
   - **Time:** a hard limit per call (code 60 s, `HF_CODE_TIMEOUT_S` to override; git 20 s; a grading run 120 s).
     The child starts in its own process group, and the whole group is killed when the limit runs out, and again
     after a normal exit, so a background process it started dies with it.
   - **Output:** a bounded buffer keeps the first 4 KB and the last 12 KB, and drops the middle. The model sees
     the last 6,000 characters, where pytest prints its summary.
   - **Environment:** scrubbed. Code keeps only `PATH`, `PYTHONPATH` (the sandbox image needs it to find pytest)
     and the platform basics. It gets a throwaway `HOME`, no bytecode, `PYTHONHASHSEED=0` and no pytest plugin
     autoload. There are no API keys, no `HF_*` paths, and no user or system git config.
3. **Confinement.** Paths are workspace-relative and checked as `fs` checks them. `run_tests` accepts selection and
   reporting options only (`-k`, `-m`, `-x`, `-v`, `--tb=…`, `--maxfail=N`, `-r…`, `--co`). Anything that names a
   file or directory (`--rootdir`, `--basetemp`, `-c`, `-p`, `--junitxml`) is refused with the allowed list.
   pytest runs as `python -P -m pytest -o pythonpath=<workspace>`: the workspace joins `sys.path` only after
   pytest has been imported, so a `pytest.py` the agent wrote can't replace pytest.
4. **Side effects: both code tools are `REVERSIBLE`, R2.** Running tests is not a read, because the code under
   test is the agent's and can write. In docker mode it can change anything under `/work`, `/state` and `/tmp`,
   and a sandbox reset undoes all of it. R2 rather than R1, because that reach includes the task database and the
   git directory, not just the workspace. Consequence: once a recipe offers `code.*`, the IRREVERSIBLE gating of
   `fs.delete_file` and destructive SQL can't be enforced, because a script can delete a file directly. Tasks
   that measure that gating don't allow code tools. Git's reads are `READ`, and `commit`, `branch` and `checkout`
   are `REVERSIBLE`, R1. None of them declares `granted_by`, because history and test results reveal more than
   `fs` does. Tasks list them in `allowed_tools` directly, and the tasks are new, so no existing hash changes.
5. **Repeats of a test run aren't waste just because they repeat.** `observes: true` in `tools.yaml` marks a tool
   that is called for what it reports about the current state (both code tools). The trajectory grader counts a
   repeat as wasted only when nothing changed since the same call, as it does for a read, so test → fix → test
   costs nothing. The call may still change files, so the reads before it are stale afterwards. Without this,
   every honest fix loop would lose Q. The field is optional and hashed only when set.
6. **Git lives outside the workspace.** `env_seed.git: true` makes the sandbox commit the seed files once, with the
   git directory at `HF_GIT_DIR`, in the state folder next to the database (`/state/git` in docker). The work tree
   is the workspace. So:
   - the agent's file tools can't edit the repository's config or hooks;
   - the state diff and the file checks see only the task's files, never `.git`;
   - `core.worktree` is unset, and the work tree is passed on every call, because the path differs in docker.

   The seed commit is made identically everywhere: identity `Harness Factory <agent@harness-factory.invalid>`,
   date 2026-01-01T00:00:00Z, branch `main`, SHA-1 objects, `core.filemode=false`. Its id depends only on the seed
   files (`310fc5f…` for `seeds/pricing`, pinned in a test). Later commits are dated one minute after their
   parent, so the same work makes the same commit ids, which keeps records and replays comparable.
7. **Git is hardened by command-line config,** the scope that beats the repository's own. Hooks point to an empty
   directory, and fsmonitor, pager, signing, `log.showSignature`, credential helpers, auto-gc and every remote
   transport (`protocol.allow=never`) are off. There is no system or global config except `safe.directory`.
   `--no-verify` is always passed. There is no tool for remotes, push, fetch, reset or rebase. Branch names are
   checked with a strict pattern and `check-ref-format`, so a name starting with `-` can't become an option.
   `checkout` takes local branches only. Code in the sandbox can write the git directory, so a test plants
   hooks, an fsmonitor command, a pager and a signing program there and checks that none of them runs.
8. **Two new check kinds, graded inside the sandbox.**
   - `tests_pass`: `path` (default `.`), or `text` (a hidden test module); `pattern` is a `-k` expression. It
     passes on exit code 0 only. A failure, a collection error, "no tests" (5) and the time limit all fail it.
   - `git_log_contains`: a commit on `ref` (default `HEAD`) after the seed commit, whose message matches
     `pattern`, and which touches `path` if one is given. With `negate` it means "no such commit". The
     zero-weight `main_untouched` guard is written this way.

   A task with a git check must set `env_seed.git` (checked when it loads). Both checks go through
   `Sandbox.probe()`: `python -P -m harness_factory.env.mcp.impl.proc`, with the request on stdin, run as a local
   subprocess or by `docker exec` in the run's container. So the agent's code is only ever executed where the
   agent ran it, never on the host in docker mode, and git never reads a repository config that code inside the
   container may have written. The grader runs pytest with `--noconftest` and an empty config file of its own. A
   `conftest.py` that reports every test as passed, or `addopts` in the workspace's `pytest.ini`, doesn't change
   the verdict. Hidden tests are written to a temporary directory outside the workspace, at grading time only,
   so the agent never sees them. A probe that can't answer (Docker gone, bad output) raises a sandbox error: the
   environment failed, not the agent. Grading now runs in a worker thread, because a test run can take minutes
   and the other runs of a campaign share the event loop.
9. **Real models don't run code in a local sandbox unless asked.** A local sandbox confines paths in the servers,
   and nothing else. Code there runs as you, with your network. So the provisioner refuses a non-scripted run
   whose recipe opens `code` or whose task has a `tests_pass` check, unless `HF_SANDBOX=docker` or
   `HF_ALLOW_LOCAL_CODE=1`. Scripted runs (CI, tests, references) run only the reference's code and need neither.
10. **Campaigns don't plan what can't be reached.** `plan()` drops a (task, recipe) pair when the recipe can't
    call every tool the task allows. This is the selector's existing `reaches_task_tools` rule, now shared as
    `provisioner.missing_task_tools`, and the selector would prune that recipe anyway. So `v2-base`, the V3–V5
    base recipes and the other matrix recipes plan exactly what they planned before, and the code tasks run under
    the new matrix recipe `v2-code` (v2-base plus `code` and `git`).
11. **pytest is a main dependency.** The tests_pass grader and the local code server run pytest from the
    factory's own Python, so an install without the `dev` extra (the factory image) needs it. An optional
    `code` extra was considered and rejected: a task with a `tests_pass` check would then fail to grade on a
    plain install. pytest is small and pure Python. The sandbox image vendors the same pytest version on the host
    alongside `mcp`, with no network inside the build, and its version is part of the image tag.
12. **git comes from the OS.** The sandbox Dockerfile gets its first `RUN` step:
    `command -v git || apt-get install git`. A base image that already has git builds with no network, as before.
    Otherwise the base must be Debian-based. The factory image installs git too, because seed repositories are
    made on the factory side and local sandboxes run the git server there. Locally, git must be on `PATH`: on
    Windows, install Git for Windows. A git-seeded task without git fails at sandbox setup (ERROR, not scored).

## Consequences

- **Identity.** Existing task, recipe and environment hashes are unchanged. `env_seed.git` and the check field
  `ref` are dropped from the task hash when unset: all 23 earlier task hashes were compared before and after, and a test
  checks every task without git against the earlier formula. A task
  that sets `git: true` hashes `proc.GIT_SEED` too, so changing how seeds are committed changes those tasks.
  `observes` is part of a tool's hashed spec only when set. The new servers are new environment hashes. Recipes
  that don't open them keep theirs (`v2-base` stays `a55c2fb739f1`).
- **Every sandbox image tag changes,** because the Dockerfile, the new server modules and the pytest version are
  all hashed. The first Docker run after this PR rebuilds the image, and the build needs apt mirrors unless the
  base already has git.
- **Run status and scoring.** No change for existing tasks. The new check kinds score only tasks that use them.
  The `observes` rule applies only to the code tools, which no earlier run could call.
- **What tests_pass trusts.** The tests import the agent's code, and code can always attack the process it runs
  in, for example by monkeypatching pytest at import. The grader closes the cheap shortcuts: the agent's
  conftest, workspace config, a shadowing `pytest.py`, editing the tests (a file guard), and special-casing the
  visible inputs (hidden tests). A deliberate attack on the test runner is out of scope here. The rubric judge
  (#49) reading the diff is the backstop.
- **Windows.** `run()` kills the process tree with `taskkill /T` instead of a process group. Git for Windows must
  be on `PATH`. Seed files checked out with CRLF line endings give a different seed commit, as they already give
  a different seed hash.
- **Two tasks so far,** on a small `pricing` seed: `code-02` (DEV: fix a boundary bug, commit with "Fix…") and
  `code-03` (TEST: work on a branch, round VAT halves up, leave `main` alone). Each has hidden tests, file guards
  and trap scripts. The long code tasks belong to task batch 4 (#52).
