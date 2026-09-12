"""Per-channel occupancy and rates for the DQM equipment.

A plain shared object: the replay equipment adds to it as it produces events,
and the DQM equipment drains it on its own slower period. NO LOCKING, and none
is needed -- `midas.frontend.FrontendBase.run()` calls every equipment's readout
from one thread (frontend.py:897-948). Adding a lock "to be safe" would be
harmless but misleading, implying a concurrency that does not exist.

Arrays are always `N_DAQ_CHANNELS` long whatever the geometry maps, because
mlogger builds its history schema from a bank's length when it starts, and a
bank that changed length when somebody edited the geometry would break history
for every run already recorded. Unmapped channels carry a sentinel instead.
"""

from dataclasses import dataclass

import numpy as np

from .identity import N_DAQ_CHANNELS, NO_READING, UNMAPPED_RATE


@dataclass
class Snapshot:
    rates_hz: np.ndarray
    mean_amplitude_v: np.ndarray
    events: int
    hits: int
    bytes_: int
    elapsed_s: float
    max_hits_per_event: int


class OccupancyAccumulator:
    """Sums hits and amplitudes per channel between DQM periods."""

    def __init__(self, n_channels: int = N_DAQ_CHANNELS):
        self.n_channels = int(n_channels)
        self._mapped = np.zeros(self.n_channels, dtype=bool)
        self.reset()

    def set_mapped(self, channels) -> None:
        """Mark which channels the geometry actually connects.

        Drives the difference between "0 Hz" (a mapped channel that saw nothing,
        which is a detector problem) and "not connected" (which is not).
        """
        self._mapped[:] = False
        idx = np.asarray(channels, dtype=np.int64)
        idx = idx[(idx >= 0) & (idx < self.n_channels)]
        self._mapped[idx] = True

    def reset(self) -> None:
        self._counts = np.zeros(self.n_channels, dtype=np.int64)
        self._amp_sum = np.zeros(self.n_channels, dtype=np.float64)
        self.events = 0
        self.hits = 0
        self.bytes_ = 0
        self.max_hits_per_event = 0

    def add_event(self, hits: np.ndarray, n_bytes: int = 0) -> None:
        """Accumulate one event. `hits` is a source hit array."""
        n = len(hits)
        if n == 0:
            return
        ch = hits["channel"].astype(np.int64)
        # bincount over the whole event rather than a per-hit loop: this runs on
        # every event the frontend produces.
        self._counts += np.bincount(ch, minlength=self.n_channels)[:self.n_channels]
        self._amp_sum += np.bincount(ch, weights=hits["amplitude"].astype(np.float64),
                                     minlength=self.n_channels)[:self.n_channels]
        self.events += 1
        self.hits += n
        self.bytes_ += n_bytes
        if n > self.max_hits_per_event:
            self.max_hits_per_event = n

    def snapshot(self, elapsed_s: float) -> Snapshot:
        """Current rates and mean amplitudes; does not reset."""
        elapsed = max(float(elapsed_s), 1e-9)
        rates = np.where(self._mapped, self._counts / elapsed, UNMAPPED_RATE)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_amp = np.where(self._counts > 0, self._amp_sum / np.maximum(self._counts, 1),
                                NO_READING)
        mean_amp = np.where(self._mapped, mean_amp, NO_READING)
        return Snapshot(rates_hz=rates.astype("<f4"),
                        mean_amplitude_v=mean_amp.astype("<f4"),
                        events=self.events, hits=self.hits, bytes_=self.bytes_,
                        elapsed_s=elapsed,
                        max_hits_per_event=self.max_hits_per_event)

    def drain(self, elapsed_s: float) -> Snapshot:
        snap = self.snapshot(elapsed_s)
        self.reset()
        return snap
