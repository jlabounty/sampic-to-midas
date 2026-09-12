#!/usr/bin/env python3
"""Write a .mid file from any source configuration, with no MIDAS installed.

    python -m fakesampic.offline --source synthetic --events 1000 -o fake.mid

This exists for two reasons. It is how the generator is developed and tested on
a machine with no MIDAS; and in `--no-restamp` mode it is the byte-identity gate
--

    python -m converter.bin_to_mid  run914.bin -o ref.mid --gap-ns 100
    python -m fakesampic.offline --source binfile --files run914.bin \\
           --no-restamp --no-loop --gap-ns 100 -o fe.mid
    tools/compare_mid.py ref.mid fe.mid

-- which proves that the replay source's events are the offline converter's
events. Every other mode reuses the same `fakesampic.banks.build_batch` and
`converter.sampic_banks` conversion, so proving this one proves the rest.
"""

import argparse
import os
import sys
import time

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.midas_writer import MidasFileWriter

from fakesampic import banks as fsbanks
from fakesampic.identity import PHYSICS_EVENT_ID
from fakesampic.sources.factory import build_source, source_kinds

BATCH = 512
"""Events converted per `build_batch` call. Large enough to amortise numpy's
per-call cost, small enough that memory stays bounded on a 256-hit mixer."""


def generate(args) -> int:
    t_start = time.monotonic()
    source = build_source(args)
    source.open()
    try:
        writer = MidasFileWriter(args.output)
        with writer:
            if not args.no_bor_eor:
                writer.write_bor(args.run_number, int(source.meta.unix_time))

            serial = 0
            n_hits = 0
            per_event_min = None
            per_event_max = 0
            t_ns = 0.0
            interval = 1e9 / args.rate_hz

            while serial < args.events:
                want = min(BATCH, args.events - serial)
                # Offline there is no real clock to pace against: arrival times
                # are simply the requested rate. The live frontend replaces this
                # with fakesampic.schedule.
                times = t_ns + np.arange(want, dtype=np.float64) * interval
                evs = source.next_events(times)
                if not evs:
                    break
                t_ns = float(times[len(evs) - 1]) + interval

                for ev, bank_list in zip(evs, fsbanks.build_batch(
                        evs, source.meta, timing_bank=not args.no_timing_bank)):
                    n = len(ev.hits)
                    writer.write_event(
                        PHYSICS_EVENT_ID, 0, serial,
                        int(source.meta.unix_time + ev.t0_first_ns * 1e-9),
                        bank_list)
                    serial += 1
                    n_hits += n
                    per_event_min = n if per_event_min is None else min(per_event_min, n)
                    per_event_max = max(per_event_max, n)

            if not args.no_bor_eor:
                writer.write_eor(args.run_number, int(time.time()))
            mb = writer.bytes_written / 1e6
    finally:
        source.close()

    elapsed = time.monotonic() - t_start
    mean = n_hits / serial if serial else float("nan")
    print(f"-> {args.output}")
    print(f"  {source.describe()}")
    print(f"  {n_hits} hits -> {serial} events")
    print(f"  hits/event min/mean/max = {per_event_min}/{mean:.2f}/{per_event_max}")
    print(f"  wrote {mb:.1f} MB in {elapsed:.2f} s ({serial / max(elapsed, 1e-9):.0f} ev/s)")
    return 0


def add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", choices=source_kinds(), default="binfile")
    p.add_argument("--events", type=int, default=1000,
                   help="events to write (default 1000)")
    p.add_argument("--rate-hz", type=float, default=1000.0,
                   help="arrival rate used for timestamps (default 1000)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--run-number", type=int, default=1)
    p.add_argument("--no-bor-eor", action="store_true")
    p.add_argument("--no-timing-bank", action="store_true")
    p.add_argument("--seed", type=int, default=0)

    g = p.add_argument_group("binfile")
    g.add_argument("--files", nargs="+", default=[])
    g.add_argument("--gap-ns", type=float, default=100.0)
    g.add_argument("--no-loop", action="store_true")
    g.add_argument("--loop-gap-ns", type=float, default=1.0e6)
    g.add_argument("--shuffle-files", action="store_true")
    g.add_argument("--start-event", type=int, default=0)
    g.add_argument("--max-events-per-pass", type=int, default=0)
    g.add_argument("--no-restamp", action="store_true",
                   help="keep the file's own timestamps and hit numbers "
                        "(the byte-identity gate; see the module docstring)")

    g = p.add_argument_group("synthetic")
    g.add_argument("--model", choices=("track", "parametric"), default="track")
    g.add_argument("--planes", type=int, default=8)
    g.add_argument("--strips", type=int, default=10)
    g.add_argument("--pitch-mm", type=float, default=0.5)
    g.add_argument("--beam-sigma-mm", type=float, default=1.5)
    g.add_argument("--beam-divergence-mrad", type=float, default=2.0)
    g.add_argument("--hit-probability", type=float, default=0.15)
    g.add_argument("--charge-threshold", type=float, default=0.02)
    g.add_argument("--amplitude-v", type=float, default=0.060)
    g.add_argument("--amplitude-spread", type=float, default=0.25)
    g.add_argument("--noise-v", type=float, default=0.0015)
    g.add_argument("--efficiency", type=float, default=0.98)

    g = p.add_argument_group("mixer")
    g.add_argument("--mix", nargs="+", default=["binfile", "synthetic"],
                   help="child source kinds to overlay")
    g.add_argument("--mix-mode", choices=("overlay", "pileup"), default="overlay")
    g.add_argument("--pileup-mu", type=float, default=0.5)
    g.add_argument("--mix-jitter-ns", type=float, default=0.0)
    g.add_argument("--mix-channel-offset", type=int, nargs="+", default=None)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_source_args(p)
    args = p.parse_args(argv)
    if args.source == "binfile" and not args.files:
        p.error("--source binfile needs --files")
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())
