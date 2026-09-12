"""A canonical pulse shape, and comparing real pulses against it.

The question this answers is "does this channel's pulse look the way it should?"
-- which amplitude and timing alone cannot, because a pulse can have the right
height and the right arrival time and still be the wrong shape: a reflection, a
saturated preamp, a channel picking up its neighbour, a sensor that has started
to break down.

A template is a NORMALISED shape: baseline subtracted and divided by amplitude,
so it carries no absolute scale. Comparing normalised shapes is the whole point;
comparing raw pulses would mostly measure how much charge was collected.

Alignment is by integer sample shift of the peak. Sub-sample alignment would be
more precise, but it interpolates -- and interpolation is exactly the operation
that hides a one-sample glitch, which is the kind of thing this is meant to
catch. The shift that was applied is reported so a caller can see it.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from converter.sampic_banks import ADC_TO_VOLTS

FORMAT_VERSION = 1


@dataclass
class PulseTemplate:
    """A unit-peak, baseline-subtracted pulse shape."""

    samples: np.ndarray
    dt_ns: float = 0.15625
    name: str = "template"
    source: str = ""
    created: str = ""
    n_averaged: int = 0
    spread: Optional[np.ndarray] = field(default=None, repr=False)

    def __post_init__(self):
        self.samples = np.asarray(self.samples, dtype=np.float64)
        if self.samples.ndim != 1 or self.samples.size < 4:
            raise ValueError("a template needs a 1-D array of at least 4 samples")
        peak = float(np.max(np.abs(self.samples)))
        if peak <= 0:
            raise ValueError("template is all zeros")

    @property
    def peak_index(self) -> int:
        return int(np.argmax(np.abs(self.samples)))

    @property
    def n_samples(self) -> int:
        return int(self.samples.size)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> Dict:
        d = {
            "format": FORMAT_VERSION,
            "name": self.name,
            "dt_ns": self.dt_ns,
            "n_averaged": self.n_averaged,
            "source": self.source,
            "created": self.created or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "peak_index": self.peak_index,
            "samples": [float(x) for x in self.samples],
        }
        if self.spread is not None:
            d["spread"] = [float(x) for x in self.spread]
        return d

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=1)
            f.write("\n")

    @classmethod
    def from_dict(cls, d: Dict) -> "PulseTemplate":
        fmt = int(d.get("format", 0))
        if fmt != FORMAT_VERSION:
            # Refuse rather than guess: a template read with the wrong meaning
            # produces plausible numbers that are quietly wrong.
            raise ValueError(
                f"template format {fmt} is not supported (expected {FORMAT_VERSION})")
        spread = d.get("spread")
        return cls(samples=np.asarray(d["samples"], dtype=np.float64),
                   dt_ns=float(d.get("dt_ns", 0.15625)),
                   name=str(d.get("name", "template")),
                   source=str(d.get("source", "")),
                   created=str(d.get("created", "")),
                   n_averaged=int(d.get("n_averaged", 0)),
                   spread=None if spread is None else np.asarray(spread, dtype=np.float64))

    @classmethod
    def load(cls, path: str) -> "PulseTemplate":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # -- building ------------------------------------------------------------

    @staticmethod
    def normalise(waveforms, baselines, amplitudes) -> np.ndarray:
        """Raw int16 samples -> baseline-subtracted, unit-peak shapes.

        `waveforms` is (n, nsamples) of int16 counts as the AD00 bank stores
        them; `baselines` and `amplitudes` are volts, as the per-hit scalars are.
        """
        wf = np.atleast_2d(np.asarray(waveforms, dtype=np.float64)) / ADC_TO_VOLTS
        base = np.atleast_1d(np.asarray(baselines, dtype=np.float64))[:, None]
        amp = np.atleast_1d(np.asarray(amplitudes, dtype=np.float64))[:, None]
        # A hit with no measurable amplitude has no shape to speak of; leaving
        # it as zeros keeps the array rectangular and contributes nothing to an
        # average.
        safe = np.where(np.abs(amp) < 1e-9, 1.0, amp)
        out = (wf - base) / safe
        out[np.broadcast_to(np.abs(amp) < 1e-9, out.shape)] = 0.0
        return out

    @classmethod
    def from_waveforms(cls, waveforms, baselines, amplitudes,
                       name: str = "template", source: str = "",
                       dt_ns: float = 0.15625,
                       min_amplitude_v: float = 0.005) -> "PulseTemplate":
        """Average many pulses into one shape, aligning their peaks.

        Pulses below `min_amplitude_v` are dropped: normalising a pulse that is
        mostly noise divides noise by a small number and produces a large,
        meaningless shape that would dominate the average.
        """
        wf = np.atleast_2d(np.asarray(waveforms))
        amp = np.atleast_1d(np.asarray(amplitudes, dtype=np.float64))
        base = np.atleast_1d(np.asarray(baselines, dtype=np.float64))
        keep = np.abs(amp) >= min_amplitude_v
        if not keep.any():
            raise ValueError(
                f"no pulses above {min_amplitude_v} V to build a template from")
        shapes = cls.normalise(wf[keep], base[keep], amp[keep])

        n_samples = shapes.shape[1]
        target = int(np.median(np.argmax(shapes, axis=1)))
        aligned = np.zeros_like(shapes)
        for i in range(shapes.shape[0]):
            shift = target - int(np.argmax(shapes[i]))
            aligned[i] = np.roll(shapes[i], shift)
            # np.roll wraps; blank the wrapped region so a pulse near an edge
            # does not paste its tail onto the start of the template.
            if shift > 0:
                aligned[i, :shift] = 0.0
            elif shift < 0:
                aligned[i, shift:] = 0.0

        mean = aligned.mean(axis=0)
        peak = float(np.max(np.abs(mean)))
        if peak <= 0:
            raise ValueError("averaged template is flat")
        mean = mean / peak
        spread = aligned.std(axis=0) / peak
        return cls(samples=mean, dt_ns=dt_ns, name=name, source=source,
                   created=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   n_averaged=int(shapes.shape[0]), spread=spread)

    # -- comparison ----------------------------------------------------------

    def compare(self, waveform, baseline: float, amplitude: float,
                window: int = 0) -> Dict:
        """How far one pulse departs from this shape.

        Returns the rms of (pulse - template) in units of the pulse's own
        amplitude, so 0.05 means "5% of peak height, on average". `window`
        limits the comparison to +-window samples about the peak; 0 uses the
        whole record, which includes the tail and the pre-pulse baseline.
        """
        shape = self.normalise(np.atleast_2d(waveform), [baseline], [amplitude])[0]
        n = min(shape.size, self.n_samples)
        shift = self.peak_index - int(np.argmax(shape[:n]))
        rolled = np.roll(shape, shift)
        if shift > 0:
            rolled[:shift] = 0.0
        elif shift < 0:
            rolled[shift:] = 0.0

        tpl = self.samples[:n]
        cur = rolled[:n]
        if window > 0:
            lo = max(0, self.peak_index - window)
            hi = min(n, self.peak_index + window + 1)
            tpl, cur = tpl[lo:hi], cur[lo:hi]
        diff = cur - tpl
        return {
            "rms": float(np.sqrt(np.mean(diff * diff))),
            "max_dev": float(np.max(np.abs(diff))) if diff.size else 0.0,
            "shift": int(shift),
            "n_compared": int(diff.size),
        }

    def compare_batch(self, waveforms, baselines, amplitudes,
                      window: int = 0) -> np.ndarray:
        """Vectorised rms-vs-template for a batch of hits.

        The analyzer calls this once per event, not once per hit: at a few kHz
        with 24 hits per event, a Python loop over hits is the whole budget.
        """
        wf = np.atleast_2d(np.asarray(waveforms))
        if wf.shape[0] == 0:
            return np.zeros(0, dtype=np.float64)
        shapes = self.normalise(wf, baselines, amplitudes)
        n = min(shapes.shape[1], self.n_samples)
        shapes = shapes[:, :n]
        peaks = np.argmax(shapes, axis=1)
        shifts = self.peak_index - peaks

        # Shift every row by its own amount without a Python loop: build the
        # gather indices once and take along axis.
        cols = np.arange(n)[None, :] - shifts[:, None]
        valid = (cols >= 0) & (cols < n)
        gathered = np.take_along_axis(shapes, np.clip(cols, 0, n - 1), axis=1)
        gathered[~valid] = 0.0

        tpl = self.samples[:n][None, :]
        if window > 0:
            lo = max(0, self.peak_index - window)
            hi = min(n, self.peak_index + window + 1)
            gathered, tpl = gathered[:, lo:hi], tpl[:, lo:hi]
        diff = gathered - tpl
        return np.sqrt(np.mean(diff * diff, axis=1))


def default_template_path(repo_root: str = None) -> str:
    root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "pulse_template.json")
