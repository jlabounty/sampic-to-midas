#!/bin/bash
# Build MIDAS from source into the workspace. Run once.
#
#     scripts/setup-midas.sh
#
# Clones tag $FS_MIDAS_TAG into $FS_MIDASSYS and builds it there, so MIDASSYS is
# self-contained inside the fake_sampic workspace and no system directory is
# touched. Idempotent: an existing clone is reused, an existing build is
# incremental.
#
# ROOT is NOT required and is deliberately disabled: nothing here needs it, and
# looking for it only creates a way for the build to fail.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

: "${FS_MIDAS_TAG:=midas-2026-07-a}"
: "${FS_MIDAS_REPO:=https://bitbucket.org/tmidas/midas}"
JOBS="${FS_BUILD_JOBS:-$(nproc 2>/dev/null || echo 4)}"

echo "MIDAS $FS_MIDAS_TAG -> $FS_MIDASSYS"

# --- zlib -----------------------------------------------------------------------
#
# MIDAS does `find_package(ZLIB REQUIRED)` (CMakeLists.txt:236) and midasio.cxx
# includes <zlib.h>, so zlib development headers are mandatory. The clean way is
#
#     sudo apt install zlib1g-dev
#
# but that needs root, which we may not have. The conda environment already
# ships zlib, so fall back to it -- via a directory containing ONLY symlinks to
# zlib.h/zconf.h, not conda's whole include tree. Putting all of conda's headers
# on the include path would shadow the system OpenSSL headers while still
# linking the system libssl, which is an ABI mismatch that does not announce
# itself.
#
# Note MIDAS finds the zlib LIBRARY via ZLIB_ROOT but never adds its include
# directory to the compile flags, so -isystem is needed even when cmake reports
# "Found ZLIB".

ZLIB_ARGS=()
if [ -f /usr/include/zlib.h ]; then
    echo "  zlib: using the system headers"
else
    CONDA_PREFIX_DIR="$FS_CONDA_ROOT/envs/$FS_CONDA_ENV"
    if [ ! -f "$CONDA_PREFIX_DIR/include/zlib.h" ]; then
        echo "ERROR: no zlib headers." >&2
        echo "       Either install them:      sudo apt install zlib1g-dev" >&2
        echo "       or create the conda env first: scripts/setup-conda.sh" >&2
        exit 1
    fi
    SHIM="$FS_WORKSPACE/midas-deps/include"
    mkdir -p "$SHIM"
    ln -sf "$CONDA_PREFIX_DIR/include/zlib.h"  "$SHIM/zlib.h"
    ln -sf "$CONDA_PREFIX_DIR/include/zconf.h" "$SHIM/zconf.h"
    echo "  zlib: using the conda environment's headers via $SHIM"
    ZLIB_ARGS=(
        -DZLIB_ROOT="$CONDA_PREFIX_DIR"
        -DCMAKE_C_FLAGS="-isystem $SHIM"
        -DCMAKE_CXX_FLAGS="-isystem $SHIM"
        # RPATH so the binaries find conda's libz without LD_LIBRARY_PATH.
        # Exporting that globally leaks conda's libtinfo into every program the
        # shell runs, including /bin/bash, which prints warnings and is a real
        # hazard for anything else on the PATH.
        -DCMAKE_INSTALL_RPATH="$CONDA_PREFIX_DIR/lib"
        -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
    )
fi

# --- clone ----------------------------------------------------------------------

if [ ! -d "$FS_MIDASSYS/.git" ]; then
    mkdir -p "$(dirname "$FS_MIDASSYS")"
    # --recurse-submodules is not optional: mxml, mjson, mvodb and midasio are
    # submodules, and without them the build fails much later and less clearly.
    git clone --branch "$FS_MIDAS_TAG" --recurse-submodules --depth 1 \
        "$FS_MIDAS_REPO" "$FS_MIDASSYS"
else
    echo "  reusing existing clone ($(git -C "$FS_MIDASSYS" describe --tags 2>/dev/null || echo unknown))"
fi

# --- build ----------------------------------------------------------------------

mkdir -p "$FS_MIDASSYS/build"
cd "$FS_MIDASSYS/build"

# NO_NVIDIA: MIDAS builds msysmon-nvidia whenever /usr/local/cuda/include exists
# and `nvidia-smi -L` succeeds (CMakeLists.txt:396-402), then links -lnvidia-ml
# from /usr/local/cuda/lib64. On a WSL2 host both conditions hold but the library
# is not there, so the whole build fails on a monitoring tool nothing here uses.
cmake .. \
    -DCMAKE_INSTALL_PREFIX="$FS_MIDASSYS" \
    -DNO_ROOT=ON \
    -DNO_NVIDIA=ON \
    "${ZLIB_ARGS[@]}"

make -j"$JOBS"
make install

# The python bindings dlopen this specific file (midas/client.py:113), not
# libmidas.a -- so this, and not the presence of bin/odbedit, is what decides
# whether the frontend can run at all.
if [ ! -f "$FS_MIDASSYS/lib/libmidas-c-compat.so" ]; then
    echo "ERROR: built, but $FS_MIDASSYS/lib/libmidas-c-compat.so is missing." >&2
    echo "       The python bindings load that file; without it the frontend cannot start." >&2
    exit 1
fi

echo
echo "MIDAS $(git -C "$FS_MIDASSYS" describe --tags 2>/dev/null) installed in $FS_MIDASSYS"
echo "next: scripts/setup-conda.sh, then scripts/start-midas.sh"
