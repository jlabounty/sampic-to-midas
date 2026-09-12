#!/bin/bash
# The single place this project's paths are defined. Source it, do not run it.
#
#     source scripts/fake-sampic-env.sh
#
# Deliberately self-contained: MIDAS, the conda environment and the experiment
# all live under the fake_sampic workspace, and MIDAS_EXPTAB is exported for
# these processes only. Nothing outside the workspace is read or written, so an
# existing MIDAS experiment on this machine is untouched -- which matters,
# because a frontend that injects fabricated events into somebody's real
# experiment is a corrupted dataset nobody notices until analysis.
#
# Override any FS_* value by exporting it before sourcing.

# --- where everything lives ---------------------------------------------------

_fs_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${FS_REPO:=$(cd "$_fs_here/.." && pwd)}"
: "${FS_WORKSPACE:=$(cd "$FS_REPO/.." && pwd)}"

: "${FS_MIDASSYS:=$FS_WORKSPACE/midas}"
: "${FS_EXPT_NAME:=fakesampic}"
: "${FS_EXPT_DIR:=$FS_WORKSPACE/online}"
: "${FS_DATA_DIR:=$FS_EXPT_DIR/data}"
: "${FS_MHTTPD_PORT:=8080}"
: "${FS_ODB_SIZE:=64MB}"
: "${FS_CONDA_ENV:=fake-sampic}"
: "${FS_CONDA_ROOT:=$HOME/miniconda3}"

# The default .bin to replay: the one real SAMPIC run in this workspace.
: "${FS_DEFAULT_BIN:=$FS_WORKSPACE/data/W9PIN_14MeV_0deg_100V_run914/W9PIN_14MeV_0deg_100V_run914.bin}"

export FS_REPO FS_WORKSPACE FS_MIDASSYS FS_EXPT_NAME FS_EXPT_DIR FS_DATA_DIR
export FS_MHTTPD_PORT FS_ODB_SIZE FS_CONDA_ENV FS_CONDA_ROOT FS_DEFAULT_BIN

# --- MIDAS --------------------------------------------------------------------

export MIDASSYS="$FS_MIDASSYS"
export MIDAS_EXPTAB="$FS_EXPT_DIR/exptab"
export MIDAS_EXPT_NAME="$FS_EXPT_NAME"

case ":$PATH:" in
    *":$MIDASSYS/bin:"*) ;;
    *) export PATH="$MIDASSYS/bin:$PATH" ;;
esac

# No LD_LIBRARY_PATH here on purpose. With zlib1g-dev installed MIDAS links the
# system zlib and needs nothing; on a machine without it, setup-midas.sh falls
# back to conda's zlib and embeds an RPATH in the binaries instead. Exporting
# LD_LIBRARY_PATH globally would put conda's libtinfo, libssl and so on ahead of
# the system ones for EVERY program this shell runs -- /bin/bash included, which
# then prints version warnings -- and that is a broad change to make for one
# library.

# --- python -------------------------------------------------------------------

export FS_PYTHON="$FS_CONDA_ROOT/envs/$FS_CONDA_ENV/bin/python"

# The repo itself on the path, so `converter` and `fakesampic` import without an
# install step. The midas bindings are pip-installed into the env by setup-conda.sh.
case ":${PYTHONPATH:-}:" in
    *":$FS_REPO:"*) ;;
    *) export PYTHONPATH="$FS_REPO${PYTHONPATH:+:$PYTHONPATH}" ;;
esac

# --- helpers ------------------------------------------------------------------

fs_have_midas() { [ -x "$MIDASSYS/bin/odbedit" ] && [ -f "$MIDASSYS/lib/libmidas-c-compat.so" ]; }

fs_require_midas() {
    if ! fs_have_midas; then
        echo "ERROR: no usable MIDAS at $MIDASSYS" >&2
        echo "       expected bin/odbedit and lib/libmidas-c-compat.so (the file the" >&2
        echo "       python bindings dlopen). Run scripts/setup-midas.sh first." >&2
        return 1
    fi
}

fs_require_python() {
    if [ ! -x "$FS_PYTHON" ]; then
        echo "ERROR: no conda environment '$FS_CONDA_ENV' at $FS_PYTHON" >&2
        echo "       Run scripts/setup-conda.sh first." >&2
        return 1
    fi
}

# Find our own running daemons.
#
# NOT `pgrep -f`, which matches whole command lines and therefore matches any
# shell whose command line happens to mention the program -- the shell calling
# this function included, and an unrelated terminal running `tail -f mhttpd.log`
# too. That makes "is it running?" answer yes when nothing is running, and makes
# "stop it" kill the caller.
#
# Walk /proc instead, skip anything whose executable is a shell or a process
# tool, and match the rest on their command line. A daemon of ours is never
# named bash.
fs_pids_matching() {
    local want="$1" pid comm cmdline uid
    uid="$(id -u)"
    for pid in /proc/[0-9]*; do
        pid="${pid#/proc/}"
        [ -r "/proc/$pid/cmdline" ] || continue
        [ "$(stat -c %u "/proc/$pid" 2>/dev/null)" = "$uid" ] || continue
        comm="$(cat "/proc/$pid/comm" 2>/dev/null)" || continue
        case "$comm" in
            bash|sh|dash|zsh|ksh|pgrep|pkill|ps|grep|tail|less|vim|nano) continue ;;
        esac
        cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
        case "$cmdline" in *"$want"*) echo "$pid" ;; esac
    done
}

fs_is_running() { [ -n "$(fs_pids_matching "$1 -e $FS_EXPT_NAME")" ]; }

fs_banner() {
    echo "fake-sampic environment"
    echo "  workspace : $FS_WORKSPACE"
    echo "  MIDASSYS  : $MIDASSYS $(fs_have_midas && echo '(built)' || echo '(NOT BUILT)')"
    echo "  experiment: $FS_EXPT_NAME at $FS_EXPT_DIR"
    echo "  exptab    : $MIDAS_EXPTAB"
    echo "  mhttpd    : http://localhost:$FS_MHTTPD_PORT"
    echo "  python    : $FS_PYTHON"
}
