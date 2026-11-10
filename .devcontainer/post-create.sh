#!/usr/bin/env bash
# Runs once when the dev container is created.
set -euo pipefail
cd "$(dirname "$0")/.."
ws="$PWD"

# Docker volumes are created root-owned.
sudo chown -R vscode:vscode /home/vscode/.venvs /home/vscode/.cache/uv /home/vscode/.claude web/node_modules

# The bind mount is owned by another uid as far as git is concerned.
git config --global --add safe.directory "$ws"

# Windows has no executable bit, so every file on the mount looks executable (ruff EXE002 on all of them). Set each
# tracked file's mode from git's index, as a Linux checkout or CI would. Git ignores modes here (core.filemode=false).
# The mount shows files as root-owned, hence sudo.
git ls-files -s | awk '$1=="100644"{sub(/^[^\t]*\t/,"");print}' | tr '\n' '\0' | xargs -0 -r sudo chmod a-x
git ls-files -s | awk '$1=="100755"{sub(/^[^\t]*\t/,"");print}' | tr '\n' '\0' | xargs -0 -r sudo chmod a+x
# Untracked files made on Windows: executable only if they start with a shebang.
git ls-files -z --others --exclude-standard | while IFS= read -r -d '' f; do
  [ "$(head -c2 "$f" 2>/dev/null)" = "#!" ] || sudo chmod a-x "$f"
done

uv sync --extra dev --extra mlops
# npm install, not npm ci: ci deletes node_modules, which is a volume mount point here.
(cd web && npm install --no-audit --no-fund)

# Seed Claude Code's memory for this project once (copied in from the host by the setup, gitignored).
key="$(printf '%s' "$ws" | sed 's/[^A-Za-z0-9]/-/g')"
mem="/home/vscode/.claude/projects/$key/memory"
if [ -d .devcontainer/local/claude-memory ] && [ ! -e "$mem/MEMORY.md" ]; then
  mkdir -p "$mem"
  cp .devcontainer/local/claude-memory/*.md "$mem/"
  echo "Seeded Claude Code memory into $mem"
fi

mkdir -p var/exports

# Signing: commits on main must be signed (CLAUDE.md). Report, don't work around.
if [ ! -f .devcontainer/local/signing.pub ]; then
  echo "WARNING: .devcontainer/local/signing.pub is missing; signed commits will fail. See .devcontainer/README.md"
elif ! ssh-add -l >/dev/null 2>&1; then
  echo "NOTE: no SSH agent with a key is forwarded; signed commits will fail until it is. See .devcontainer/README.md"
fi
echo "Dev container ready. Try: uv run hfx validate"
