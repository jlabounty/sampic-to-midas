"""Synthetic SAMPIC waveforms.

Produces the 64-sample int16 records the .bin format stores, NOT volts. That is
deliberate: `converter.sampic_banks.build_ad_records` divides the int16 samples
by 1e4 to fill the AD00 bank (sampic_banks.py:84), so generating counts and
letting the same code path convert them means fake data carries the real
quantisation -- 0.1 mV steps -- instead of being suspiciously smooth.

The per-hit `baseline` and `amplitude` scalars are then measured back off the
QUANTISED samples rather than being the values we asked for. The AD00 bank
carries both the waveform and these scalars, and `build_ad_records` computes
`peak = baseline + amplitude` (sampic_banks.py:93); deriving them from the
samples makes the three agree to within one float32 ULP (~6e-8 V), the way a
real reconstruction of a real waveform would. They are NOT bit-identical: the
scalars are computed in float64 and stored as float32, so `baseline + amplitude`
rounds slightly differently from `max(waveform)`. Writing the REQUESTED values
instead would leave a mismatch of order the noise amplitude, which is the sort
of thing that costs an afternoon.

Note real SAMPIC data does not have this property at all: the board reports its
own fitted amplitude and baseline, and measured across run914 the difference
from max(waveform) runs to -24 mV. So a consistency check like this is a test of
SYNTHETIC hits only.

Pulse shape is the standard two-exponential
    f(t) = (1 - exp(-t/rise)) * exp(-t/fall),  t >= 0
normalised to unit peak. SAMPIC pulses in run914 are positive-going on a
positive baseline (measured: baseline ~0.746 V, amplitude ~0.064 V max).
"""

from dataclasses import dataclass

import numpy as np

from converter.sampic_banks import ADC_TO_VOLTS, AD_MAX_SAMPLES

INT16_MAX = 32767
INT16_MIN = -32768

# Measured from data/W9PIN_14MeV_0deg_100V_run914 -- see docs/ODB.md.
DEFAULT_BASELINE_V = 0.7459
DEFAULT_AMPLITUDE_V = 0.060
DEFAULT_NOISE_V = 0.0015
DEFAULT_RISE_NS = 0.6
DEFAULT_FALL_NS = 2.0
DEFAULT_SAMPLING_MSPS = 6400        # 0.15625 ns/sample
DEFAULT_PEAK_SAMPLE = 20.0

BASELINE_SAMPLES = 8
"""Samples averaged for the baseline estimate, ahead of the pulse.

Must stay well below `peak_sample` or the estimate eats the rising edge; checked
in `PulseParams.validate`.
"""


@dataclass(frozen=True)
class PulseParams:
    baseline_v: float = DEFAULT_BASELINE_V
    amplitude_v: float = DEFAULT_AMPLITUDE_V
    amplitude_spread: float = 0.25
    noise_v: float = DEFAULT_NOISE_V
    rise_ns: float = DEFAULT_RISE_NS
    fall_ns: float = DEFAULT_FALL_NS
    sampling_msps: int = DEFAULT_SAMPLING_MSPS
    n_samples: int = AD_MAX_SAMPLES
    peak_sample: float = DEFAULT_PEAK_SAMPLE
    polarity: int = 1

    def validate(self) -> None:
        if self.rise_ns <= 0 or self.fall_ns <= 0:
            raise ValueError(f"rise/fall must be > 0 ns, got {self.rise_ns}/{self.fall_ns}")
        if self.rise_ns >= self.fall_ns:
            raise ValueError(
                f"rise ({self.rise_ns} ns) must be shorter than fall ({self.fall_ns} ns); "
                "otherwise the 'pulse' is a slow bump with no discernible edge")
        if not 1 <= self.n_samples <= AD_MAX_SAMPLES:
            raise ValueError(f"n_samples must be 1..{AD_MAX_SAMPLES}, got {self.n_samples}")
        if self.sampling_msps <= 0:
            raise ValueError(f"sampling rate must be > 0 MS/s, got {self.sampling_msps}")
        if not BASELINE_SAMPLES + 2 <= self.peak_sample <= self.n_samples - 1:
            raise ValueError(
                f"peak_sample must be {BASELINE_SAMPLES + 2}..{self.n_samples - 1} so the "
                f"baseline window sits ahead of the edge, got {self.peak_sample}")
        if self.polarity not in (1, -1):
            raise ValueError(f"polarity must be +1 or -1, got {self.polarity}")
        if self.noise_v < 0:
            raise ValueError(f"noise must be >= 0 V, got {self.noise_v}")

    @property
    def dt_ns(self) -> float:
        """Sample spacing. 1e3/6400 = 0.15625 ns at the run914 setting."""
        return 1e3 / self.sampling_msps

    @property
    def peak_time_ns(self) -> float:
        """Where f(t) peaks: t = rise * ln(1 + fall/rise). Derived, not fitted."""
        return self.rise_ns * np.log1p(self.fall_ns / self.rise_ns)


