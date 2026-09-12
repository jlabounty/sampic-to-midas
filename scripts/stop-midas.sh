#!/bin/bash
# Stop the fakesampic experiment's MIDAS clients.
#
#     scripts/stop-midas.sh [--clean]
#
# --clean also removes the ODB and shared memory, so the next start begins from
# a fresh ODB. It does NOT remove run files under online/data.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

CLEAN=0
[ "${1:-}" = "--clean" ] && CLEAN=1

if [ -f "$FS_EXPT_DIR/.ODB.SHM" ] && fs_have_midas; then
    odbedit -e "$FS_EXPT_NAME" -c "stop now" >/dev/null 2>&1 || true
fi

# Uses fs_pids_matching from fake-sampic-env.sh, which walks /proc and skips
# shells. `pkill -f mhttpd` would match any command line CONTAINING "mhttpd",
# including the shell that invoked this script -- so it would kill its own
# caller.
fs_stop() {
    local label="$1" pat="$2" pids
    pids=$(fs_pids_matching "$pat")
    [ -z "$pids" ] && return 0
    echo "stopping $label ($(echo $pids | tr '\n' ' '))"
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 50); do
        pids=$(fs_pids_matching "$pat")
        [ -z "$pids" ] && return 0
        sleep 0.1
    done
    pids=$(fs_pids_matching "$pat")
    [ -n "$pids" ] && { echo "  forcing $label"; kill -9 $pids 2>/dev/null || true; }
    return 0
}

# PLAIN SUBSTRINGS, not regexes: fs_pids_matching does a shell glob compare
# against the command line, so a pattern like "python.* -m fakesampic" matches
# nothing at all -- and silently, leaving the process running.
fs_stop "frontend" "fakesampic.frontend"
fs_stop "analyzer" "fakesampic.analyzer"
fs_stop "mlogger"  "mlogger -e $FS_EXPT_NAME"
fs_stop "mhttpd"   "mhttpd -e $FS_EXPT_NAME"

if [ "$CLEAN" = 1 ]; then
    echo "removing ODB and shared memory"
    rm -f "$FS_EXPT_DIR"/.*.SHM "$FS_EXPT_DIR"/.ODB* 2>/dev/null || true
    # MIDAS names its POSIX shared memory after the experiment directory, and
    # those segments outlive `rm -rf online/`. Left behind, the next odbinit
    # meets a stale segment of the wrong size and fails confusingly.
    for seg in /dev/shm/*"${FS_EXPT_NAME}"*; do
        [ -e "$seg" ] && rm -f "$seg" && echo "  removed $seg"
    done
fi
echo "done"
