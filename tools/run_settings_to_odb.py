#!/usr/bin/env python3
"""Turn a SAMPIC `run_settings.txt` into a JSON ODB dump.

The SAMPIC standalone DAQ writes its configuration as a `run_settings.txt`
alongside the `.bin`; a MIDAS run instead carries the configuration as a JSON
ODB dump in the begin-of-run event. This converts the former into the latter,
so a converted `.bin` can carry a faithful ODB and the Gaudi chain can resolve
the sampling rate and per-channel thresholds exactly as it will with real
MIDAS data -- rather than falling back to nominal properties.

Feed the result to `bin_to_mid.py --odb-json`, and it becomes the BOR payload
that PIMidasSelector::ReadBOR() parses into a PIODBHeader.

The layout mirrors a MIDAS ODB tree, and the keys under
/Equipment/SAMPIC/Settings match the default JSON pointers of
PIDEMPrelimAnalysis (SamplingRateODBPath, TriggerThresholdODBPath).
"""

import argparse
import json
import re
import sys


def _num(text, pattern, cast=float, default=None):
    m = re.search(pattern, text)
    return cast(m.group(1)) if m else default


def parse(path: str, run_number: int = 0) -> dict:
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    text = "".join(lines)

    n_channels = _num(text, r"NB OF CHANNELS IN SYSTEM\s+(\d+)", int, 0)

    # --- per-channel block --------------------------------------------------
    # "=== SAMPIC-CHANNEL [c] for FE-BLOCK [b] or BOARD-CHANNEL [n] ==="
    # followed by
    # "== CHANNEL_TRIGGER_MODE: 'X' THRESHOLD: v [V] TRIG_EDGE: 'E' ... =="
    thresholds = [None] * n_channels
    modes = [None] * n_channels
    edges = [None] * n_channels
    sources = [None] * n_channels
    fe_block = [None] * n_channels

    for i, line in enumerate(lines):
        m = re.search(r"BOARD-CHANNEL\s*\[(\d+)\]", line)
        if not m:
            continue
        ch = int(m.group(1))
        if ch >= n_channels:
            continue
        blk = re.search(r"FE-BLOCK\s*\[(\d+)\]", line)
        if blk:
            fe_block[ch] = int(blk.group(1))
        params = lines[i + 1] if i + 1 < len(lines) else ""
        thresholds[ch] = _num(params, r"THRESHOLD:\s*(-?[\d.]+)", float)
        mode = re.search(r"CHANNEL_TRIGGER_MODE:\s*'([^']*)'", params)
        modes[ch] = mode.group(1) if mode else None
        edge = re.search(r"TRIG_EDGE:\s*'([^']*)'", params)
        edges[ch] = edge.group(1) if edge else None
        sources[ch] = _num(params, r"SOURCE_FOR_CT:\s*(\d+)", int)

    missing = [c for c, t in enumerate(thresholds) if t is None]
    if missing:
        print(f"warning: no threshold parsed for channels {missing}",
              file=sys.stderr)

    # --- front-end blocks ---------------------------------------------------
    fe_baselines = [float(v) for v in
                    re.findall(r"FE-END-BLOCK\s*\[\d+\].*?BASELINE VALUE:\s*([\d.]+)",
                               text)]

    msps = _num(text, r"SAMPLING FREQUENCY:?\s*(\d+)", int)
    if msps is None:
        raise SystemExit("no SAMPLING FREQUENCY found; is this a run_settings.txt?")

    settings = {
        # --- what PIDEMPrelimAnalysis reads by default ----------------------
        # GS/s, matching NominalSamplingGSPS and SamplingRateODBPath.
        "SamplingFrequencyGSPS": msps / 1000.0,
        # Signed as the DAQ writes it; the analysis takes the magnitude.
        "TriggerThresholds": thresholds,

        # --- the rest of the configuration, carried for completeness --------
        "SamplingFrequencyMSPS": msps,
        "NumberOfChannels": n_channels,
        "NumberOfSamplesToRead": _num(text, r"NB_OF_SAMPLES_TO_READ:\s*(\d+)", int),
        "SmartRead": _num(text, r"SMART_READ:\s*(\d+)", int),
        "OffsetForStartOfRead": _num(text, r"OFFSET_FOR_START_OF_READ:\s*(\d+)", int),
        "ChannelTriggerModes": modes,
        "TriggerEdges": edges,
        "SourceForCT": sources,
        "ChannelFEBlock": fe_block,
        "FEBlockBaselineValue": fe_baselines,
        "ExternalTriggerType": _num(text, r"EXTERNAL_TRIGGER_TYPE:\s*(\d+)", int),
        "ExtSigLevel": _num(text, r"EXT_SIG_LEVEL:\s*(\d+)", int),
        "ExtSigEdge": _num(text, r"EXT_SIG_EDGE:\s*(\d+)", int),
        "ExtTrigGateNs": _num(text, r"EXT_TRIG_GATE:\s*(\d+)", int),
        "EnablePostrig": _num(text, r"ENABLE_POSTRIG:\s*(\d+)", int),
        "PostrigValue": _num(text, r"POSTRIG_VALUE:\s*(\d+)", int),
        "EnableL2CoincidenceMode": _num(text, r"EN_L2_COINCIDENCE_MODE:\s*(\d+)", int),
        "L2Logic": _num(text, r"L2_LOGIC:\s*(\d+)", int),
        "L2PrimitivesGateNs": _num(text, r"L2_PRIMITIVES_GATE:\s*(\d+)", int),
        "L2LatencyGateNs": _num(text, r"L2_LATENCY_GATE:\s*(\d+)", int),
        "CtrlFirmwareVersion": (re.search(r"CTRL FPGA FIRMWARE VERSION\s+(\S+)", text)
                                or [None, None])[1]
        if re.search(r"CTRL FPGA FIRMWARE VERSION\s+(\S+)", text) else None,
    }

    return {
        "Equipment": {"SAMPIC": {"Settings": settings}},
        "Runinfo": {
            # PIConditionsSvc reads this, via PIODBRunInfo, to pick the IOV row
            # of every conditions table; without it the service refuses to
            # initialise. The SAMPIC standalone DAQ has no notion of a run
            # number -- there is nothing in run_settings.txt to take it from --
            # so it is supplied on the command line and defaults to 0.
            "Run number": run_number,
            "UnixTime": _num(text, r"UnixTime\s*=\s*([\d.]+)", float),
            "Date": (re.search(r"date\s*=\s*(\S+)", text) or [None, None])[1]
            if re.search(r"date\s*=\s*(\S+)", text) else None,
            "SoftwareVersion": (re.search(r"SOFTWARE VERSION:\s*(\S+)", text)
                                or [None, None])[1]
            if re.search(r"SOFTWARE VERSION:\s*(\S+)", text) else None,
        },
        # Provenance: this is a stand-in for a real ODB, not a dump of one.
        "_source": f"generated by run_settings_to_odb.py from {path}",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="run_settings.txt")
    p.add_argument("-o", "--output", required=True, help="output .json")
    p.add_argument("--indent", type=int, default=1)
    p.add_argument("--run-number", type=int, default=0,
                   help="value for /Runinfo/Run number, which PIConditionsSvc "
                        "uses to select the IOV row of each conditions table "
                        "(default: 0)")
    args = p.parse_args(argv)

    odb = parse(args.input, run_number=args.run_number)
    with open(args.output, "w") as f:
        json.dump(odb, f, indent=args.indent)
        f.write("\n")

    s = odb["Equipment"]["SAMPIC"]["Settings"]
    thr = [t for t in s["TriggerThresholds"] if t is not None]
    print(f"{args.input} -> {args.output}")
    print(f"  {s['SamplingFrequencyGSPS']} GS/s ({s['SamplingFrequencyMSPS']} MS/s), "
          f"{s['NumberOfSamplesToRead']} samples")
    print(f"  {len(thr)}/{s['NumberOfChannels']} channel thresholds, "
          f"{min(thr):+.4f} .. {max(thr):+.4f} V")
    print(f"  run number {odb['Runinfo']['Run number']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
