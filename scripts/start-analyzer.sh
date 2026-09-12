#!/bin/bash
# Start the histogram backend against the fakesampic experiment.
#
#     scripts/start-analyzer.sh            # foreground (Ctrl-C to stop)
#     scripts/start-analyzer.sh --daemon   # background, logging to online/analyzer.log
#
# The analyzer registers no equipment and no transition callbacks, so it can
# never delay a run start however wedged it gets, and it samples the buffer
# with GET_NONBLOCKING so it can never back-pressure the frontend.

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

_running="$(fs_pids_matching "fakesampic.analyzer")"
if [ -n "$_running" ]; then
    echo "ERROR: an analyzer is already running (pid $(echo $_running | cut -d' ' -f1))." >&2
    exit 1
fi

echo "starting sampic-analyzer (experiment $FS_EXPT_NAME)"
if [ "${1:-}" = "--daemon" ]; then
    LOG="$FS_EXPT_DIR/analyzer.log"
    nohup "$FS_PYTHON" -m fakesampic.analyzer -e "$FS_EXPT_NAME" >"$LOG" 2>&1 &
    echo "  pid $!, logging to $LOG"
    for _ in $(seq 1 100); do
        if odbedit -e "$FS_EXPT_NAME" -c "ls /Analyzer/SampicDQM" >/dev/null 2>&1; then
            echo "  settings registered at /Analyzer/SampicDQM"
            exit 0
        fi
        sleep 0.1
    done
    echo "  WARNING: /Analyzer/SampicDQM did not appear; check $LOG" >&2
    exit 1
fi
exec "$FS_PYTHON" -m fakesampic.analyzer -e "$FS_EXPT_NAME"
