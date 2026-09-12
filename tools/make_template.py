#!/usr/bin/env python3
"""Build a canonical pulse shape from real or generated data.

    tools/make_template.py run914.mid           -o config/pulse_template.json
    tools/make_template.py run914.bin --gap-ns 100
    tools/make_template.py --synthetic 2000     -o config/pulse_template.json

The result is a normalised shape -- baseline subtracted, unit peak -- averaged
over many pulses with their peaks aligned. The analyzer compares live pulses
against it and histograms how far each one departs, which is how a channel that
has started producing the wrong SHAPE gets noticed: amplitude and timing can
both look perfectly normal while the pulse is a reflection or a saturated
preamp.

Pulses below --min-amplitude are dropped. Normalising a pulse that is mostly
noise divides noise by a small number and produces a large, meaningless shape
that would dominate the average.
"""

import argparse
import os
import sys

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.mid_reader import decode_ad, iter_events
from converter.sampic_banks import ADC_TO_VOLTS
from converter import sampic_bin
from fakesampic.template import PulseTemplate


def from_mid(path, max_hits, channels):
    """Waveforms as raw int16 counts, plus the per-hit baseline and amplitude."""
    wfs, bases, amps = [], [], []
    for ev in iter_events(path):
        if ev.is_special:
            continue
        for name, tid, data in ev.banks:
            if name != "AD00":
                continue
            ad = decode_ad(data)
            keep = np.ones(len(ad), dtype=bool) if channels is None else \
                np.isin(ad["channel"], channels)
            if not keep.any():
                continue
            # The AD00 waveform is volts; the template works in raw counts so
            # that it is built the same way from a .bin and from a .mid.
            wfs.append(np.rint(ad["waveform"][keep] * ADC_TO_VOLTS).astype("<i2"))
            bases.append(ad["baseline"][keep])
            amps.append(ad["amplitude"][keep])
        if sum(w.shape[0] for w in wfs) >= max_hits:
            break
    if not wfs:
        raise SystemExit(f"no AD00 hits found in {path}")
    return (np.concatenate(wfs)[:max_hits], np.concatenate(bases)[:max_hits],
            np.concatenate(amps)[:max_hits])


def from_bin(path, max_hits, channels):
    wfs, bases, amps = [], [], []
    total = 0
    with sampic_bin.BinFile(path) as bf:
        for chunk in bf.iter_chunks(16384):
            keep = np.ones(len(chunk), dtype=bool) if channels is None else \
                np.isin(chunk["channel"], channels)
            if keep.any():
                wfs.append(np.asarray(chunk["wf"][keep]))
                bases.append(chunk["baseline"][keep].astype(np.float64))
                amps.append(chunk["amplitude"][keep].astype(np.float64))
                total += int(keep.sum())
            if total >= max_hits:
                break
    if not wfs:
        raise SystemExit(f"no hits found in {path}")
    return (np.concatenate(wfs)[:max_hits], np.concatenate(bases)[:max_hits],
            np.concatenate(amps)[:max_hits])


def from_synthetic(n_hits, seed):
    """A template from the generator itself, for a detector that has no data yet."""
    from fakesampic.waveform import PulseParams, generate
    rng = np.random.default_rng(seed or None)
    p = PulseParams()
    amps = np.abs(rng.normal(p.amplitude_v, p.amplitude_v * p.amplitude_spread, n_hits))
    offs = rng.uniform(-p.dt_ns / 2, p.dt_ns / 2, n_hits)
    return generate(amps.clip(0.005, None), offs, p, rng)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help=".mid or .bin file to average")
    ap.add_argument("--synthetic", type=int, metavar="N",
                    help="build from N generated pulses instead of a file")
    ap.add_argument("-o", "--output", default="config/pulse_template.json")
    ap.add_argument("--name", default=None)
    ap.add_argument("--max-hits", type=int, default=5000)
    ap.add_argument("--channels", default="",
                    help="comma-separated channel list (default: all)")
    ap.add_argument("--min-amplitude", type=float, default=0.005,
                    help="volts; smaller pulses are excluded (default 0.005)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    channels = None
    if args.channels.strip():
        channels = [int(c) for c in args.channels.replace(" ", "").split(",")]

    if args.synthetic:
        wf, base, amp = from_synthetic(args.synthetic, args.seed)
        source = f"synthetic, {args.synthetic} generated pulses"
        name = args.name or "synthetic"
    elif args.input and args.input.endswith(".mid"):
        wf, base, amp = from_mid(args.input, args.max_hits, channels)
        source = os.path.basename(args.input)
        name = args.name or os.path.splitext(source)[0]
    elif args.input:
        wf, base, amp = from_bin(args.input, args.max_hits, channels)
        source = os.path.basename(args.input)
        name = args.name or os.path.splitext(source)[0]
    else:
        ap.error("give an input file or --synthetic N")

    t = PulseTemplate.from_waveforms(wf, base, amp, name=name, source=source,
                                     min_amplitude_v=args.min_amplitude)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    t.save(args.output)

    rms = t.compare_batch(wf[:500], base[:500], amp[:500], window=12)
    print(f"{args.output}")
    print(f"  '{t.name}' from {t.source}")
    print(f"  {t.n_samples} samples, peak at {t.peak_index}, "
          f"averaged over {t.n_averaged} pulses")
    # Report the LARGEST spread and where it sits, rather than the spread at the
    # peak. For synthetic hits the latter is ~0 by construction, because the
    # generator derives `amplitude` from the sampled maximum. For real SAMPIC
    # hits it is not: the board reports its own fitted amplitude, which differs
    # from max(waveform), and near-threshold pulses can have their argmax land
    # on noise so the alignment misses. A large spread AT the peak is therefore
    # a sign the input was dominated by pulses too small to be worth averaging
    # -- raise --min-amplitude.
    worst = int(np.argmax(t.spread))
    print(f"  largest shape spread: {float(t.spread[worst]):.4f} of peak height "
          f"at sample {worst} (peak is at {t.peak_index})")
    print(f"  input pulses vs this template: rms {rms.mean():.4f} "
          f"(median {np.median(rms):.4f}, worst {rms.max():.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
