"""Per-channel accumulation and its sentinels."""

import numpy as np
import pytest

from fakesampic.identity import N_DAQ_CHANNELS, NO_READING, UNMAPPED_RATE
from fakesampic.occupancy import OccupancyAccumulator
from fakesampic.sources.base import HIT_DTYPE


def _hits(channels, amps):
    h = np.zeros(len(channels), dtype=HIT_DTYPE)
    h["channel"] = channels
    h["amplitude"] = amps
    return h


def test_bank_length_is_fixed_regardless_of_mapping():
    """mlogger builds its history schema from the bank length at startup."""
    acc = OccupancyAccumulator()
    acc.set_mapped(range(3))
    snap = acc.snapshot(1.0)
    assert len(snap.rates_hz) == N_DAQ_CHANNELS
    assert len(snap.mean_amplitude_v) == N_DAQ_CHANNELS
    assert snap.rates_hz.dtype == np.dtype("<f4")


def test_three_channel_states_stay_distinguishable():
    acc = OccupancyAccumulator()
    acc.set_mapped([0, 1])
    acc.add_event(_hits([0, 0], [0.1, 0.3]))
    snap = acc.snapshot(2.0)
    assert snap.rates_hz[0] == pytest.approx(1.0)          # busy
    assert snap.rates_hz[1] == pytest.approx(0.0)          # mapped but silent
    assert snap.rates_hz[9] == pytest.approx(UNMAPPED_RATE)  # not connected
    assert snap.mean_amplitude_v[1] == pytest.approx(NO_READING)


def test_mean_amplitude_is_a_mean():
    acc = OccupancyAccumulator()
    acc.set_mapped([5])
    acc.add_event(_hits([5, 5, 5], [0.1, 0.2, 0.6]))
    assert acc.snapshot(1.0).mean_amplitude_v[5] == pytest.approx(0.3, rel=1e-5)


def test_drain_resets_and_snapshot_does_not():
    acc = OccupancyAccumulator()
    acc.set_mapped([1])
    acc.add_event(_hits([1], [0.2]), n_bytes=344)
    assert acc.snapshot(1.0).hits == 1
    assert acc.snapshot(1.0).hits == 1, "snapshot must not consume"
    assert acc.drain(1.0).hits == 1
    assert acc.snapshot(1.0).hits == 0, "drain must reset"


def test_tracks_max_hits_per_event():
    acc = OccupancyAccumulator()
    acc.set_mapped(range(10))
    acc.add_event(_hits([1, 2], [0.1, 0.1]))
    acc.add_event(_hits([1, 2, 3, 4, 5], [0.1] * 5))
    snap = acc.drain(1.0)
    assert snap.events == 2 and snap.hits == 7
    assert snap.max_hits_per_event == 5


def test_out_of_range_channels_do_not_crash():
    acc = OccupancyAccumulator()
    acc.set_mapped([0])
    acc.add_event(_hits([0, 200 % 256], [0.1, 0.1]))
    assert np.isfinite(acc.snapshot(1.0).rates_hz).all()


def test_empty_event_is_ignored():
    acc = OccupancyAccumulator()
    acc.add_event(np.zeros(0, dtype=HIT_DTYPE))
    assert acc.snapshot(1.0).events == 0


def test_zero_elapsed_does_not_divide_by_zero():
    acc = OccupancyAccumulator()
    acc.set_mapped([0])
    acc.add_event(_hits([0], [0.1]))
    assert np.isfinite(acc.snapshot(0.0).rates_hz).all()
