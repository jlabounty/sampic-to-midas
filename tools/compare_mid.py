#!/usr/bin/env python3
"""Compare two .mid files by their bank payloads alone.

    tools/compare_mid.py a.mid b.mid

Compares the ordered sequence of (bank name, type id, payload bytes) across all
physics events and reports the first difference.

Event headers are deliberately NOT compared. A file written by mlogger from a
live frontend and a file written offline by bin_to_mid cannot agree on
timestamps (one is wall-clock at the moment of sending) or necessarily on serial
numbers, and requiring them to would make the comparison useless for its actual
purpose. The bank sequence IS the contract with the Gaudi unpacker: it is what
PIMidasDecoder reads, and nothing else about the file reaches an analysis.

Special events (BOR/EOR) are skipped on both sides for the same reason.

A live run file also carries events from OTHER equipment -- this frontend's DQM
equipment writes FSRT/FSAM/FSST into the same buffer, and a real experiment has
several frontends -- so --event-id selects the stream to compare. Without it,
"identical" would depend on how many unrelated equipments happened to be running.
"""

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from converter.mid_reader import iter_events


def bank_stream(path, event_id=None):
    """Yield (event_index, bank_name, tid, payload) for the selected events."""
    idx = 0
    for ev in iter_events(path):
        if ev.is_special:
            continue
        if event_id is not None and ev.event_id != event_id:
            continue
        for name, tid, data in ev.banks:
            yield idx, name, tid, bytes(data)
        idx += 1


def compare(path_a: str, path_b: str, max_report: int = 5,
            event_id: int = None) -> int:
    a, b = bank_stream(path_a, event_id), bank_stream(path_b, event_id)
    n_banks = 0
    n_events = 0
    problems = []
    while True:
        ia = next(a, None)
        ib = next(b, None)
        if ia is None and ib is None:
            break
        if ia is None or ib is None:
            longer = path_b if ia is None else path_a
            problems.append(f"{os.path.basename(longer)} has more banks "
                            f"(after {n_banks} matching)")
            break
        n_banks += 1
        n_events = max(n_events, ia[0] + 1)
        if ia[1:] != ib[1:]:
            if ia[1] != ib[1]:
                why = f"name {ia[1]!r} vs {ib[1]!r}"
            elif ia[2] != ib[2]:
                why = f"tid {ia[2]} vs {ib[2]}"
            else:
                pa, pb = ia[3], ib[3]
                if len(pa) != len(pb):
                    why = f"payload {len(pa)} vs {len(pb)} bytes"
                else:
                    off = next(i for i in range(len(pa)) if pa[i] != pb[i])
                    why = (f"payload differs at byte {off} of {len(pa)}: "
                           f"0x{pa[off]:02x} vs 0x{pb[off]:02x}")
            problems.append(f"event {ia[0]} bank {ia[1]}: {why}")
            if len(problems) >= max_report:
                break

    print(f"{path_a}\n{path_b}")
    which = "physics" if event_id is None else f"event-id-{event_id}"
    print(f"  compared {n_banks} banks across {n_events} {which} events")
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
        print("\nFILES DIFFER")
        return 1
    print("\nBANK STREAMS IDENTICAL")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max-report", type=int, default=5)
    p.add_argument("--event-id", type=int, default=None,
                   help="compare only events with this event ID (e.g. 1 for the "
                        "physics stream, excluding other equipment's events)")
    args = p.parse_args(argv)
    return compare(args.a, args.b, args.max_report, args.event_id)


if __name__ == "__main__":
    sys.exit(main())
