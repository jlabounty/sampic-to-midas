"""Generated events for a detector that does not exist yet.

Two models, chosen from the ODB:

parametric
    Each mapped channel fires independently with a fixed probability and gets an
    independent amplitude. Cheap, and right for exercising rates, occupancy and
    the DAQ plumbing. Correlation plots are flat by construction, because there
    is nothing correlating the channels.

track
    A straight track is thrown through the plane stack with a beam profile, and
    each plane it crosses shares charge across neighbouring strips using the
    Gaussian-mixture model from `fakesampic.chargesharing`. Centre-of-gravity
    position reconstruction works on this, planes correlate with each other, and
    the residual distributions have sensible widths -- which is what a DQM page
    being developed against fake data needs in order to look like it is working.

Everything is computed for a whole batch of events at once. A per-event or
per-hit Python loop here would dominate the frontend's cost: the batch is
typically ~100 events of ~24 hits on each readout tick.
"""

from typing import List, Optional, Sequence

import numpy as np

from ..chargesharing import SensorFit, strip_fractions
from ..geometry import Geometry, build_geometry
from ..waveform import PulseParams, generate as generate_waveforms
from .base import (HIT_DTYPE, EventSource, SourceEvent, SourceMeta, SourceStats,
                   HIT_NUMBER_WRAP)

C_MM_PER_NS = 299.792458
"""Speed of light. Plane-to-plane time of flight is z/c -- about 0.23 ns across a
70 mm stack, which is small but not negligible next to a 40 ps time jitter, and
leaving it out would make the planes look simultaneous."""

RAW_TOT_SENTINEL = 65535
TOT_SENTINEL = -1.0
"""Values the SAMPIC standalone format writes when it has no ToT measurement.
Both were measured across every hit of run914, so fake hits carry them too
rather than a plausible-looking number that no real file contains."""


