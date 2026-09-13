#!/bin/sh
# =============================================================================
# Run the offline EPF test suite inside a Linux container.
#
# The test suite imports `app`, which in turn imports the Cython-built `cpy.so`
# (a Linux ELF). That module cannot be imported on a Windows/macOS host, so the
# suite must run under Linux - we reuse the project's own Docker image for that.
#
#   sh scripts/run-tests.sh          # build (if needed) + run
#
# Only the offline cases run here (battery mapping, config handling, settings
# rendering, /sleep contract). Network / live-Immich cases are documented in
# TESTSPEC.md but are intentionally NOT part of this automated run.
# =============================================================================
set -eu

TOP="$(git rev-parse --show-toplevel 2>/dev/null || { echo "not a git repo" >&2; exit 1; })"
cd "$TOP"

IMAGE="epf-tests:local"

echo "==> Building test image ${IMAGE} ..."
docker build -q -t "$IMAGE" . >/dev/null

echo "==> Running the offline test suite inside the container ..."
docker run --rm \
  -e IMMICH_PHOTO_DEST=/tmp/epf_test_photos \
  "$IMAGE" \
  sh -c 'pip install -q pytest 2>/dev/null; cd /app && python -m pytest'
