#!/usr/bin/env python3
"""Round-trip validator for root_to_mid.py: assert a produced .mid file is a
faithful re-packaging of the source dat_to_root wfms TTree slice.

Mirrors tools/validate_midas.py (the .bin validator): independent re-parse of
the .mid, independent re-read + sort + clustering of the tree, explicit
field-by-field comparison. Exit 0 = all PASS.
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter import event_builder  # noqa: E402
from converter.mid_reader import decode_ad, decode_at, iter_events  # noqa: E402
from converter.midas_writer import BANK_HEADER_FLAGS_32A, TID_BYTE  # noqa: E402
from converter.root_to_mid import load_hits  # noqa: E402

FAILURES = []


def check(section, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {section}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(section)


def main(argv=None) -> int:
    import uproot

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root_file")
    p.add_argument("mid_file")
    p.add_argument("--tree", default="wfms")
    p.add_argument("--gap-ns", type=float, default=100.0)
    p.add_argument("--entry-start", type=int, default=None)
    p.add_argument("--entry-stop", type=int, default=None)
    p.add_argument("--max-hits", type=int, default=None)
    p.add_argument("--no-timing-bank", action="store_true")
    args = p.parse_args(argv)

    src = load_hits(uproot.open(args.root_file)[args.tree],
                    args.entry_start, args.entry_stop, 100000, False)
    if args.max_hits is not None:
        src = src[:args.max_hits]
    t0 = src["t0"].copy()
    if not event_builder.is_sorted(t0):
        order = np.argsort(t0, kind="stable")
        src = src[order]
        t0 = t0[order]
    starts = event_builder.cluster_starts(t0, args.gap_ns)
    bounds = np.concatenate((starts, [len(t0)]))

    print("== 1. structure ==")
    events = [ev for ev in iter_events(args.mid_file)]
    check("all events parse cleanly", True, f"{len(events)} physics events")
    check("bank header flags 0x31", all(
        ev.flags == BANK_HEADER_FLAGS_32A for ev in events))
    expected = ["AD00"] if args.no_timing_bank else ["AD00", "AT00"]
    check("banks per event", all(
        [b[0] for b in ev.banks] == expected
        and all(b[1] == TID_BYTE for b in ev.banks) for ev in events))

    print("== 2. counts ==")
    ad_arrays = [decode_ad(dict((b[0], b[2]) for b in ev.banks)["AD00"])
                 for ev in events]
    n_mid = sum(len(a) for a in ad_arrays)
    # the .mid may hold fewer events than the slice clusters to (--max-events)
    n_exp = sum(bounds[i + 1] - bounds[i] for i in range(len(events)))
    check("event count <= recomputed clusters", len(events) <= len(starts),
          f"mid {len(events)} vs clusters {len(starts)}")
    check("total hits match covered clusters", n_mid == n_exp,
          f"mid {n_mid} vs tree {n_exp}")

    print("== 3. hit-level equality ==")
    field_fail = None
    cursor = 0
    for i, ad in enumerate(ad_arrays):
        s = src[cursor:cursor + len(ad)]
        cursor += len(ad)
        exp_wf = (s["wf"] / np.float64(1.0e4)).astype("<f4")
        checks = [
            ("channel", ad["channel"] == s["channel"]),
            ("hit_number", ad["hit_number"] == s["hit_number"]),
            ("data_size", ad["data_size"] == s["wf_size"]),
            ("first_cell", ad["first_cell_physical_index"] == s["first_cell"]),
            ("raw_tot", ad["raw_tot_value"] == s["raw_tot"]),
            ("tot", ad["tot_value"].view("<u4") == s["tot"].view("<u4")),
            ("amplitude",
             ad["amplitude"].view("<u4") == s["amplitude"].view("<u4")),
            ("baseline",
             ad["baseline"].view("<u4") == s["baseline"].view("<u4")),
            ("time_instant", ad["time_instant"] == s["time"].astype("<f8")),
            ("t0", ad["first_cell_timestamp"].view("<u8") == s["t0"].view("<u8")),
            ("waveform", ad["waveform"].view("<u4") == exp_wf.view("<u4")),
        ]
        for fname, arr in checks:
            if not np.all(arr):
                field_fail = f"event {i}, field {fname}"
                break
        if field_fail:
            break
    check("bitwise field equality", field_fail is None,
          field_fail or f"{n_mid} hits compared")

    print("== 4. event-level ==")
    if not args.no_timing_bank:
        at_fail = None
        for i, ev in enumerate(events):
            at = decode_at(dict((b[0], b[2]) for b in ev.banks)["AT00"])
            s, e = bounds[i], bounds[i + 1]
            if at[1] != e - s or at[0] != int(round(t0[s])):
                at_fail = f"event {i}"
                break
        check("AT nhits and fe_timestamp", at_fail is None, at_fail or "")
    gap_fail = None
    for i in range(len(events)):
        s, e = bounds[i], bounds[i + 1]
        if e - s > 1 and np.diff(t0[s:e]).max() > args.gap_ns:
            gap_fail = f"event {i}: internal gap > {args.gap_ns}"
            break
    check("gap invariant (intra <= gap)", gap_fail is None, gap_fail or "")

    print()
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