def pulse_shape(t_ns: np.ndarray, rise_ns: float, fall_ns: float) -> np.ndarray:
    """Unit-peak two-exponential pulse; zero before t=0. Vectorised over any shape."""
    t = np.asarray(t_ns, dtype=np.float64)
    out = np.zeros_like(t)
    live = t > 0
    tl = t[live]
    out[live] = (1.0 - np.exp(-tl / rise_ns)) * np.exp(-tl / fall_ns)
    t_peak = rise_ns * np.log1p(fall_ns / rise_ns)
    peak = (1.0 - np.exp(-t_peak / rise_ns)) * np.exp(-t_peak / fall_ns)
    return out / peak


def generate(amplitudes_v: np.ndarray, time_offsets_ns: np.ndarray,
             params: PulseParams, rng: np.random.Generator):
    """Waveforms for a batch of hits.

    `amplitudes_v` (n,) is the peak height above baseline each hit should have;
    `time_offsets_ns` (n,) shifts each pulse within its window, which is how
    sub-sample timing information gets in -- without it every hit would peak on
    exactly the same sample and time reconstruction would have nothing to do.

    Returns `(wf int16 (n, n_samples), baseline_v (n,), amplitude_v (n,))`, the
    latter two measured off the quantised samples.

    Vectorised over the whole batch on purpose: this is called once per readout
    tick with every hit in it, and a per-hit loop here would cap the frontend at
    a few hundred Hz.
    """
    params.validate()
    amp = np.atleast_1d(np.asarray(amplitudes_v, dtype=np.float64))
    off = np.atleast_1d(np.asarray(time_offsets_ns, dtype=np.float64))
    n = amp.size
    if off.size == 1 and n > 1:
        off = np.repeat(off, n)
    if off.size != n:
        raise ValueError(f"amplitudes ({n}) and offsets ({off.size}) differ in length")

    dt = params.dt_ns
    # Sample k sits at t = k*dt; the pulse starts so that its peak lands on
    # `peak_sample`, shifted by this hit's sub-sample offset.
    t_grid = np.arange(params.n_samples, dtype=np.float64) * dt
    t_start = params.peak_sample * dt - params.peak_time_ns + off
    shape = pulse_shape(t_grid[None, :] - t_start[:, None],
                        params.rise_ns, params.fall_ns)

    volts = params.baseline_v + params.polarity * amp[:, None] * shape
    if params.noise_v > 0:
        volts = volts + rng.normal(0.0, params.noise_v, volts.shape)

    counts = np.rint(volts * ADC_TO_VOLTS)
    if (counts > INT16_MAX).any() or (counts < INT16_MIN).any():
        # Silently wrapping would make a saturated pulse look like a negative
        # one, which is a confusing thing to debug from a DQM plot.
        raise ValueError(
            f"waveform saturates int16: baseline {params.baseline_v} V + amplitude "
            f"up to {float(amp.max()):.3f} V is outside +-{INT16_MAX / ADC_TO_VOLTS:.3f} V")
    wf = counts.astype("<i2")

    # Measure back off the quantised samples -- see the module docstring.
    base_counts = wf[:, :BASELINE_SAMPLES].mean(axis=1)
    if params.polarity > 0:
        peak_counts = wf.max(axis=1).astype(np.float64)
    else:
        peak_counts = wf.min(axis=1).astype(np.float64)
    baseline_v = (base_counts / ADC_TO_VOLTS).astype(np.float64)
    amplitude_v = ((peak_counts - base_counts) / ADC_TO_VOLTS).astype(np.float64)
    return wf, baseline_v, amplitude_v
