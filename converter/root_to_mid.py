#!/usr/bin/env python3
"""Convert already-unpacked SAMPIC data (a dat_to_root ROOT file with the
`wfms` TTree) into a fake MIDAS .mid file — the ROOT-input sibling of
bin_to_mid.py, reusing the same event builder, AD/AT bank builders and MIDAS
writer.

The wfms tree stores WaveformData in volts (double, sample/1e4); it is
converted back to the raw int16 counts (exact inverse) so the AD-bank builder
and validators share one code path with the .bin converter.

Multi-channel trees are generally NOT time-ordered; hits are sorted by
StartTime before clustering. The requested entry slice is held in memory as
compact packed records (~161 B/hit, i.e. ~370 MB for a full 2.3M-hit run);
use --entry-stop / --max-hits to work on a slice first.

Requires uproot + numpy (run in the pioneer container or any env with both).
"""

import argparse
import os
import sys
import time

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from converter import event_builder, sampic_banks
    from converter.bin_to_mid import PHYSICS_EVENT_ID, run_number_from_name
    from converter.midas_writer import MidasFileWriter, TID_BYTE
    from converter.sampic_bin import hit_dtype
else:
    from . import event_builder, sampic_banks
    from .bin_to_mid import PHYSICS_EVENT_ID, run_number_from_name
    from .midas_writer import MidasFileWriter, TID_BYTE
    from .sampic_bin import hit_dtype

BRANCHES = ["HitNumber", "Channel", "StartTime", "RawToTValue", "TOTValue",
            "TimeMeasure", "BaselineMeasure", "AmplitudeMeasure",
            "FirstCellIndex", "WaveformSize", "WaveformData"]

MAX_SAMPLES = 64


def parse_header_unix_time(rootfile) -> float:
    """Best-effort unix time from the TBRunHeader date/time strings."""
    import datetime
    import re

    try:
        hdr = rootfile["TBRunHeader"].all_members
        date = str(hdr.get("date", "")).strip()          # e.g. 2026.1.11
        tstr = str(hdr.get("time", "")).strip()          # e.g. 17h.14m.29s.726ms
        y, mo, d = (int(x) for x in date.split("."))
        m = re.match(r"(\d+)h\.(\d+)m\.(\d+)s", tstr)
        hh, mm, ss = (int(g) for g in m.groups())
        return datetime.datetime(y, mo, d, hh, mm, ss).timestamp()
    except Exception:
        return 0.0


def load_hits(tree, entry_start, entry_stop, chunk_entries, verbose):
    """Stream the requested slice into a packed hit_dtype(64) array."""
    import awkward as ak

    n_total = tree.num_entries
    stop = min(entry_stop if entry_stop is not None else n_total, n_total)
    start = entry_start or 0
    n = max(0, stop - start)
    out = np.zeros(n, dtype=hit_dtype(MAX_SAMPLES))
    pos = 0
    for chunk in tree.iterate(BRANCHES, entry_start=start, entry_stop=stop,
                              step_size=chunk_entries):
        m = len(chunk["Channel"])
        rec = out[pos:pos + m]
        rec["hit_number"] = ak.to_numpy(chunk["HitNumber"])
        rec["channel"] = ak.to_numpy(chunk["Channel"])
        rec["t0"] = ak.to_numpy(chunk["StartTime"])
        rec["raw_tot"] = ak.to_numpy(chunk["RawToTValue"])
        rec["tot"] = ak.to_numpy(chunk["TOTValue"])
        rec["time"] = ak.to_numpy(chunk["TimeMeasure"])
        rec["baseline"] = ak.to_numpy(chunk["BaselineMeasure"])
        rec["amplitude"] = ak.to_numpy(chunk["AmplitudeMeasure"])
        rec["first_cell"] = ak.to_numpy(chunk["FirstCellIndex"])
        wf_size = ak.to_numpy(chunk["WaveformSize"])
        if (wf_size > MAX_SAMPLES).any():
            raise RuntimeError(f"WaveformSize > {MAX_SAMPLES} in tree")
        rec["wf_size"] = wf_size
        # volts (double) -> raw int16 counts, the exact inverse of counts/1e4
        wf_v = ak.to_numpy(ak.fill_none(
            ak.pad_none(chunk["WaveformData"], MAX_SAMPLES, axis=-1), 0.0))
        counts = np.rint(wf_v * 1.0e4)
        if np.abs(counts - wf_v * 1.0e4).max() > 1e-3:
            raise RuntimeError(
                "WaveformData does not look like int16 counts / 1e4 volts")
        rec["wf"] = counts.astype("<i2")
        pos += m
        if verbose:
            print(f"  read {pos}/{n} hits", file=sys.stderr)
    return out


