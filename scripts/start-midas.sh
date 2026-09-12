#!/bin/bash
# Create (if needed) and start the self-contained fakesampic MIDAS experiment.
#
#     scripts/start-midas.sh
#
# Idempotent: safe to run when it is already up. Creates online/ with its own
# exptab, ODB and history; starts mhttpd and mlogger. mlogger is what actually
# writes history and run files -- without it /Equipment updates but nothing is
# recorded, which is a confusing way to have no data.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"

fs_require_midas

mkdir -p "$FS_EXPT_DIR" "$FS_DATA_DIR"

if [ ! -f "$MIDAS_EXPTAB" ]; then
    echo "$FS_EXPT_NAME $FS_EXPT_DIR $(whoami)" > "$MIDAS_EXPTAB"
    echo "created $MIDAS_EXPTAB"
fi

# Refuse to run against anything but our own directory. Cheap insurance against a
# stray MIDAS_EXPTAB in the environment pointing at a real experiment -- this
# frontend fabricates events, and they must never reach one.
if ! grep -q "^$FS_EXPT_NAME $FS_EXPT_DIR " "$MIDAS_EXPTAB"; then
    echo "ERROR: $MIDAS_EXPTAB does not define $FS_EXPT_NAME at $FS_EXPT_DIR" >&2
    exit 1
fi

# Create the ODB explicitly rather than letting the first client make one: a
# client that creates it picks the small default size, and every later client
# then refuses to attach ("shared memory size N is smaller than database size M").
if [ ! -f "$FS_EXPT_DIR/.ODB.SHM" ]; then
    echo "creating ODB ($FS_ODB_SIZE)"
    ( cd "$FS_EXPT_DIR" && odbinit -e "$FS_EXPT_NAME" -s "$FS_ODB_SIZE" --cleanup )
fi

start_one() {
    local name="$1"; shift
    if fs_is_running "$name"; then
        echo "$name already running"
        return 0
    fi
    echo "starting $name"
    ( cd "$FS_EXPT_DIR" && "$@" ) || {
        echo "ERROR: $name failed to start" >&2; return 1; }
}

# The port is an ODB setting, not a command-line flag, in this MIDAS series:
# "-p" selects the obsolete web server and exits with a message instead of
# starting, and there is no --http. Set it before mhttpd starts so a non-default
# FS_MHTTPD_PORT takes effect on the first run rather than the second.
odbedit -e "$FS_EXPT_NAME" -c \
    "set \"/WebServer/localhost port\" $FS_MHTTPD_PORT" >/dev/null 2>&1 || true

start_one mhttpd mhttpd -e "$FS_EXPT_NAME" -D
start_one mlogger mlogger -e "$FS_EXPT_NAME" -D

# Poll for readiness rather than sleeping: odbedit failing because mhttpd has not
# finished attaching is a race, not an error, and a fixed sleep is either too
# short on a loaded machine or wasted time on an idle one.
for _ in $(seq 1 50); do
    if odbedit -e "$FS_EXPT_NAME" -c "ls /System/Clients" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done

# Event size and buffer depth. The defaults are 4 MB / 32 MB, and an 8-plane
# synthetic event is ~8 kB, so 32 MB of SYSTEM buffer holds well under a second
# at a few kHz -- which shows up as back-pressure rather than as an error.
odbedit -e "$FS_EXPT_NAME" -c 'set "/Experiment/MAX_EVENT_SIZE" 16777216' >/dev/null 2>&1 || true
odbedit -e "$FS_EXPT_NAME" -c 'set "/Experiment/Buffer sizes/SYSTEM" 134217728' >/dev/null 2>&1 || true

# Run files go under online/data, never into the repository.
odbedit -e "$FS_EXPT_NAME" -c "set \"/Logger/Data dir\" \"$FS_DATA_DIR\"" >/dev/null 2>&1 || true

echo
fs_banner
echo
echo "clients:"
odbedit -e "$FS_EXPT_NAME" -c "ls /System/Clients" 2>/dev/null | sed 's/^/  /'
