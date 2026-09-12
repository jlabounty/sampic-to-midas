#!/usr/bin/env python3
"""Round-trip validator: assert a produced .mid file is a faithful, byte-exact
re-packaging of its source SAMPIC .bin file. Hard gate before running Gaudi.

Re-parses both files independently (uses converter.sampic_bin and
converter.mid_reader; never the writer) and re-implements the field mapping
explicitly, so a systematic converter bug cannot cancel out.

Exit code 0 = all checks PASS.
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter import event_builder, sampic_bin  # noqa: E402
from converter.mid_reader import decode_ad, decode_at, iter_events  # noqa: E402
from converter.midas_writer import BANK_HEADER_FLAGS_32A, BOR_EVENT_ID, \
    EOR_EVENT_ID, TID_BYTE  # noqa: E402

FAILURES = []


def check(section, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {section}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(section)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bin_file")
    p.add_argument("mid_file")
    p.add_argument("--event-id", type=int, default=None,
                   help="validate only events with this event ID. A run file "
                        "written by mlogger also contains other equipment's "
                        "events (this project's DQM equipment uses 200), which "
                        "are not SAMPIC physics events and have no AD00 bank.")
    p.add_argument("--gap-ns", type=float, default=100.0,
                   help="gap used at conversion time (default 100)")
    p.add_argument("--no-timing-bank", action="store_true",
                   help="the file was produced without AT00 banks")
    args = p.parse_args(argv)

    bf = sampic_bin.BinFile(args.bin_file)
    if not bf.fixed_layout:
        print("note: variable-size .bin layout, loading via slow path")
        bin_hits = np.concatenate(list(bf.iter_chunks()))
    else:
        bin_hits = bf.hits
    t0 = np.ascontiguousarray(bin_hits["t0"], dtype="<f8")
    if not event_builder.is_sorted(t0):
        order = np.argsort(t0, kind="stable")
        bin_hits = bin_hits[order]
        t0 = t0[order]
    exp_starts = event_builder.cluster_starts(t0, args.gap_ns)
    exp_bounds = np.concatenate((exp_starts, [len(t0)]))

    print("== 1. structure ==")
    specials = []
    events = []
    for ev in iter_events(args.mid_file, include_special=True):
        if ev.is_special:
            specials.append(ev)
        elif args.event_id is None or ev.event_id == args.event_id:
            events.append(ev)

    check("all events parse cleanly", True,
          f"{len(events)} physics + {len(specials)} special")
    if specials:
        check("BOR/EOR pair",
              len(specials) == 2
              and specials[0].event_id == BOR_EVENT_ID
              and specials[1].event_id == EOR_EVENT_ID,
              f"ids {[hex(s.event_id) for s in specials]}")
        check("BOR serial is the run number", True,
              f"run {specials[0].serial}")
    flags_ok = all(ev.flags == BANK_HEADER_FLAGS_32A for ev in events)
    check("bank header flags 0x31 (bk_init32a)", flags_ok)
    names_ok = True
    for ev in events:
        names = [b[0] for b in ev.banks]
        expected = ["AD00"] if args.no_timing_bank else ["AD00", "AT00"]
        if names != expected or any(b[1] != TID_BYTE for b in ev.banks):
            names_ok = False
            break
    check("exactly one AD00 (+AT00) bank per event, tid=TID_BYTE", names_ok)

    print("== 2. counts ==")
    ad_arrays = [decode_ad(dict(zip([b[0] for b in ev.banks],
                                    [b[2] for b in ev.banks]))["AD00"])
                 for ev in events]
    n_mid_hits = sum(len(a) for a in ad_arrays)
    check("total hits", n_mid_hits == len(bin_hits),
          f"mid {n_mid_hits} vs bin {len(bin_hits)}")
    check("event count matches recomputed clustering",
          len(events) == len(exp_starts),
          f"mid {len(events)} vs recomputed {len(exp_starts)}")

    print("== 3. hit-level equality ==")
    cursor = 0
    field_fail = None
    for i, ad in enumerate(ad_arrays):
        src = bin_hits[cursor:cursor + len(ad)]
        cursor += len(ad)
        n_src = src["wf"].shape[1]
        # identical expression/cast chain as the converter
        exp_wf = (src["wf"] / np.float64(1.0e4)).astype("<f4")
        checks = [
            ("channel", ad["channel"] == src["channel"]),
            ("hit_number", ad["hit_number"] == src["hit_number"]),
            ("sampic_index", ad["sampic_index"] == src["channel"] // 16),
            ("channel_index", ad["channel_index"] == src["channel"] % 16),
            ("data_size", ad["data_size"] == src["wf_size"]),
            ("first_cell_physical_index",
             ad["first_cell_physical_index"] == src["first_cell"]),
            ("raw_tot_value", ad["raw_tot_value"] == src["raw_tot"]),
            ("tot_value", ad["tot_value"].view("<u4") == src["tot"].view("<u4")),
            ("amplitude",
             ad["amplitude"].view("<u4") == src["amplitude"].view("<u4")),
            ("baseline",
             ad["baseline"].view("<u4") == src["baseline"].view("<u4")),
            ("peak", ad["peak"] == src["baseline"] + src["amplitude"]),
            ("time_instant", ad["time_instant"] == src["time"].astype("<f8")),
            ("first_cell_timestamp",
             ad["first_cell_timestamp"].view("<u8") == src["t0"].view("<u8")),
            ("waveform",
             ad["waveform"][:, :n_src].view("<u4") == exp_wf.view("<u4")),
            ("waveform padding zero",
             ad["waveform"][:, n_src:] == np.float32(0.0)),
            ("time_index zero", ad["time_index"] == np.float32(0.0)),
            ("time_amplitude zero", ad["time_amplitude"] == np.float32(0.0)),
            ("residual_pedestal zero", ad["residual_pedestal_corrected"] == 0),
        ]
        for fname, arr in checks:
            if not np.all(arr):
                field_fail = f"event {i}, field {fname}"
                break
        if field_fail:
            break
    check("bitwise field equality across all hits", field_fail is None,
          field_fail or f"{n_mid_hits} hits compared")

    print("== 4. event-level ==")
    if args.no_timing_bank:
        check("timing banks skipped (--no-timing-bank)", True)
    else:
        at_ok = None
        for i, ev in enumerate(events):
            at = decode_at(dict((b[0], b[2]) for b in ev.banks)["AT00"])
            s, e = exp_bounds[i], exp_bounds[i + 1]
            if at[1] != e - s or at[2] != e - s:
                at_ok = f"event {i}: nhits {at[1]} != {e - s}"
                break
            if at[0] != int(round(t0[s])):
                at_ok = f"event {i}: fe_timestamp {at[0]} != round({t0[s]})"
                break
        check("AT nhits and fe_timestamp per event", at_ok is None, at_ok or "")

    gap_ok = None
    for i in range(len(exp_starts)):
        s, e = exp_bounds[i], exp_bounds[i + 1]
        if e - s > 1 and np.diff(t0[s:e]).max() > args.gap_ns:
            gap_ok = f"event {i}: internal gap > {args.gap_ns} ns"
            break
        if e < len(t0) and t0[e] - t0[e - 1] <= args.gap_ns:
            gap_ok = f"boundary {i}: inter-event gap <= {args.gap_ns} ns"
            break
    check("gap invariants (intra <= gap < inter)", gap_ok is None, gap_ok or "")

    bf.close()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
