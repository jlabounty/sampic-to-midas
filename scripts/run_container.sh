#!/bin/bash
# Open an interactive shell in the pioneer-midas container with the whole
# fake_sampic workspace mounted at /workdir (main/, data/, sampic-to-midas/).
# --net=host lets jupyter (notebooks/) be reached from the host browser.
set -euo pipefail

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCH="$(uname -m)"

exec docker run -it --rm \
    -v "$WORKSPACE":/workdir \
    --net=host \
    "ghcr.io/pioneer-experiment/pioneer-midas:latest-${ARCH}" "$@"
