"""What the analyzer accumulates from each event.

Kept free of MIDAS so the whole analysis can be run over a .mid file offline and
unit-tested without an experiment. `fakesampic.analyzer` is the thin MIDAS
client that feeds this.

Every fill is vectorised across the hits of an event. At a few kHz with ~24 hits
an event that is ~100k hits a second; a Python loop over hits anywhere in here
would be the entire CPU budget.

The histogram ranges are deliberately fixed rather than auto-scaled. A histogram
whose range moves is not comparable between runs or between channels, and the
under/overflow counters exist precisely so a badly chosen range announces itself
instead of silently clipping.
"""

from typing import Dict, List, Optional

import numpy as np

from converter.sampic_banks import ADC_TO_VOLTS

from .hist import Hist1D, Hist2D, HistStore, Persistence
from .template import PulseTemplate


class SampicAnalysis:
    """Accumulates histograms and persistence from decoded AD00 hits."""

    def __init__(self, geometry=None, n_channels: int = 128,
                 persistence_depth: int = 20, n_samples: int = 64,
                 template: Optional[PulseTemplate] = None,
                 amp_max_v: float = 0.20, template_window: int = 12):
        self.geometry = geometry
        self.n_channels = int(n_channels)
        self.n_samples = int(n_samples)
        self.template = template
        self.template_window = int(template_window)
        self.store = HistStore()
        self.events = 0
        self.hits = 0

        s = self.store
        n_planes = geometry.n_planes if geometry is not None else 1
        n_strips = geometry.n_strips if geometry is not None else 1

        s.add(Hist1D("amp/all", 200, 0.0, amp_max_v, xlabel="amplitude [V]",
                     title="Pulse amplitude, all channels"))
        s.add(Hist1D("amp/baseline", 200, 0.0, 1.5, xlabel="baseline [V]",
                     title="Baseline, all channels"))
        s.add(Hist1D("mult/hits_per_event", 128, 0, 128, xlabel="hits",
                     title="Hits per event"))
        s.add(Hist1D("mult/planes_per_event", n_planes + 1, 0, n_planes + 1,
                     xlabel="planes hit", title="Planes hit per event"))
        s.add(Hist1D("time/spread_ns", 200, 0.0, 20.0, xlabel="max-min t0 [ns]",
                     title="Time spread within an event"))
        s.add(Hist1D("occ/channel", self.n_channels, 0, self.n_channels,
                     xlabel="channel", title="Occupancy by DAQ channel"))
        for p in range(n_planes):
            name = geometry.planes[p].name if geometry is not None else str(p)
            s.add(Hist1D(f"amp/plane_{name}", 200, 0.0, amp_max_v,
                         xlabel="amplitude [V]", title=f"Pulse amplitude, plane {name}"))

        s.add(Hist2D("occ/plane_vs_strip", n_strips, 0, n_strips, n_planes, 0, n_planes,
                     xlabel="strip", ylabel="plane", title="Occupancy, plane vs strip"))
        # The classic persistence display: every sample of every hit, stacked.
        # Ranges chosen around the measured run914 baseline of 0.746 V.
        s.add(Hist2D("shape/persistence", self.n_samples, 0, self.n_samples,
                     220, 0.70, 0.92, xlabel="sample", ylabel="volts",
                     title="Persistence, all channels"))

        if template is not None:
            self._add_template_hists(amp_max_v)

        s.persistence = Persistence(self.n_channels, persistence_depth, self.n_samples)

    def _add_template_hists(self, amp_max_v: float) -> None:
        self.store.add(Hist1D("shape/template_rms", 200, 0.0, 0.5,
                              xlabel="rms deviation [fraction of peak]",
                              title="Departure from the canonical pulse shape"))
        self.store.add(Hist2D("shape/rms_vs_amp", 100, 0.0, amp_max_v, 100, 0.0, 0.5,
                              xlabel="amplitude [V]", ylabel="rms vs template",
                              title="Shape deviation vs amplitude"))

    def set_template(self, template: Optional[PulseTemplate]) -> None:
        """Swap the canonical shape at runtime, adding its histograms if new."""
        self.template = template
        if template is not None and "shape/template_rms" not in self.store:
            self._add_template_hists(0.20)

    # -- filling -------------------------------------------------------------

    def add_event(self, hits: np.ndarray) -> None:
        """`hits` is one event's AD00 records (converter.sampic_banks dtype)."""
        n = len(hits)
        if n == 0:
            return
        self.events += 1
        self.hits += n
        s = self.store

        channels = hits["channel"].astype(np.int64)
        amp = hits["amplitude"].astype(np.float64)
        base = hits["baseline"].astype(np.float64)
        t0 = hits["first_cell_timestamp"].astype(np.float64)
        # AD00 waveforms are volts already (build_ad_records divided by 1e4).
        wf_volts = hits["waveform"]

        s.get("amp/all").fill(amp)
        s.get("amp/baseline").fill(base)
        s.get("mult/hits_per_event").fill([n])
        s.get("time/spread_ns").fill([float(t0.max() - t0.min())])
        s.get("occ/channel").fill(channels)

        if self.geometry is not None:
            planes = self.geometry.channel_plane[np.clip(channels, 0, self.n_channels - 1)]
            strips = self.geometry.channel_strip[np.clip(channels, 0, self.n_channels - 1)]
            mapped = planes >= 0
            if mapped.any():
                s.get("occ/plane_vs_strip").fill(strips[mapped], planes[mapped])
                s.get("mult/planes_per_event").fill([len(np.unique(planes[mapped]))])
                for p in np.unique(planes[mapped]):
                    name = self.geometry.planes[int(p)].name
                    h = s.get(f"amp/plane_{name}")
                    if h is not None:
                        h.fill(amp[planes == p])

        # Persistence density: one fill for every sample of every hit, with the
        # sample index broadcast across hits rather than looped.
        n_s = min(wf_volts.shape[1], self.n_samples)
        sample_idx = np.tile(np.arange(n_s, dtype=np.float64), n)
        s.get("shape/persistence").fill(sample_idx, wf_volts[:, :n_s].ravel())

        # Ring buffer keeps raw counts, which is what the format stores and half
        # the wire size of volts.
        raw = np.rint(wf_volts[:, :n_s] * ADC_TO_VOLTS).astype("<i2")
        s.persistence.add(channels, raw, base)

        if self.template is not None:
            rms = self.template.compare_batch(raw, base, amp,
                                              window=self.template_window)
            s.get("shape/template_rms").fill(rms)
            s.get("shape/rms_vs_amp").fill(amp, rms)

    def reset(self) -> None:
        self.store.reset_all()
        self.events = 0
        self.hits = 0

    # -- reporting -----------------------------------------------------------

    def status(self) -> Dict:
        return {
            "events": self.events,
            "hits": self.hits,
            "histograms": len(self.store),
            "n_channels": self.n_channels,
            "persistence_depth": (self.store.persistence.depth
                                  if self.store.persistence else 0),
            "template": None if self.template is None else {
                "name": self.template.name,
                "n_samples": self.template.n_samples,
                "peak_index": self.template.peak_index,
                "n_averaged": self.template.n_averaged,
                "source": self.template.source,
                "created": self.template.created,
            },
        }

    def names(self) -> List[str]:
        return self.store.names()
