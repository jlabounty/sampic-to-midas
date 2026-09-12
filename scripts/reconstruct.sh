#!/bin/bash
# Run a .mid file produced by this frontend through the PIONEER Gaudi chain.
#
#     scripts/reconstruct.sh ../online/data/run00019.mid
#     scripts/reconstruct.sh run.mid out.root 500      # limit to 500 events
#
# Runs PIMidasSelector -> PIMidasDecoder(PITMidasSampic) -> PIAOutputStream
# inside the pioneer-midas container and writes an RNTuple named "rec".
#
# Requires: docker, the pioneer-midas image, and main/ already built inside it
# (cd /workdir/main && ./setup.sh -b -t -e). The build is reused from
# main/docker/, so this is only a few seconds once that exists.
#
# Note the container's ENTRYPOINT is already /bin/bash -- passing "bash script"
# would run "bash bash script" and fail with "cannot execute binary file".

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

IN="${1:-}"
if [ -z "$IN" ] || [ ! -f "$IN" ]; then
    echo "usage: $(basename "$0") <input.mid> [output.root] [max events]" >&2
    exit 2
fi
IN_ABS="$(cd "$(dirname "$IN")" && pwd)/$(basename "$IN")"
case "$IN_ABS" in
    "$FS_WORKSPACE"/*) ;;
    *) echo "ERROR: $IN_ABS is outside $FS_WORKSPACE, which is what gets mounted." >&2
       exit 1 ;;
esac

OUT="${2:-$FS_REPO/output/$(basename "${IN%.mid}")_rec.root}"
mkdir -p "$(dirname "$OUT")"
OUT_ABS="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
# The OUTPUT has to live under the workspace too: only that directory is mounted
# into the container, so anywhere else becomes a path the container cannot write
# and Gaudi fails at initialise with a bare "No such file or directory".
case "$OUT_ABS" in
    "$FS_WORKSPACE"/*) ;;
    *) echo "ERROR: output $OUT_ABS is outside $FS_WORKSPACE." >&2
       echo "       Only the workspace is mounted into the container; choose a" >&2
       echo "       path inside it (the default is $FS_REPO/output/)." >&2
       exit 1 ;;
esac
EVTMAX="${3:--1}"

# Paths as the container sees them.
c_path() { echo "/workdir${1#$FS_WORKSPACE}"; }
IMAGE="ghcr.io/pioneer-experiment/pioneer-midas:latest-$(uname -m)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: image $IMAGE is not present locally." >&2
    echo "       docker pull $IMAGE" >&2
    exit 1
fi
if [ ! -f "$FS_WORKSPACE/main/docker/setenv.sh" ]; then
    echo "ERROR: main/ has not been built in the container." >&2
    echo "       scripts/run_container.sh, then: cd /workdir/main && ./setup.sh -b -t -e" >&2
    exit 1
fi

echo "reconstructing $(basename "$IN_ABS") -> $(basename "$OUT_ABS")"
docker run --rm -v "$FS_WORKSPACE":/workdir \
    -e SAMPIC_MID="$(c_path "$IN_ABS")" \
    -e SAMPIC_REC="$(c_path "$OUT_ABS")" \
    -e SAMPIC_EVTMAX="$EVTMAX" \
    "$IMAGE" /workdir/sampic-to-midas/scripts/run_reco.sh
echo
echo "wrote $OUT_ABS"
echo "  read it with:  uproot.open('$(basename "$OUT_ABS")')['rec']"
