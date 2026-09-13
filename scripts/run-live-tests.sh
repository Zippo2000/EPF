#!/bin/sh
# =============================================================================
# Run the LIVE integration tests against a real Immich v3 server.
#
# All connection details are read from the local, git-ignored .env file:
#     IMMICH_URL      base URL of the Immich server
#     IMMICH_API_KEY  the server's API key
#     IMMICH_ALBUM    album the tests exercise
# so nothing sensitive is a command-line argument. The two values may still be
# overridden on the command line for a one-off run:
#
#     sh scripts/run-live-tests.sh                      # uses .env
#     sh scripts/run-live-tests.sh <immich-url> [album]
#
# This exercises the v3 API contract (albums, paginated search/metadata,
# original download) plus the full app /download pipeline end-to-end.
# =============================================================================
set -eu

TOP="$(git rev-parse --show-toplevel 2>/dev/null || { echo "not a git repo" >&2; exit 1; })"
cd "$TOP"

# Import KEY=VALUE pairs from .env if present (supplies the three vars above).
set -a
[ -f .env ] && . ./.env
set +a

# Command-line overrides take precedence over the .env values.
IMMICH_URL="${1:-${IMMICH_URL:-}}"
IMMICH_ALBUM="${2:-${IMMICH_ALBUM:-eink}}"

if [ -z "$IMMICH_URL" ]; then
    echo "Usage: sh scripts/run-live-tests.sh [<immich-url> [album]]" >&2
    echo "  (defaults read from .env: IMMICH_URL, IMMICH_ALBUM, IMMICH_API_KEY)" >&2
    exit 1
fi
if [ -z "${IMMICH_API_KEY:-}" ]; then
    echo "Error: no IMMICH_API_KEY available (set it in the local .env file)." >&2
    exit 1
fi

IMAGE="epf-tests:live"
echo "==> Building test image ${IMAGE} ..."
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Running live tests against ${IMMICH_URL} (album: ${IMMICH_ALBUM}) ..."
docker run --rm \
    -e EPF_LIVE_TESTS=1 \
    -e IMMICH_URL="$IMMICH_URL" \
    -e IMMICH_API_KEY="$IMMICH_API_KEY" \
    -e IMMICH_ALBUM="$IMMICH_ALBUM" \
    -e IMMICH_PHOTO_DEST=/tmp/epf_live_photos \
    "$IMAGE" \
    sh -c 'pip install -q pytest 2>/dev/null; cd /app && python -m pytest -v tests/test_live.py'