class SyntheticSource(EventSource):
    """Generates LGAD-telescope events from geometry and beam parameters."""

    name = "synthetic"

    def __init__(self, geometry: Optional[Geometry] = None,
                 model: str = "track",
                 pulse: Optional[PulseParams] = None,
                 sensor_fit: Optional[SensorFit] = None,
                 beam_x_mm: float = 0.0, beam_y_mm: float = 0.0,
                 beam_sigma_mm: float = 1.5,
                 beam_divergence_mrad: float = 2.0,
                 charge_threshold: float = 0.02,
                 hit_probability: float = 0.15,
                 time_jitter_ps: float = 40.0,
                 randomize_first_cell: bool = True,
                 raw_tot: int = RAW_TOT_SENTINEL,
                 tot_ns: float = TOT_SENTINEL,
                 fe_board_index: int = 0,
                 seed: int = 0):
        super().__init__(SourceMeta(fe_board_index=fe_board_index,
                                    inl_corrected=True, adc_corrected=True,
                                    sampling_msps=(pulse or PulseParams()).sampling_msps,
                                    unix_time=0.0))
        if model not in ("track", "parametric"):
            raise ValueError(f"model must be 'track' or 'parametric', got {model!r}")
        self.geometry = geometry if geometry is not None else build_geometry()
        self.model = model
        self.pulse = pulse if pulse is not None else PulseParams()
        self.pulse.validate()
        self.fit = sensor_fit if sensor_fit is not None else SensorFit.default()
        self.beam_x_mm = float(beam_x_mm)
        self.beam_y_mm = float(beam_y_mm)
        self.beam_sigma_mm = float(beam_sigma_mm)
        self.beam_divergence = float(beam_divergence_mrad) * 1e-3
        if not 0.0 <= charge_threshold < 1.0:
            raise ValueError(f"charge threshold must be in [0,1), got {charge_threshold}")
        self.charge_threshold = float(charge_threshold)
        if not 0.0 <= hit_probability <= 1.0:
            raise ValueError(f"hit probability must be in [0,1], got {hit_probability}")
        self.hit_probability = float(hit_probability)
        self.time_jitter_ns = float(time_jitter_ps) * 1e-3
        self.randomize_first_cell = bool(randomize_first_cell)
        self.raw_tot = int(raw_tot)
        self.tot_ns = float(tot_ns)
        self._rng = np.random.default_rng(seed or None)
        self._hit_number = 0
        self._stats = SourceStats()

        g = self.geometry
        self._centres = np.stack([p.strip_positions_mm() for p in g.planes])
        self._plane_z = g.plane_z_mm()
        self._is_x = np.array([p.orientation == "X" for p in g.planes])
        self._eff = np.array([p.efficiency for p in g.planes], dtype=np.float64)

    def reset(self, t_ns: float) -> None:
        self._hit_number = 0
        self._stats = SourceStats()

    # -- the two models ------------------------------------------------------

    def _fire_track(self, n_ev: int):
        """(event_idx, plane_idx, strip_idx, charge_fraction) for a batch."""
        rng = self._rng
        g = self.geometry
        x0 = rng.normal(self.beam_x_mm, self.beam_sigma_mm, n_ev)
        y0 = rng.normal(self.beam_y_mm, self.beam_sigma_mm, n_ev)
        tx = rng.normal(0.0, self.beam_divergence, n_ev)
        ty = rng.normal(0.0, self.beam_divergence, n_ev)

        ev_i, pl_i, st_i, frac = [], [], [], []
        for p in range(g.n_planes):
            z = self._plane_z[p]
            u = (x0 + tx * z) if self._is_x[p] else (y0 + ty * z)
            f = strip_fractions(u, self._centres[p], self.fit)   # (n_ev, n_strips)
            keep = f > self.charge_threshold
            if self._eff[p] < 1.0:
                # Inefficiency is per plane per event: a plane either sees the
                # particle or it does not. Dropping individual strips instead
                # would punch holes in a cluster, which no sensor does.
                keep &= (rng.random(n_ev) < self._eff[p])[:, None]
            e, s = np.nonzero(keep)
            if e.size == 0:
                continue
            ev_i.append(e)
            pl_i.append(np.full(e.size, p, dtype=np.int64))
            st_i.append(s)
            frac.append(f[e, s])
        if not ev_i:
            empty = np.empty(0, dtype=np.int64)
            return empty, empty, empty, np.empty(0, dtype=np.float64)
        return (np.concatenate(ev_i), np.concatenate(pl_i),
                np.concatenate(st_i), np.concatenate(frac))

    def _fire_parametric(self, n_ev: int):
        rng = self._rng
        g = self.geometry
        shape = (n_ev, g.n_planes, g.n_strips)
        mapped = g.channel_of >= 0
        keep = (rng.random(shape) < self.hit_probability) & mapped[None, :, :]
        e, p, s = np.nonzero(keep)
        # Full charge on each fired strip: no sharing, by definition of the model.
        return e, p, s, np.ones(e.size, dtype=np.float64)

    # -- production ----------------------------------------------------------

    def next_events(self, times_ns: Sequence[float]) -> List[SourceEvent]:
        times = np.asarray(times_ns, dtype=np.float64)
        n_ev = times.size
        if n_ev == 0:
            return []
        rng = self._rng
        g = self.geometry

        if self.model == "track":
            ev_i, pl_i, st_i, frac = self._fire_track(n_ev)
        else:
            ev_i, pl_i, st_i, frac = self._fire_parametric(n_ev)

        channels = g.channel_of[pl_i, st_i] if ev_i.size else np.empty(0, np.int64)
        if ev_i.size:
            ok = channels >= 0          # strips with no DAQ channel are not read out
            ev_i, pl_i, st_i, frac, channels = (a[ok] for a in
                                                (ev_i, pl_i, st_i, frac, channels))

        n_hits = int(ev_i.size)
        if n_hits == 0:
            # Every event empty is legitimate (a low hit probability, a beam off
            # the edge of the detector) but an event with no hits has no AD00
            # bank to send, so report zero events rather than empty ones.
            return []

        # Per-event MIP amplitude, shared by every strip of that event's clusters.
        mip = np.abs(rng.normal(self.pulse.amplitude_v,
                                self.pulse.amplitude_v * self.pulse.amplitude_spread,
                                n_ev))
        amp = mip[ev_i] * frac

        # Time: event time, plus time of flight to the plane, plus jitter, plus
        # amplitude-dependent time walk (a smaller pulse crosses threshold later).
        tof = self._plane_z[pl_i] / C_MM_PER_NS
        walk = self.pulse.rise_ns * (1.0 - np.clip(frac, 1e-3, 1.0))
        t_hit = (times[ev_i] + tof
                 + rng.normal(0.0, self.time_jitter_ns, n_hits) + walk)

        # Sub-sample offset within the digitisation window, so the pulse does not
        # land on the same sample every time.
        sub = t_hit - np.floor(t_hit / self.pulse.dt_ns) * self.pulse.dt_ns
        wf, baseline_v, amplitude_v = generate_waveforms(amp, sub, self.pulse, rng)

        order = np.lexsort((t_hit, ev_i))       # sort by event, then time within it
        ev_i, channels, t_hit = ev_i[order], channels[order], t_hit[order]
        wf, baseline_v, amplitude_v = wf[order], baseline_v[order], amplitude_v[order]

        hits = np.zeros(n_hits, dtype=HIT_DTYPE)
        hits["hit_number"] = ((self._hit_number + np.arange(n_hits, dtype=np.int64))
                              & HIT_NUMBER_WRAP).astype("<i4")
        hits["channel"] = channels.astype("u1")
        hits["t0"] = t_hit
        hits["raw_tot"] = self.raw_tot
        hits["tot"] = self.tot_ns
        hits["time"] = t_hit.astype("<f4")
        hits["baseline"] = baseline_v
        hits["amplitude"] = amplitude_v
        hits["first_cell"] = (rng.integers(0, self.pulse.n_samples, n_hits)
                              if self.randomize_first_cell else 0)
        hits["wf_size"] = self.pulse.n_samples
        hits["wf"][:, :self.pulse.n_samples] = wf
        self._hit_number += n_hits

        bounds = np.searchsorted(ev_i, np.arange(n_ev + 1))
        out: List[SourceEvent] = []
        for i in range(n_ev):
            lo, hi = int(bounds[i]), int(bounds[i + 1])
            if hi <= lo:
                continue
            chunk = hits[lo:hi]
            out.append(SourceEvent(chunk, float(chunk["t0"][0])))
        self._stats.events += len(out)
        self._stats.hits += n_hits
        return out

    def describe(self) -> str:
        return (f"synthetic ({self.model}): {self.geometry.describe()}, "
                f"beam sigma {self.beam_sigma_mm:g} mm")
