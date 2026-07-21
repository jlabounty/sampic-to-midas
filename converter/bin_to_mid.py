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
            if order is not None:
                for start in range(0, len(order), args.chunk_hits):
                    yield bf.hits[order[start:start + args.chunk_hits]]
            else:
                yield from bf.iter_chunks(args.chunk_hits)

        n_hits_read = 0
        n_hits_written = 0
        n_events = 0
        hits_per_event_min = None
        hits_per_event_max = 0
        with MidasFileWriter(args.output) as writer:
            if not args.no_bor_eor:
                writer.write_bor(run_number, int(header.unix_time))

            # carry: AD records + t0s of the trailing unfinished cluster
            carry_ad = np.empty(0, dtype=sampic_banks.AD_HIT_DTYPE)
            carry_t0 = np.empty(0, dtype="<f8")
            serial = 0
            done = False

            def flush(ad, t0s):
                nonlocal serial, n_events, n_hits_written
                nonlocal hits_per_event_min, hits_per_event_max
                banks = [(sampic_banks.AD_BANK_NAME, TID_BYTE, ad.tobytes())]
                if not args.no_timing_bank:
                    banks.append((sampic_banks.AT_BANK_NAME, TID_BYTE,
                                  sampic_banks.build_at_payload(t0s[0], len(ad))))
                writer.write_event(PHYSICS_EVENT_ID, 0, serial,
                                   int(header.unix_time + t0s[0] * 1e-9), banks)
                serial += 1
                n_events += 1
                n_hits_written += len(ad)
                n = len(ad)
                hits_per_event_min = n if hits_per_event_min is None \
                    else min(hits_per_event_min, n)
                hits_per_event_max = max(hits_per_event_max, n)

            for chunk in chunks():
                if done:
                    break
                if args.max_hits is not None:
                    remaining = args.max_hits - n_hits_read
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining]
                n_hits_read += len(chunk)

                ad = sampic_banks.build_ad_records(
                    chunk, fe_board_index,
                    header.inl_corrected, header.adc_corrected)
                t0s = np.concatenate((carry_t0, chunk["t0"].astype("<f8")))
                ad = np.concatenate((carry_ad, ad)) if len(carry_ad) else ad

                starts = event_builder.cluster_starts(t0s, args.gap_ns)
                # keep the last cluster as carry: the next chunk may continue it
                for i in range(len(starts) - 1):
                    flush(ad[starts[i]:starts[i + 1]], t0s[starts[i]:starts[i + 1]])
                    if args.max_events is not None and n_events >= args.max_events:
                        done = True
                        break
                if done:
                    carry_ad = np.empty(0, dtype=sampic_banks.AD_HIT_DTYPE)
                    carry_t0 = np.empty(0, dtype="<f8")
                    break
                carry_ad = ad[starts[-1]:].copy() if len(starts) else ad[:0]
                carry_t0 = t0s[starts[-1]:].copy() if len(starts) else t0s[:0]

            if len(carry_ad) and not done:
                if args.max_events is None or n_events < args.max_events:
                    flush(carry_ad, carry_t0)

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
