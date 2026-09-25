#!/bin/sh
# Stamp the exact local source state without copying Git or local secrets into the image.
set -eu
cd "$(dirname "$0")/.."
build_sha=$(git rev-parse --verify HEAD)
build_dirty=false
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    build_dirty=true
fi
printf 'Building %.12s (dirty=%s)\n' "$build_sha" "$build_dirty"
exec docker compose build --build-arg "BUILD_SHA=$build_sha" \
    --build-arg "BUILD_DIRTY=$build_dirty" "$@"
