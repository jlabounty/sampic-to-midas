#!/usr/bin/env python3
"""Dump the event/bank table of a MIDAS .mid file (debugging aid)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.mid_reader import iter_events  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mid_file")
    p.add_argument("-n", "--max-events", type=int, default=20)
    args = p.parse_args(argv)

    shown = 0
    total = 0
    for ev in iter_events(args.mid_file, include_special=True):
        total += 1
        if shown < args.max_events:
            kind = {0x8000: "BOR", 0x8001: "EOR"}.get(ev.event_id, "phys")
            banks = ", ".join(f"{name}(tid={tid},{len(data)}B)"
                              for name, tid, data in ev.banks) or "-"
            print(f"#{total - 1:<6} {kind:4} id=0x{ev.event_id:04x} "
                  f"serial={ev.serial:<8} ts={ev.time_stamp} "
                  f"size={ev.data_size:<8} banks: {banks}")
            shown += 1
    if total > shown:
        print(f"... ({total} events total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
