#!/bin/bash
# Build the pioneer-midas docker image (pioneer base + MIDAS libraries).
#
# The Dockerfile lives on the main-repo branch feature/container-updates-July26
# (utils/docker/midas/). We extract it with `git show` so the local checkout
# stays on its current branch. The branch's own build_container.sh passes
# --build-arg BASE_IMG but the Dockerfile ARG is BASE_IMAGE (defaulting to the
# arm64 base), so we call docker directly with BASE_IMAGE set explicitly.
set -euo pipefail

MAIN_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../main" && pwd)"
ARCH="$(uname -m)"
BASE="ghcr.io/pioneer-experiment/pioneer:latest-${ARCH}"
TAG="ghcr.io/pioneer-experiment/pioneer-midas:latest-${ARCH}"
BRANCH="origin/feature/container-updates-July26"

git -C "$MAIN_REPO" fetch origin feature/container-updates-July26

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
git -C "$MAIN_REPO" show "${BRANCH}:utils/docker/midas/Dockerfile" \
    > "$BUILD_DIR/Dockerfile"

echo "Building $TAG from $BASE ..."
docker build \
    --build-arg BASE_IMAGE="$BASE" \
    -t "$TAG" \
    -f "$BUILD_DIR/Dockerfile" "$BUILD_DIR"
echo "Done: $TAG"
