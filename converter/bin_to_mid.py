#!/usr/bin/env python3
"""Convert a SAMPIC standalone .bin file into a fake MIDAS .mid file.

One MIDAS event = one time-gap cluster of hits (a new event starts when the
gap between consecutive FirstSampleTimeStamps exceeds --gap-ns). Each physics
event carries an AD00 bank (all hits, pi_midas SAMPIC format) and an AT00
event-timing bank. The result feeds the existing Gaudi chain:
PIMidasSelector -> PIMidasDecoder(PITMidasSampic) -> PIAOutputStream.

Memory use is bounded (~tens of MB) regardless of input size: the input is
mmap'ed and processed in --chunk-hits chunks, with the trailing (possibly
incomplete) cluster carried over so no event is split at a chunk boundary.
"""

import argparse
import os
import re
import sys
import time

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from converter import event_builder, sampic_banks, sampic_bin
    from converter.midas_writer import MidasFileWriter, TID_BYTE
else:
    from . import event_builder, sampic_banks, sampic_bin
    from .midas_writer import MidasFileWriter, TID_BYTE

PHYSICS_EVENT_ID = 1


def run_number_from_name(path: str) -> int:
    m = re.search(r"run(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else 0


def convert(args) -> int:
    t_start = time.monotonic()
    with sampic_bin.BinFile(args.input) as bf:
        header = bf.header
        fe_board_index = (args.fe_board_index if args.fe_board_index is not None
                          else header.fe_board_index)
        run_number = (args.run_number if args.run_number is not None
                      else run_number_from_name(args.input))
        if args.verbose:
            print(f"header: {header.software_version}, "
                  f"{header.sampling_freq_msps} MS/s, "
                  f"channel mask 0x{header.enabled_channels_mask:08x}, "
                  f"{header.header_bytes} header bytes")
            print(f"layout: fixed={bf.fixed_layout} "
                  f"n_samples={bf.n_samples} n_hits={bf.n_hits()}")

        order = None
        if bf.fixed_layout:
            t0 = bf.t0_column()
            sorted_ok = event_builder.is_sorted(t0)
            if not sorted_ok:
                if args.sort == "assume":
                    print("ERROR: timestamps are not sorted (and --sort assume)",
                          file=sys.stderr)
                    return 1
                print("note: timestamps not monotonic, sorting hit index")
                order = np.argsort(t0, kind="stable")
            elif args.sort == "force":
                order = np.argsort(t0, kind="stable")
        elif args.sort != "assume":
            print("note: variable-size layout, sortedness is assumed "
                  "(slow path)", file=sys.stderr)

        def chunks():
            nonlocal n_hits_read
            if order is not None:
                it = (bf.hits[order[s:s + args.chunk_hits]]
                      for s in range(0, len(order), args.chunk_hits))
            else:
                it = bf.iter_chunks(args.chunk_hits)
            for chunk in it:
                if args.max_hits is not None:
                    remaining = args.max_hits - n_hits_read
                    if remaining <= 0:
                        return
                    chunk = chunk[:remaining]
                n_hits_read += len(chunk)
                yield chunk

        n_hits_read = 0
        n_hits_written = 0
        n_events = 0
        hits_per_event_min = None
        hits_per_event_max = 0
        with MidasFileWriter(args.output) as writer:
            if not args.no_bor_eor:
                writer.write_bor(run_number, int(header.unix_time))

            serial = 0

            def flush_batch(batch):
                """Write one MIDAS event per cluster, converting the batch once.

                `build_ad_records` is elementwise, so how its input is grouped
                cannot change a single output byte -- but calling it per event
                would pay numpy's per-call overhead once per cluster, which for
                a file like run914 (42k single-hit events) dominates everything
                else. Convert the whole batch, then slice it back apart.
                """
                nonlocal serial, n_events, n_hits_written
                nonlocal hits_per_event_min, hits_per_event_max
                if not batch:
                    return
                joined = np.concatenate(batch) if len(batch) > 1 else batch[0]
                ad_all = sampic_banks.build_ad_records(
                    joined, fe_board_index,
                    header.inl_corrected, header.adc_corrected)
                off = 0
                for cluster in batch:
                    n = len(cluster)
                    ad = ad_all[off:off + n]
                    off += n
                    t0_first = cluster["t0"][0]
                    banks = [(sampic_banks.AD_BANK_NAME, TID_BYTE, ad.tobytes())]
                    if not args.no_timing_bank:
                        banks.append((sampic_banks.AT_BANK_NAME, TID_BYTE,
                                      sampic_banks.build_at_payload(t0_first, n)))
                    writer.write_event(PHYSICS_EVENT_ID, 0, serial,
                                       int(header.unix_time + t0_first * 1e-9),
                                       banks)
                    serial += 1
                    n_events += 1
                    n_hits_written += n
                    hits_per_event_min = n if hits_per_event_min is None \
                        else min(hits_per_event_min, n)
                    hits_per_event_max = max(hits_per_event_max, n)

            batch = []
            batch_hits = 0
            n_clusters = 0
            for cluster in event_builder.iter_clusters(chunks(), args.gap_ns):
                batch.append(cluster)
                batch_hits += len(cluster)
                n_clusters += 1
                if args.max_events is not None and n_clusters >= args.max_events:
                    break
                if batch_hits >= args.chunk_hits:
                    flush_batch(batch)
                    batch, batch_hits = [], 0
            flush_batch(batch)

            if not args.no_bor_eor:
                writer.write_eor(run_number, int(time.time()))
            mb = writer.bytes_written / 1e6

    elapsed = time.monotonic() - t_start
    mean = n_hits_written / n_events if n_events else float("nan")
    print(f"{args.input} -> {args.output}")
    print(f"  run {run_number}: {n_hits_written} hits -> {n_events} events "
          f"(gap {args.gap_ns} ns)")
    print(f"  hits/event min/mean/max = {hits_per_event_min}/{mean:.2f}/"
          f"{hits_per_event_max}")
    print(f"  wrote {mb:.1f} MB in {elapsed:.2f} s")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="SAMPIC .bin/.dat input file")
    p.add_argument("-o", "--output", required=True,
                   help="output MIDAS file (use a .mid extension)")
    p.add_argument("--gap-ns", type=float, default=100.0,
                   help="time gap starting a new event (default 100 ns)")
    p.add_argument("--run-number", type=int, default=None,
                   help="run number for BOR/EOR (default: 'runNNN' from filename)")
    p.add_argument("--max-events", type=int, default=None,
                   help="stop after N MIDAS events (smoke tests)")
    p.add_argument("--max-hits", type=int, default=None,
                   help="read at most N hits from the input")
    p.add_argument("--fe-board-index", type=int, default=None,
                   help="override the FE board index from the file header")
    p.add_argument("--no-bor-eor", action="store_true",
                   help="do not write begin/end-of-run marker events")
    p.add_argument("--no-timing-bank", action="store_true",
                   help="do not write AT00 event-timing banks")
    p.add_argument("--chunk-hits", type=int, default=65536,
                   help="hits processed per chunk (default 65536)")
    p.add_argument("--sort", choices=("auto", "assume", "force"), default="auto",
                   help="timestamp ordering policy (default auto: check, "
                        "sort only if needed)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if not args.output.endswith(".mid"):
        print("warning: output should end in .mid (midasio picks its reader "
              "by extension)", file=sys.stderr)
    return convert(args)


if __name__ == "__main__":
    sys.exit(main())
