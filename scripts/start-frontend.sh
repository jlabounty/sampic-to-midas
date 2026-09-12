#!/bin/bash
# Start the fake SAMPIC frontend against the fakesampic experiment.
#
#     scripts/start-frontend.sh            # foreground (Ctrl-C to stop)
#     scripts/start-frontend.sh --daemon   # background, logging to online/fe.log
#
# Refuses to start a second copy: two frontends with the same client name fight
# over one equipment's ODB tree, and the symptom (settings that revert by
# themselves) does not look anything like the cause.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

fs_require_midas
fs_require_python

if [ ! -f "$FS_EXPT_DIR/.ODB.SHM" ]; then
    echo "ERROR: no ODB at $FS_EXPT_DIR. Run scripts/start-midas.sh first." >&2
    exit 1
fi

# fs_pids_matching skips shells, so this cannot match the script itself -- a
# plain `pgrep -f` here reported "already running" against its own command line.
_fs_running="$(fs_pids_matching "fakesampic.frontend")"
if [ -n "$_fs_running" ]; then
    echo "ERROR: a fake-sampic frontend is already running (pid $(echo $_fs_running | cut -d' ' -f1))." >&2
    echo "       Stop it first, or run scripts/stop-midas.sh." >&2
    exit 1
fi

if [ ! -f "$FS_DEFAULT_BIN" ]; then
    echo "note: FS_DEFAULT_BIN does not exist, so Settings/BinFile/Files is seeded empty:"
    echo "      $FS_DEFAULT_BIN"
    echo "      Set it in the ODB, or use Settings/Source = synthetic."
fi

echo "starting fake-sampic frontend (experiment $FS_EXPT_NAME)"
if [ "${1:-}" = "--daemon" ]; then
    LOG="$FS_EXPT_DIR/fe.log"
    nohup "$FS_PYTHON" -m fakesampic.frontend -e "$FS_EXPT_NAME" >"$LOG" 2>&1 &
    echo "  pid $!, logging to $LOG"
    # Poll for the equipment to appear rather than sleeping a fixed time.
    for _ in $(seq 1 100); do
        if odbedit -e "$FS_EXPT_NAME" -c "ls /Equipment/FakeSampic/Common" >/dev/null 2>&1; then
            echo "  equipment registered"
            exit 0
        fi
        sleep 0.1
    done
    echo "  WARNING: equipment did not appear; check $LOG" >&2
    exit 1
fi
exec "$FS_PYTHON" -m fakesampic.frontend -e "$FS_EXPT_NAME"
