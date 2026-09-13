#!/bin/sh
# =============================================================================
# Run the LIVE integration tests against a real Immich v3 server.
#
# Usage:
#   sh scripts/run-live-tests.sh <immich-url> [album-name]
#     e.g.  sh scripts/run-live-tests.sh http://192.168.1.10:2283 eink
#
# The API key is NOT a command argument: it is read from the local, git-ignored
# .env file (IMMICH_API_KEY) so it never shows up in shell history / the process
# list / version control. The Immich URL and album come from the arguments.
#
# This exercises the v3 API contract (albums, paginated search/metadata,
# original download) plus the full app /download pipeline end-to-end.
# =============================================================================
set -eu

TOP="$(git rev-parse --show-toplevel 2>/dev/null || { echo "not a git repo" >&2; exit 1; })"
cd "$TOP"

# --- Connection details ------------------------------------------------------
# Import KEY=VALUE pairs from .env if present (supplies IMMICH_API_KEY).
set -a
[ -f .env ] && . ./.env
set +a

IMMICH_LIVE_URL="${1:-${IMMICH_LIVE_URL:-}}"
EPF_LIVE_ALBUM="${2:-${EPF_LIVE_ALBUM:-eink}}"

if [ -z "$IMMICH_LIVE_URL" ]; then
    echo "Usage: sh scripts/run-live-tests.sh <immich-url> [album]" >&2
    echo "  e.g. sh scripts/run-live-tests.sh http://<host>:2283 eink" >&2
    exit 1
fi
if [ -z "${IMMICH_API_KEY:-}" ]; then
    echo "Error: no IMMICH_API_KEY available (put it in the local .env file)." >&2
    exit 1
fi

IMAGE="epf-tests:live"
echo "==> Building test image ${IMAGE} ..."
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Running live tests against ${IMMICH_LIVE_URL} (album: ${EPF_LIVE_ALBUM}) ..."
docker run --rm \
    -e EPF_LIVE_TESTS=1 \
    -e IMMICH_LIVE_URL="$IMMICH_LIVE_URL" \
    -e IMMICH_API_KEY="$IMMICH_API_KEY" \
    -e EPF_LIVE_ALBUM="$EPF_LIVE_ALBUM" \
    -e IMMICH_PHOTO_DEST=/tmp/epf_live_photos \
    "$IMAGE" \
    sh -c 'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -v tests/test_live.py'
