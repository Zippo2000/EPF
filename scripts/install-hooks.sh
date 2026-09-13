#!/bin/sh
# =============================================================================
# Install the gitleaks pre-commit hook (one time).
#
# Points `git config core.hooksPath` at the committed .githooks/ directory so
# that git runs OUR hooks (which live under version control) instead of the
# per-user, untracked .git/hooks/. This keeps the hook shareable across
# clones/developers.
#
#   Run from anywhere in the repo:
#       sh scripts/install-hooks.sh
# =============================================================================
set -eu

# Operate from the repository top level regardless of the caller's CWD.
TOP="$(git rev-parse --show-toplevel 2>/dev/null || { echo "Not inside a git repo." >&2; exit 1; })"
cd "$TOP"

if [ ! -f .githooks/pre-commit ]; then
    echo "Error: .githooks/pre-commit not found (run this from a checkout that has it)." >&2
    exit 1
fi

# Absolute path to the hooks dir, in the native flavour git expects on this OS.
if command -v cygpath >/dev/null 2>&1; then
    ABS_HOOKS="$(cd .githooks && cygpath -w "$PWD")"
else
    ABS_HOOKS="$(cd .githooks && pwd)"
fi

PREV="$(git config core.hooksPath 2>/dev/null || true)"

git config core.hooksPath "$ABS_HOOKS"

echo "core.hooksPath ="
echo "  before: ${PREV:-<unset>}"
echo "  after : $ABS_HOOKS"
echo ""
echo "Done. Staged secrets are now checked on every 'git commit'."
echo "Bypass a single commit with:  git commit --no-verify"
