#!/bin/bash
# Turn run-file writing on or off.
#
#     scripts/logging.sh                  # show the current state
#     scripts/logging.sh off              # produce events, write nothing to disk
#     scripts/logging.sh on               # record run files again
#     scripts/logging.sh on --restart-run # ...and apply it immediately
#
# OFF IS THE DEFAULT for this experiment, because its whole purpose is
# developing DQM pages against a live stream. The data is fabricated, so
# recording it has no value -- and it accumulates fast: the 8-plane synthetic
# detector at a few hundred Hz is over a MB/s, a gigabyte an hour of files
# nobody will read. Events still reach the SYSTEM buffer with writing off, so
# every custom page, analyser and bm_receive_event consumer keeps working.
#
# Turn it ON when you actually want a run file -- the byte-identity check in
# docs/RUNNING.md needs one.
#
# A CHANGE ONLY TAKES EFFECT AT THE NEXT BEGIN-OF-RUN.
# mlogger decides what to open when the run starts, so flipping this during a
# run does nothing at all until the run is cycled -- verified: six seconds of
# running after switching it on produced no file, and a stop/start produced one
# immediately. Hence the warning below, and --restart-run to do it for you.
#
# This flips /Logger/Write data, the global switch, rather than the per-channel
# Active flag, so it cannot leave one channel quietly recording.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=fake-sampic-env.sh
source "$SCRIPT_DIR/fake-sampic-env.sh"
fs_require_midas

ACTION="${1:-status}"
RESTART=0
[ "${2:-}" = "--restart-run" ] && RESTART=1

odb() { odbedit -e "$FS_EXPT_NAME" -c "$1" 2>/dev/null; }

# odbedit echoes the command it was given, BEFORE and AFTER the output, and the
# echo contains the key name. An unanchored match therefore returns three
# "values" for one key. Anchor on the start of the line -- only the real value
# row begins with the key name -- and take the first match.
odb_value() {
    # Capture first, then parse. Piping odbedit straight into `awk ... exit`
    # closes the pipe early, odbedit takes a SIGPIPE, and `set -o pipefail`
    # turns that into a script abort -- intermittently, depending on whether
    # odbedit had finished writing, which is the worst kind of bug to inherit.
    local out
    out="$(odb "ls -l \"$1\"" || true)"
    printf '%s\n' "$out" | awk -v k="$2" '$0 ~ "^" k "  " {print $NF; found=1} END{exit !found}' || true
}
write_state() { odb_value "/Logger/Write data" "Write data"; }
run_state()   { odb_value "/Runinfo/State" "State"; }

# /Runinfo/State: 1 stopped, 2 paused, 3 running.
is_running() { [ "$(run_state)" = "3" ]; }

cycle_run() {
    echo "  restarting the run so mlogger picks it up"
    odb "stop now" >/dev/null
    for _ in $(seq 1 50); do is_running || break; sleep 0.1; done
    odb "start now" >/dev/null
    for _ in $(seq 1 50); do is_running && break; sleep 0.1; done
    echo "  run $(odb 'ls -l /Runinfo' | awk '/^Run number/{print $NF}') started"
}

note_pending() {
    if is_running; then
        if [ "$RESTART" = 1 ]; then
            cycle_run
        else
            echo "  NOTE: a run is active. mlogger only reads this at begin-of-run,"
            echo "        so nothing changes until the run is cycled:"
            echo "          scripts/logging.sh $ACTION --restart-run"
            echo "        or stop and start the run yourself."
        fi
    fi
}

case "$ACTION" in
    on)
        odb 'set "/Logger/Write data" y' >/dev/null
        echo "run-file writing ON  -> $FS_DATA_DIR"
        note_pending
        echo "  remember to turn it off again when you go back to page work."
        ;;
    off)
        odb 'set "/Logger/Write data" n' >/dev/null
        echo "run-file writing OFF (events still flow to the SYSTEM buffer, so"
        echo "  every custom page and any analyser keeps working)"
        note_pending
        ;;
    status)
        s="$(write_state)"
        used="$(du -sh "$FS_DATA_DIR" 2>/dev/null | cut -f1 || true)"
        # Count with a nullglob array, not `ls | wc -l`. With no matching files
        # ls exits 2, and `set -o pipefail` makes that the pipeline's status,
        # which `set -e` turns into the script exiting -- silently, and only
        # once the directory happens to be empty, which is exactly when someone
        # is most likely to be running this.
        shopt -s nullglob
        local_files=("$FS_DATA_DIR"/*.mid "$FS_DATA_DIR"/*.mid.lz4)
        shopt -u nullglob
        n=${#local_files[@]}
        if [ "$s" = "y" ]; then
            echo "run-file writing is ON   ($n file(s), $used in $FS_DATA_DIR)"
        else
            echo "run-file writing is OFF  ($n file(s), $used in $FS_DATA_DIR)"
        fi
        if is_running; then
            echo "  a run is active; a change now would wait for the next begin-of-run"
        fi
        ;;
    *)
        echo "usage: $(basename "$0") [on|off|status] [--restart-run]" >&2; exit 2 ;;
esac
