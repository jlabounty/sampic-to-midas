"""Charge sharing against the C++ reference."""

import math

import numpy as np
import pytest

from fakesampic.chargesharing import (DEFAULT_SENSOR_FIT, SensorFit,
                                      build_eta_table, eval_gaussian_mixture,
                                      strip_fractions)


def _cpp_reference(x, fit):
    """Literal transcription of evalGaussianMixture (LGADDetectorMapping.hh:294)."""
    return (fit.amp1 * math.exp(-0.5 * (x * x) / (fit.sigma1 * fit.sigma1))
            + fit.amp2 * math.exp(-0.5 * (x * x) / (fit.sigma2 * fit.sigma2))
            + fit.amp3 * math.exp(-0.5 * (x * x) / (fit.sigma3 * fit.sigma3)))


def test_matches_cpp_to_machine_precision():
    fit = SensorFit.default()
    for x in (-2.0, -0.37, 0.0, 0.13, 0.5, 1.9):
        assert eval_gaussian_mixture(x, fit) == pytest.approx(
            _cpp_reference(x, fit), rel=1e-12, abs=1e-12)


def test_vectorised_equals_scalar_loop():
    fit = SensorFit.default()
    xs = np.linspace(-3, 3, 257)
    assert eval_gaussian_mixture(xs, fit) == pytest.approx(
        np.array([_cpp_reference(float(x), fit) for x in xs]), rel=1e-12)


def test_fractions_sum_to_one_and_are_symmetric():
    fit = SensorFit.default()
    centres = (np.arange(10) - 4.5) * 0.5
    frac = strip_fractions([0.0, 0.25, -1.1], centres, fit)
    assert frac.sum(axis=1) == pytest.approx(np.ones(3))
    # A track exactly between two strips splits evenly between them.
    mid = strip_fractions([0.0], centres, fit)[0]
    assert mid[4] == pytest.approx(mid[5], rel=1e-9)


def test_charge_is_shared_not_concentrated():
    """The whole reason this model exists: more than one strip must light up."""
    fit = SensorFit.default()
    centres = (np.arange(10) - 4.5) * 0.5
    frac = strip_fractions([0.0], centres, fit)[0]
    assert (frac > 0.02).sum() >= 2, "a single lit strip gives flat correlations"
    assert frac.max() < 0.95


def test_track_far_outside_collects_nothing_rather_than_dividing_by_zero():
    fit = SensorFit.default()
    centres = (np.arange(10) - 4.5) * 0.5
    frac = strip_fractions([1e6], centres, fit)
    assert np.isfinite(frac).all()
    assert frac.sum() == pytest.approx(0.0)


def test_eta_table_shape_and_antisymmetry():
    x, corr = build_eta_table(SensorFit.default(), 10, 0.5, -2.5, 2.5, 0.01)
    assert x.shape == corr.shape
    # The bias is an odd function of position about the plane centre.
    mid = len(x) // 2
    assert corr[mid] == pytest.approx(0.0, abs=2e-3)


def test_rejects_malformed_fits():
    with pytest.raises(ValueError, match="6 values"):
        SensorFit.from_sequence([1, 2, 3])
    with pytest.raises(ValueError, match="sigmas"):
        SensorFit.from_sequence([1, 0, 1, 1, 1, 1])


def test_default_fit_is_the_cpp_one():
    assert DEFAULT_SENSOR_FIT[0] == pytest.approx(0.95143)
    # Sigmas in mm, already scaled by um_to_mm in the C++ (line 467).
    assert 0.1 < DEFAULT_SENSOR_FIT[1] < 1.0
