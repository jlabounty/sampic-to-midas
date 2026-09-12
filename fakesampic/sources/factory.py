"""The one place source kinds are enumerated.

Adding a source means adding it here and nowhere else: the ODB validation, the
offline CLI and the frontend all go through `build_source`.
"""

from typing import List

from ..chargesharing import SensorFit
from ..geometry import build_geometry
from ..identity import N_DAQ_CHANNELS
from ..waveform import PulseParams
from .base import EventSource
from .binfile import BinFileSource
from .mixer import MixerSource
from .synthetic import SyntheticSource

KINDS = ("binfile", "synthetic", "mixer")


def source_kinds() -> List[str]:
    return list(KINDS)


def _binfile(args) -> BinFileSource:
    return BinFileSource(
        files=args.files, gap_ns=args.gap_ns,
        loop=not getattr(args, "no_loop", False),
        loop_gap_ns=args.loop_gap_ns,
        shuffle=getattr(args, "shuffle_files", False),
        start_event=getattr(args, "start_event", 0),
        max_events_per_pass=getattr(args, "max_events_per_pass", 0),
        restamp_times=not getattr(args, "no_restamp", False),
        restamp_hit_number=not getattr(args, "no_restamp", False),
        seed=getattr(args, "seed", 0))


def _synthetic(args) -> SyntheticSource:
    geom = build_geometry(n_planes=args.planes, n_strips=args.strips,
                          pitch_mm=args.pitch_mm,
                          efficiency=[args.efficiency],
                          n_daq_channels=N_DAQ_CHANNELS)
    pulse = PulseParams(amplitude_v=args.amplitude_v,
                        amplitude_spread=args.amplitude_spread,
                        noise_v=args.noise_v)
    return SyntheticSource(
        geometry=geom, model=args.model, pulse=pulse,
        sensor_fit=SensorFit.default(),
        beam_sigma_mm=args.beam_sigma_mm,
        beam_divergence_mrad=args.beam_divergence_mrad,
        charge_threshold=args.charge_threshold,
        hit_probability=args.hit_probability,
        seed=getattr(args, "seed", 0))


def _mixer(args) -> MixerSource:
    children = []
    for kind in args.mix:
        if kind == "mixer":
            raise ValueError("a mixer cannot list itself as a child")
        children.append(build_source(args, kind))
    jitter = [getattr(args, "mix_jitter_ns", 0.0)] * len(children)
    offsets = getattr(args, "mix_channel_offset", None)
    return MixerSource(children, mode=args.mix_mode,
                       pileup_mu=args.pileup_mu, jitter_ns=jitter,
                       channel_offset=offsets, n_daq_channels=N_DAQ_CHANNELS,
                       seed=getattr(args, "seed", 0))


def build_source(args, kind: str = None) -> EventSource:
    kind = kind or args.source
    if kind == "binfile":
        return _binfile(args)
    if kind == "synthetic":
        return _synthetic(args)
    if kind == "mixer":
        return _mixer(args)
    raise ValueError(f"unknown source kind {kind!r}; known: {', '.join(KINDS)}")
