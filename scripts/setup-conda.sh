#!/bin/bash
# Create the conda environment this project runs in. Run once.
#
#     scripts/setup-conda.sh [--python 3.14]
#
# A dedicated environment, not the conda base: base is the shell default here,
# and installing into it would affect every other project on this machine.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

PYVER="3.14"
if [ "${1:-}" = "--python" ] && [ -n "${2:-}" ]; then PYVER="$2"; fi

if [ ! -x "$FS_CONDA_ROOT/bin/conda" ]; then
    echo "ERROR: no conda at $FS_CONDA_ROOT/bin/conda (set FS_CONDA_ROOT)." >&2
    exit 1
fi
CONDA="$FS_CONDA_ROOT/bin/conda"

if "$CONDA" env list | awk '{print $1}' | grep -qx "$FS_CONDA_ENV"; then
    echo "conda environment '$FS_CONDA_ENV' already exists"
else
    echo "creating conda environment '$FS_CONDA_ENV' (python $PYVER)"
    # numpy is the only hard dependency; pytest is for the test suite. zlib comes
    # along with python and is what setup-midas.sh falls back to for headers.
    "$CONDA" create -y -n "$FS_CONDA_ENV" "python=$PYVER" numpy pytest
fi

PY="$FS_CONDA_ROOT/envs/$FS_CONDA_ENV/bin/python"

if [ -d "$FS_MIDASSYS/python" ]; then
    echo "installing the MIDAS python bindings from $FS_MIDASSYS/python"
    "$PY" -m pip install -q -e "$FS_MIDASSYS/python"
else
    echo "note: $FS_MIDASSYS/python not found; run scripts/setup-midas.sh first."
fi

echo
"$PY" - <<'PYEOF'
import sys
print(f"python {sys.version.split()[0]}")
try:
    import numpy; print(f"numpy  {numpy.__version__}")
except ImportError as e: print(f"numpy  MISSING ({e})")
try:
    import midas, midas.frontend, midas.event
    print("midas  bindings import OK")
except Exception as e:
    print(f"midas  bindings FAILED: {e}")
    print("       If this is a python-version problem, recreate with:")
    print("         scripts/setup-conda.sh --python 3.12")
    sys.exit(1)
PYEOF
echo
echo "next: scripts/start-midas.sh"
