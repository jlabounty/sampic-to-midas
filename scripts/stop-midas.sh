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

for name in fake-sampic-fe mlogger mhttpd; do
    if pgrep -u "$(id -u)" -f "$name" >/dev/null 2>&1; then
        echo "stopping $name"
        pkill -u "$(id -u)" -f "$name" || true
    fi
done

for _ in $(seq 1 50); do
    pgrep -u "$(id -u)" -f "mhttpd -e $FS_EXPT_NAME" >/dev/null 2>&1 || break
    sleep 0.1
done
pkill -9 -u "$(id -u)" -f "mhttpd -e $FS_EXPT_NAME" 2>/dev/null || true

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
