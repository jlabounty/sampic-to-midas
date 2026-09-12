"""The synthetic pulse model and its self-consistency with the AD00 scalars."""

import numpy as np
import pytest

from converter.sampic_banks import build_ad_records
from converter.sampic_bin import hit_dtype
from fakesampic.waveform import (BASELINE_SAMPLES, PulseParams, generate,
                                 pulse_shape)


def test_sampling_interval_is_the_run914_value():
    assert PulseParams().dt_ns == pytest.approx(0.15625)   # 1e3 / 6400 MS/s


def test_pulse_peaks_where_the_analytic_formula_says():
    p = PulseParams()
    t = np.linspace(0, 20, 20001)
    shape = pulse_shape(t, p.rise_ns, p.fall_ns)
    assert t[shape.argmax()] == pytest.approx(p.peak_time_ns, abs=2e-3)
    # Sampled on a finite grid, so the maximum sample sits just below the
    # analytic peak; what matters is that nothing exceeds 1.
    assert shape.max() == pytest.approx(1.0, rel=1e-6), "normalised to unit peak"
    assert shape.max() <= 1.0
    assert (shape[t <= 0] == 0).all(), "causal: nothing before t=0"


def test_peak_lands_on_the_requested_sample(rng):
    p = PulseParams(noise_v=0.0, peak_sample=20.0)
    wf, _, _ = generate(np.full(4, 0.06), np.zeros(4), p, rng)
    assert (wf.argmax(axis=1) == 20).all()


def test_sub_sample_offsets_move_the_pulse(rng):
    """Without this, every hit peaks on the same sample and timing is undoable."""
    p = PulseParams(noise_v=0.0)
    wf, _, _ = generate(np.full(3, 0.06), np.array([-0.5, 0.0, 0.5]), p, rng)
    peaks = wf.argmax(axis=1)
    assert peaks[0] < peaks[1] < peaks[2]


def test_output_is_int16_counts_not_volts(rng):
    wf, _, _ = generate(np.full(4, 0.06), np.zeros(4), PulseParams(), rng)
    assert wf.dtype == np.dtype("<i2")
    # 0.746 V baseline -> ~7459 counts at 1e4 counts/V.
    assert 7000 < int(np.median(wf)) < 8000


def test_scalars_are_measured_off_the_quantised_samples(rng):
    """baseline + amplitude must reproduce max(waveform)/1e4 to a float32 ULP.

    Not bit-identical: the scalars are computed in float64 and stored as
    float32, so the sum rounds differently from the division. What matters is
    that the gap is rounding-sized (~6e-8 V) rather than noise-sized -- if the
    scalars were the REQUESTED values rather than the measured ones, the
    disagreement would be of order the baseline noise, 1e-3 V.
    """
    wf, base, amp = generate(rng.normal(0.06, 0.01, 500).clip(0.005, None),
                             rng.normal(0, 0.08, 500), PulseParams(), rng)
    lhs = (base.astype("<f4") + amp.astype("<f4")).astype("<f4")
    rhs = (wf.max(axis=1) / np.float32(1e4)).astype("<f4")
    gap = np.abs(lhs.astype(np.float64) - rhs.astype(np.float64))
    assert gap.max() <= 2 * np.spacing(np.float32(1.0)), f"max gap {gap.max():g} V"
    assert gap.max() < 1e-5, "must be far smaller than the 1e-3 V noise"


def test_ad_bank_peak_equals_max_waveform(rng):
    """The same property, verified through the real bank builder."""
    n = 300
    wf, base, amp = generate(rng.normal(0.06, 0.01, n).clip(0.005, None),
                             rng.normal(0, 0.08, n), PulseParams(), rng)
    hits = np.zeros(n, dtype=hit_dtype(64))
    hits["wf"], hits["wf_size"] = wf, 64
    hits["baseline"], hits["amplitude"] = base, amp
    ad = build_ad_records(hits, 0, True, True)
    assert ad["peak"] == pytest.approx(ad["waveform"].max(axis=1), abs=1e-6)


def test_baseline_window_sits_ahead_of_the_edge(rng):
    p = PulseParams(noise_v=0.0)
    wf, base, _ = generate(np.array([0.06]), np.zeros(1), p, rng)
    assert p.peak_sample > BASELINE_SAMPLES + 1
    assert base[0] == pytest.approx(p.baseline_v, abs=1e-3)


def test_saturation_is_an_error_not_a_wraparound(rng):
    """int16 overflow would turn a big positive pulse into a negative one."""
    with pytest.raises(ValueError, match="saturates"):
        generate(np.array([5.0]), np.zeros(1), PulseParams(), rng)


@pytest.mark.parametrize("kwargs,match", [
    (dict(rise_ns=3.0, fall_ns=1.0), "shorter than fall"),
    (dict(n_samples=0), "n_samples"),
    (dict(peak_sample=2.0), "peak_sample"),
    (dict(polarity=0), "polarity"),
    (dict(noise_v=-1.0), "noise"),
])
def test_rejects_impossible_parameters(kwargs, match):
    with pytest.raises(ValueError, match=match):
        PulseParams(**kwargs).validate()


def test_negative_polarity_measures_the_minimum(rng):
    p = PulseParams(polarity=-1, baseline_v=0.5, noise_v=0.0)
    wf, base, amp = generate(np.array([0.06]), np.zeros(1), p, rng)
    assert amp[0] < 0
    assert (base[0] + amp[0]) == pytest.approx(wf.min() / 1e4, abs=1e-9)
