"""Histogram accumulators and the persistence ring buffer."""

import numpy as np
import pytest

from fakesampic.hist import Hist1D, Hist2D, HistStore, Persistence


def test_binning_places_values_in_the_right_bins():
    h = Hist1D("t", 10, 0.0, 10.0)
    h.fill([0.0, 0.5, 9.999])
    assert h.counts[0] == 2 and h.counts[9] == 1


def test_out_of_range_is_counted_not_clipped():
    """Clipping into the edge bin makes a spike that is not real."""
    h = Hist1D("t", 10, 0.0, 10.0)
    h.fill([-5.0, 15.0, 5.0])
    assert h.underflow == 1 and h.overflow == 1
    assert h.counts.sum() == 1
    assert h.entries == 3, "entries counts everything offered, in range or not"


def test_upper_edge_is_exclusive():
    h = Hist1D("t", 4, 0.0, 4.0)
    h.fill([4.0])
    assert h.overflow == 1 and h.counts.sum() == 0


def test_weighted_fill():
    h = Hist1D("t", 4, 0.0, 4.0)
    h.fill([0.5, 0.5, 2.5], weights=[2.0, 3.0, 1.0])
    assert h.counts[0] == 5.0 and h.counts[2] == 1.0


def test_reset_clears_everything():
    h = Hist1D("t", 4, 0.0, 4.0)
    h.fill([1.0, -1.0, 9.0])
    h.reset()
    assert h.counts.sum() == 0 and h.entries == 0
    assert h.underflow == 0 and h.overflow == 0


def test_empty_fill_is_harmless():
    h = Hist1D("t", 4, 0.0, 4.0)
    h.fill([])
    assert h.entries == 0


@pytest.mark.parametrize("kwargs,match", [
    (dict(nbins=0, xlow=0, xhigh=1), "nbins"),
    (dict(nbins=4, xlow=1, xhigh=1), "xhigh > xlow"),
    (dict(nbins=4, xlow=2, xhigh=1), "xhigh > xlow"),
])
def test_rejects_impossible_binning(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Hist1D("bad", **kwargs)


def test_hist2d_orientation():
    """counts is (ny, nx) -- getting this backwards transposes every plot."""
    h = Hist2D("t", nx=4, xlow=0, xhigh=4, ny=2, ylow=0, yhigh=2)
    h.fill([3.5], [0.5])
    assert h.counts.shape == (2, 4)
    assert h.counts[0, 3] == 1


def test_hist2d_drops_out_of_range_points():
    h = Hist2D("t", nx=4, xlow=0, xhigh=4, ny=2, ylow=0, yhigh=2)
    h.fill([-1, 2, 9], [1, 1, 1])
    assert h.counts.sum() == 1
    assert h.entries == 3


def test_hist2d_rejects_mismatched_lengths():
    h = Hist2D("t", nx=2, xlow=0, xhigh=2, ny=2, ylow=0, yhigh=2)
    with pytest.raises(ValueError, match="differ in length"):
        h.fill([1, 2], [1])


# --- persistence ------------------------------------------------------------

def test_ring_buffer_keeps_the_last_n_oldest_first():
    p = Persistence(4, depth=3, n_samples=2)
    for i in range(5):
        p.add([1], np.full((1, 2), i, dtype="<i2"))
    assert p.count(1) == 3
    # Oldest first, so a page can fade them by age.
    assert p.waveforms(1)[:, 0].tolist() == [2, 3, 4]


def test_ring_buffer_before_it_wraps():
    p = Persistence(4, depth=5, n_samples=2)
    p.add([0, 0], np.array([[1, 1], [2, 2]], dtype="<i2"))
    assert p.count(0) == 2
    assert p.waveforms(0)[:, 0].tolist() == [1, 2]


def test_untouched_channels_are_empty_not_zero_filled():
    p = Persistence(4, depth=3, n_samples=2)
    p.add([2], np.ones((1, 2), dtype="<i2"))
    assert p.count(0) == 0
    assert p.waveforms(0).shape == (0, 2)
    assert p.filled_channels() == [2]


def test_out_of_range_channels_are_ignored():
    p = Persistence(4, depth=2, n_samples=2)
    p.add([99, -1, 2], np.ones((3, 2), dtype="<i2"))
    assert p.filled_channels() == [2]


def test_narrow_waveform_is_zero_padded():
    p = Persistence(2, depth=1, n_samples=6)
    p.add([0], np.array([[7, 7, 7]], dtype="<i2"))
    assert p.waveforms(0)[0].tolist() == [7, 7, 7, 0, 0, 0]


def test_persistence_rejects_mismatched_input():
    p = Persistence(2, depth=1, n_samples=4)
    with pytest.raises(ValueError, match="channels but"):
        p.add([0, 1], np.ones((1, 4), dtype="<i2"))


def test_store_tracks_names_and_resets_everything():
    s = HistStore()
    s.add(Hist1D("b", 2, 0, 2))
    s.add(Hist1D("a", 2, 0, 2))
    s.persistence = Persistence(2, depth=2, n_samples=2)
    s.get("a").fill([1.0])
    s.persistence.add([0], np.ones((1, 2), dtype="<i2"))
    assert s.names() == ["a", "b"] and "a" in s and len(s) == 2
    s.reset_all()
    assert s.get("a").entries == 0
    assert s.persistence.count(0) == 0
