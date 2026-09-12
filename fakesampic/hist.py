"""Histograms and waveform persistence, in numpy.

No MIDAS here: the analyzer that fills these is a MIDAS client, but the
accumulators themselves are plain objects so the whole analysis can be exercised
offline against a .mid file, and unit-tested on a machine with no MIDAS.

NO LOCKING, and none is needed. The analyzer fills from its event loop and the
brpc callback reads from inside `client.communicate()` -- the same thread. A
lock here would imply a concurrency that does not exist and would be one more
thing to keep correct.

Bin counts are float64 rather than integer because a future weighted fill (say,
by amplitude) should not silently truncate, and 8 bytes per bin is nothing at
these sizes.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class Hist1D:
    name: str
    nbins: int
    xlow: float
    xhigh: float
    xlabel: str = ""
    title: str = ""
    counts: np.ndarray = field(default=None, repr=False)
    entries: int = 0
    underflow: float = 0.0
    overflow: float = 0.0

    def __post_init__(self):
        if self.nbins < 1:
            raise ValueError(f"{self.name}: nbins must be >= 1, got {self.nbins}")
        if not self.xhigh > self.xlow:
            raise ValueError(f"{self.name}: need xhigh > xlow, got {self.xlow}..{self.xhigh}")
        if self.counts is None:
            self.counts = np.zeros(self.nbins, dtype=np.float64)

    def fill(self, values, weights=None) -> None:
        v = np.atleast_1d(np.asarray(values, dtype=np.float64))
        if v.size == 0:
            return
        w = None if weights is None else np.atleast_1d(
            np.asarray(weights, dtype=np.float64))
        # Count out-of-range separately rather than clipping into the edge bins:
        # a spike in the first bin that is really "everything below range" is a
        # genuinely misleading plot.
        below = v < self.xlow
        above = v >= self.xhigh
        inside = ~(below | above)
        self.underflow += float(below.sum() if w is None else w[below].sum())
        self.overflow += float(above.sum() if w is None else w[above].sum())
        if inside.any():
            scale = self.nbins / (self.xhigh - self.xlow)
            idx = ((v[inside] - self.xlow) * scale).astype(np.int64)
            np.clip(idx, 0, self.nbins - 1, out=idx)
            self.counts += np.bincount(
                idx, weights=None if w is None else w[inside], minlength=self.nbins)
        self.entries += int(v.size)

    def reset(self) -> None:
        self.counts[:] = 0.0
        self.entries = 0
        self.underflow = self.overflow = 0.0

    @property
    def edges(self) -> np.ndarray:
        return np.linspace(self.xlow, self.xhigh, self.nbins + 1)


@dataclass
class Hist2D:
    name: str
    nx: int
    xlow: float
    xhigh: float
    ny: int
    ylow: float
    yhigh: float
    xlabel: str = ""
    ylabel: str = ""
    title: str = ""
    counts: np.ndarray = field(default=None, repr=False)
    entries: int = 0

    def __post_init__(self):
        if self.nx < 1 or self.ny < 1:
            raise ValueError(f"{self.name}: nx and ny must be >= 1")
        if not (self.xhigh > self.xlow and self.yhigh > self.ylow):
            raise ValueError(f"{self.name}: need high > low on both axes")
        if self.counts is None:
            self.counts = np.zeros((self.ny, self.nx), dtype=np.float64)

    def fill(self, xs, ys) -> None:
        x = np.atleast_1d(np.asarray(xs, dtype=np.float64))
        y = np.atleast_1d(np.asarray(ys, dtype=np.float64))
        if x.size == 0:
            return
        if x.size != y.size:
            raise ValueError(f"{self.name}: x and y differ in length ({x.size} vs {y.size})")
        keep = ((x >= self.xlow) & (x < self.xhigh) &
                (y >= self.ylow) & (y < self.yhigh))
        self.entries += int(x.size)
        if not keep.any():
            return
        ix = ((x[keep] - self.xlow) * (self.nx / (self.xhigh - self.xlow))).astype(np.int64)
        iy = ((y[keep] - self.ylow) * (self.ny / (self.yhigh - self.ylow))).astype(np.int64)
        np.clip(ix, 0, self.nx - 1, out=ix)
        np.clip(iy, 0, self.ny - 1, out=iy)
        # One bincount over the flattened index: a 2D histogram filled per point
        # in Python would dominate the analyzer's cost at kHz rates.
        flat = np.bincount(iy * self.nx + ix, minlength=self.nx * self.ny)
        self.counts += flat.reshape(self.ny, self.nx)

    def reset(self) -> None:
        self.counts[:] = 0.0
        self.entries = 0


class Persistence:
    """The last N waveforms of each channel, as raw int16 samples.

    A ring buffer rather than a 2D density histogram, because the question being
    asked -- "show me the last N pulses on this strip" -- is answered by the
    traces themselves: you can see a single misshapen pulse, which a density
    plot averages away. Density is the right tool for millions of events; this
    is the right tool for spotting what one channel is doing now.

    int16 is what the format stores, so keeping counts rather than volts halves
    the memory and the wire size and loses nothing (volts = counts / 1e4).
    """

    def __init__(self, n_channels: int, depth: int = 20, n_samples: int = 64):
        if depth < 1:
            raise ValueError(f"persistence depth must be >= 1, got {depth}")
        self.n_channels = int(n_channels)
        self.depth = int(depth)
        self.n_samples = int(n_samples)
        self._buf = np.zeros((self.n_channels, self.depth, self.n_samples), dtype="<i2")
        self._n = np.zeros(self.n_channels, dtype=np.int64)     # total ever added
        self._baseline = np.zeros((self.n_channels, self.depth), dtype="<f4")

    def add(self, channels, waveforms, baselines=None) -> None:
        """Append one waveform per entry. Later entries overwrite older ones."""
        ch = np.atleast_1d(np.asarray(channels, dtype=np.int64))
        wf = np.atleast_2d(np.asarray(waveforms))
        if ch.size == 0:
            return
        if wf.shape[0] != ch.size:
            raise ValueError(f"got {ch.size} channels but {wf.shape[0]} waveforms")
        keep = (ch >= 0) & (ch < self.n_channels)
        n_src = min(wf.shape[1], self.n_samples)
        for k in np.flatnonzero(keep):
            c = int(ch[k])
            slot = int(self._n[c] % self.depth)
            self._buf[c, slot, :n_src] = wf[k, :n_src]
            if n_src < self.n_samples:
                self._buf[c, slot, n_src:] = 0
            if baselines is not None:
                self._baseline[c, slot] = baselines[k]
            self._n[c] += 1

    def count(self, channel: int) -> int:
        """How many slots of this channel's buffer are filled."""
        if not 0 <= channel < self.n_channels:
            return 0
        return int(min(self._n[channel], self.depth))

    def waveforms(self, channel: int) -> np.ndarray:
        """This channel's stored waveforms, OLDEST FIRST so a page can fade them."""
        n = self.count(channel)
        if n == 0:
            return np.zeros((0, self.n_samples), dtype="<i2")
        if n < self.depth:
            return self._buf[channel, :n]
        start = int(self._n[channel] % self.depth)
        return np.concatenate((self._buf[channel, start:], self._buf[channel, :start]))

    def baselines(self, channel: int) -> np.ndarray:
        n = self.count(channel)
        if n == 0:
            return np.zeros(0, dtype="<f4")
        if n < self.depth:
            return self._baseline[channel, :n]
        start = int(self._n[channel] % self.depth)
        return np.concatenate((self._baseline[channel, start:],
                               self._baseline[channel, :start]))

    def filled_channels(self) -> List[int]:
        return np.flatnonzero(self._n > 0).tolist()

    def reset(self) -> None:
        self._buf[:] = 0
        self._n[:] = 0
        self._baseline[:] = 0


class HistStore:
    """Named histograms plus the persistence buffer."""

    def __init__(self):
        self._h: Dict[str, object] = {}
        self.persistence: Optional[Persistence] = None

    def add(self, hist):
        self._h[hist.name] = hist
        return hist

    def get(self, name):
        return self._h.get(name)

    def names(self) -> List[str]:
        return sorted(self._h.keys())

    def reset_all(self) -> None:
        for h in self._h.values():
            h.reset()
        if self.persistence is not None:
            self.persistence.reset()

    def __contains__(self, name):
        return name in self._h

    def __len__(self):
        return len(self._h)
