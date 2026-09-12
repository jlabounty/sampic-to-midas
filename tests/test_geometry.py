"""Geometry port fidelity against LGADDetectorMapping.hh."""

import numpy as np
import pytest

from fakesampic.geometry import UNMAPPED, build_geometry


def test_strip_position_matches_cpp_formula():
    # computeStripPosition (LGADDetectorMapping.hh:214):
    #   centre = (N-1)/2 - offset;  pos = (strip - centre) * pitch
    g = build_geometry(n_planes=1, n_strips=10, pitch_mm=0.5)
    pos = g.planes[0].strip_positions_mm()
    assert pos == pytest.approx([-2.25, -1.75, -1.25, -0.75, -0.25,
                                 0.25, 0.75, 1.25, 1.75, 2.25])
    # Symmetric about zero for an even strip count, as the C++ is.
    assert pos.sum() == pytest.approx(0.0)


def test_plane_offset_shifts_towards_positive_u():
    a = build_geometry(n_planes=1, n_strips=10, pitch_mm=0.5)
    b = build_geometry(n_planes=1, n_strips=10, pitch_mm=0.5, offsets_mm=[1.0])
    shift = b.planes[0].strip_positions_mm() - a.planes[0].strip_positions_mm()
    assert shift == pytest.approx(np.full(10, 0.5))   # offset * pitch


def test_units_are_mm_not_um():
    """The regression case for the wrong comment in evalGaussianMixture.

    Pitch is 0.5 -- millimetres. If anything ever reinterprets the geometry as
    microns, strip positions land 1000x out and this catches it.
    """
    g = build_geometry(n_planes=1, n_strips=10, pitch_mm=0.5)
    span = np.ptp(g.planes[0].strip_positions_mm())
    assert 4.0 < span < 5.0, "a 10-strip 0.5 mm plane spans 4.5 mm"


def test_channel_map_round_trips():
    g = build_geometry(n_planes=8, n_strips=10)
    for p in range(g.n_planes):
        for s in range(g.n_strips):
            ch = int(g.channel_of[p, s])
            if ch == UNMAPPED:
                continue
            assert int(g.channel_plane[ch]) == p
            assert int(g.channel_strip[ch]) == s
            assert g.channel_position_mm[ch] == pytest.approx(
                float(g.planes[p].strip_position_mm(s)))


def test_default_detector_is_eight_planes_all_mapped():
    g = build_geometry()
    assert g.n_planes == 8 and g.n_strips == 10
    assert g.mapped_channels.size == 80, "8 x 10 strips must all get a channel"
    assert [p.orientation for p in g.planes] == list("XYXYXYXY")


def test_unmapped_channels_report_a_sentinel_not_zero():
    g = build_geometry()
    assert int(g.channel_plane[100]) == UNMAPPED
    assert g.channel_names()[100] == "ch100"


def test_rejects_duplicate_channels():
    with pytest.raises(ValueError, match="twice"):
        build_geometry(n_planes=2, n_strips=2, channel_map=[0, 1, 1, 2])


def test_rejects_bad_orientation_and_efficiency():
    with pytest.raises(ValueError, match="orientation"):
        build_geometry(n_planes=2, n_strips=2, orientation=["X", "Z"])
    with pytest.raises(ValueError, match="efficiency"):
        build_geometry(n_planes=1, n_strips=2, efficiency=[1.5])


def test_single_element_list_broadcasts():
    """MIDAS hands back a 1-element ODB array as a scalar; both must work."""
    g = build_geometry(n_planes=4, n_strips=4, efficiency=[0.5])
    assert [p.efficiency for p in g.planes] == [0.5] * 4