def convert(args) -> int:
    import uproot

    t_start = time.monotonic()
    f = uproot.open(args.input)
    tree = f[args.tree]
    unix_time = args.unix_time if args.unix_time is not None \
        else parse_header_unix_time(f)
    run_number = (args.run_number if args.run_number is not None
                  else run_number_from_name(args.input))

    hits = load_hits(tree, args.entry_start, args.entry_stop,
                     args.chunk_entries, args.verbose)
    if args.max_hits is not None:
        hits = hits[:args.max_hits]
    if args.verbose:
        print(f"loaded {len(hits)} hits from {args.tree} "
              f"(tree has {tree.num_entries})")

    t0 = hits["t0"].copy()
    if not event_builder.is_sorted(t0):
        order = np.argsort(t0, kind="stable")
        hits = hits[order]
        t0 = t0[order]
        if args.verbose:
            print("sorted hits by StartTime")

    ad = sampic_banks.build_ad_records(hits, args.fe_board_index,
                                       args.inl_corrected, args.adc_corrected)
    starts = event_builder.cluster_starts(t0, args.gap_ns)
    bounds = np.concatenate((starts, [len(t0)]))
    n_events = len(starts)
    if args.max_events is not None:
        n_events = min(n_events, args.max_events)

    sizes = []
    with MidasFileWriter(args.output) as writer:
        if not args.no_bor_eor:
            writer.write_bor(run_number, int(unix_time))
        for i in range(n_events):
            s, e = bounds[i], bounds[i + 1]
            banks = [(sampic_banks.AD_BANK_NAME, TID_BYTE, ad[s:e].tobytes())]
            if not args.no_timing_bank:
                banks.append((sampic_banks.AT_BANK_NAME, TID_BYTE,
                              sampic_banks.build_at_payload(t0[s], e - s)))
            writer.write_event(PHYSICS_EVENT_ID, 0, i,
                               int(unix_time + t0[s] * 1e-9), banks)
            sizes.append(e - s)
        if not args.no_bor_eor:
            writer.write_eor(run_number, int(time.time()))
        mb = writer.bytes_written / 1e6

    sizes = np.array(sizes)
    elapsed = time.monotonic() - t_start
    print(f"{args.input} -> {args.output}")
    print(f"  run {run_number}: {sizes.sum()} hits -> {n_events} events "
          f"(gap {args.gap_ns} ns)")
    if len(sizes):
        print(f"  hits/event min/mean/max = {sizes.min()}/{sizes.mean():.2f}/"
              f"{sizes.max()}")
    print(f"  wrote {mb:.1f} MB in {elapsed:.2f} s")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="dat_to_root ROOT file with a wfms TTree")
    p.add_argument("-o", "--output", required=True,
                   help="output MIDAS file (use a .mid extension)")
    p.add_argument("--tree", default="wfms")
    p.add_argument("--gap-ns", type=float, default=100.0,
                   help="time gap starting a new event (default 100 ns)")
    p.add_argument("--entry-start", type=int, default=None,
                   help="first tree entry to read")
    p.add_argument("--entry-stop", type=int, default=None,
                   help="stop before this tree entry (slice large files)")
    p.add_argument("--max-hits", type=int, default=None)
    p.add_argument("--max-events", type=int, default=None,
                   help="write at most N MIDAS events")
    p.add_argument("--run-number", type=int, default=None,
                   help="default: 'runNNN' from the filename")
    p.add_argument("--unix-time", type=float, default=None,
                   help="run start (default: parse TBRunHeader date/time)")
    p.add_argument("--fe-board-index", type=int, default=0)
    p.add_argument("--inl-corrected", action="store_true", default=True)
    p.add_argument("--adc-corrected", action="store_true", default=True)
    p.add_argument("--no-bor-eor", action="store_true")
    p.add_argument("--no-timing-bank", action="store_true")
    p.add_argument("--chunk-entries", type=int, default=100000)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if not args.output.endswith(".mid"):
        print("warning: output should end in .mid", file=sys.stderr)
    return convert(args)


if __name__ == "__main__":
    sys.exit(main())
