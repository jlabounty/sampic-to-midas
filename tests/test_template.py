"""The canonical pulse shape, and comparing pulses against it."""

import json

import numpy as np
import pytest

from fakesampic.template import PulseTemplate
from fakesampic.waveform import PulseParams, generate


@pytest.fixture
def pulses(rng):
    p = PulseParams(noise_v=0.0005)
    wf, base, amp = generate(np.full(300, 0.06), rng.normal(0, 0.08, 300), p, rng)
    return wf, base, amp


def test_built_template_is_unit_peak(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    assert float(np.max(np.abs(t.samples))) == pytest.approx(1.0)
    assert t.n_averaged == len(wf)
    assert t.n_samples == wf.shape[1]


def test_template_matches_the_pulses_it_was_built_from(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    rms = t.compare_batch(wf, base, amp, window=12)
    # Sub-sample timing jitter keeps this from being zero, but it must be small
    # compared with a genuinely wrong shape (see the next test).
    assert rms.mean() < 0.15


def test_a_distorted_pulse_is_flagged(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    good = t.compare(wf[0], base[0], amp[0])["rms"]
    bad_wf = wf[0].astype(np.float64).copy()
    bad_wf[30:40] += 3000          # a 0.3 V bump in the tail
    bad = t.compare(bad_wf, base[0], amp[0])["rms"]
    assert bad > 10 * good, f"distortion {bad} vs clean {good}"


def test_comparison_is_scale_free(pulses):
    """Halving a pulse's amplitude must not change its shape score."""
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    one = t.compare(wf[0], base[0], amp[0])["rms"]
    # Same shape, half the height, measured consistently.
    small = ((wf[0].astype(np.float64) - base[0] * 1e4) * 0.5 + base[0] * 1e4)
    half = t.compare(small, base[0], amp[0] * 0.5)["rms"]
    assert half == pytest.approx(one, abs=0.02)


def test_window_restricts_the_comparison(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    full = t.compare(wf[0], base[0], amp[0], window=0)
    near = t.compare(wf[0], base[0], amp[0], window=5)
    assert near["n_compared"] < full["n_compared"]
    assert near["n_compared"] == 11


def test_batch_matches_the_scalar_path(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    batch = t.compare_batch(wf[:20], base[:20], amp[:20], window=12)
    one_by_one = [t.compare(wf[i], base[i], amp[i], window=12)["rms"]
                  for i in range(20)]
    assert batch == pytest.approx(one_by_one, rel=1e-9)


def test_normalise_handles_a_zero_amplitude_hit():
    """Dividing noise by ~0 would produce a huge meaningless shape."""
    wf = np.ones((2, 8), dtype="<i2") * 7000
    out = PulseTemplate.normalise(wf, [0.7, 0.7], [0.05, 0.0])
    assert np.isfinite(out).all()
    assert (out[1] == 0).all()


def test_building_needs_pulses_above_threshold():
    wf = np.ones((5, 8), dtype="<i2") * 7000
    with pytest.raises(ValueError, match="no pulses above"):
        PulseTemplate.from_waveforms(wf, np.full(5, 0.7), np.full(5, 0.0001))


def test_round_trips_through_json(tmp_path, pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp, name="x", source="unit test")
    path = tmp_path / "t.json"
    t.save(str(path))
    back = PulseTemplate.load(str(path))
    assert back.name == "x" and back.source == "unit test"
    assert back.n_averaged == t.n_averaged
    assert back.peak_index == t.peak_index
    assert back.samples == pytest.approx(t.samples)
    assert back.spread == pytest.approx(t.spread)


def test_refuses_an_unknown_format_version(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"format": 99, "samples": [0, 1, 0, 0]}))
    # Guessing would produce plausible numbers that are quietly wrong.
    with pytest.raises(ValueError, match="format 99"):
        PulseTemplate.load(str(path))


def test_rejects_a_degenerate_template():
    with pytest.raises(ValueError, match="at least 4 samples"):
        PulseTemplate(samples=np.array([1.0, 0.0]))
    with pytest.raises(ValueError, match="all zeros"):
        PulseTemplate(samples=np.zeros(8))


def test_alignment_shift_is_reported(pulses):
    wf, base, amp = pulses
    t = PulseTemplate.from_waveforms(wf, base, amp)
    shifted = np.roll(wf[0], 3)
    r = t.compare(shifted, base[0], amp[0], window=8)
    assert r["shift"] != 0, "a moved pulse should report the shift applied"
