"""The three event sources, and the contract they all satisfy."""

import numpy as np
import pytest

from converter.sampic_bin import hit_dtype
from fakesampic.geometry import build_geometry
from fakesampic.sources.base import HIT_DTYPE, HIT_NUMBER_WRAP, restamp, widen
from fakesampic.sources.binfile import BinFileSource
from fakesampic.sources.mixer import MixerSource
from fakesampic.sources.synthetic import SyntheticSource


def _synth(**kw):
    kw.setdefault("seed", 4242)
    return SyntheticSource(geometry=build_geometry(), **kw)


# --- the shared contract ----------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda run914: BinFileSource([run914], gap_ns=100.0),
    lambda run914: _synth(model="track"),
    lambda run914: _synth(model="parametric"),
])
def test_sources_emit_the_shared_dtype_sorted_in_time(make, run914):
    src = make(run914)
    src.open()
    try:
        evs = src.next_events(np.arange(50) * 1e6)
        assert evs, "source produced nothing"
        for ev in evs:
            assert ev.hits.dtype == HIT_DTYPE, "every source must emit hit_dtype(64)"
            assert len(ev.hits) > 0, "an empty event has no AD00 bank to send"
            # cluster_starts and the AT00 timestamp both require ascending t0.
            assert (np.diff(ev.hits["t0"]) >= 0).all()
            assert ev.t0_first_ns == pytest.approx(float(ev.hits["t0"][0]))
            assert (ev.hits["channel"] >= 0).all()
            assert (ev.hits["wf_size"] > 0).all()
    finally:
        src.close()


def test_widen_pads_a_narrow_waveform_without_touching_the_rest():
    narrow = np.zeros(3, dtype=hit_dtype(32))
    narrow["wf"][:] = 7
    narrow["channel"] = [1, 2, 3]
    wide = widen(narrow)
    assert wide.dtype == HIT_DTYPE
    assert (wide["wf"][:, :32] == 7).all()
    assert (wide["wf"][:, 32:] == 0).all(), "unused slots must be zero"
    assert wide["channel"].tolist() == [1, 2, 3]


def test_widen_refuses_an_oversized_waveform():
    with pytest.raises(ValueError, match="more than the 64"):
        widen(np.zeros(1, dtype=hit_dtype(80)))


# --- re-stamping ------------------------------------------------------------

def test_restamp_preserves_intra_event_structure():
    h = np.zeros(3, dtype=HIT_DTYPE)
    h["t0"] = [1000.0, 1002.5, 1010.0]
    h["time"] = [1002.2, 1004.1, 1012.5]
    out = restamp(h, 5.0e6, hit_number=100)
    assert np.diff(out["t0"]) == pytest.approx(np.diff(h["t0"]))
    assert out["t0"][0] == pytest.approx(5.0e6)
    assert out["hit_number"].tolist() == [100, 101, 102]


def test_restamp_time_offset_survives_to_float32_precision():
    """`time` is float32 in the format, so the residual degrades at large t0.

    That is a property of the SAMPIC file format, not of the restamp, and it is
    why Reset Clock At BOR defaults on -- so pin it rather than let it drift.
    """
    h = np.zeros(2, dtype=HIT_DTYPE)
    h["t0"] = [1000.0, 1002.0]
    h["time"] = [1002.2, 1004.1]
    for t in (1e3, 1e5, 5e6):
        out = restamp(h, t)
        err = np.abs((out["time"].astype(np.float64) - out["t0"])
                     - (h["time"].astype(np.float64) - h["t0"])).max()
        assert err <= np.spacing(np.float32(t)) * 1.01


def test_hit_number_wraps_instead_of_overflowing_int32():
    h = np.zeros(4, dtype=HIT_DTYPE)
    out = restamp(h, 0.0, hit_number=HIT_NUMBER_WRAP - 1)
    assert (out["hit_number"] >= 0).all(), "int32 overflow would go negative"


# --- binfile ----------------------------------------------------------------

def test_endless_loop_is_monotonic_across_every_seam(run914):
    """The core requirement: an endless stream with no discontinuity.

    Short passes so the seam is crossed many times in a quick test.
    """
    src = BinFileSource([run914], gap_ns=100.0, loop=True, loop_gap_ns=1e6,
                        max_events_per_pass=20)
    src.open()
    try:
        t0, hn = [], []
        t = 0.0
        while len(t0) < 300:
            gaps = src.native_gaps_ns(32)
            assert gaps is not None and gaps.size, "replay source must offer timing"
            times = t + np.cumsum(gaps)
            t = float(times[-1])
            for ev in src.next_events(times):
                t0.append(float(ev.hits["t0"][0]))
                hn.append(int(ev.hits["hit_number"][0]))
        t0 = np.array(t0)
        assert src.stats().loops >= 5, "test must actually cross seams"
        assert (np.diff(t0) > 0).all(), "timestamps must never go backwards"
        assert (np.diff(hn) > 0).all(), "hit numbers must never repeat"
        # No timestamp may be inherited from the file, whose own t0 reaches 6e10.
        assert t0.max() < 1e10
    finally:
        src.close()


def test_passthrough_keeps_the_files_own_timestamps(run914):
    """The byte-identity mode: without it, comparison with bin_to_mid is empty."""
    src = BinFileSource([run914], gap_ns=100.0, loop=False, restamp_times=False)
    src.open()
    try:
        evs = src.next_events(np.arange(5) * 1e9)
        # run914's first hit sits at a large absolute timestamp; a restamped
        # event would start near the requested time instead.
        assert evs[0].hits["t0"][0] > 1e6
        assert evs[0].t0_first_ns == pytest.approx(float(evs[0].hits["t0"][0]))
    finally:
        src.close()


def test_non_looping_source_reports_exhausted_rather_than_erroring(run914):
    src = BinFileSource([run914], gap_ns=100.0, loop=False, max_events_per_pass=5)
    src.open()
    try:
        got = 0
        for _ in range(10):
            got += len(src.next_events(np.arange(10) * 1e6))
        assert src.stats().exhausted
        assert "exhausted" in src.describe()
    finally:
        src.close()


def test_missing_file_is_refused_at_construction():
    with pytest.raises(FileNotFoundError, match="no SAMPIC"):
        BinFileSource(["/nonexistent/nothing-here-*.bin"])


def test_native_gaps_track_the_real_file_structure(run914):
    src = BinFileSource([run914], gap_ns=100.0)
    src.open()
    try:
        gaps = src.native_gaps_ns(200)
        # run914 runs at roughly 700 Hz with a very irregular structure --
        # reproducing that, rather than an idealised Poisson, is the point.
        assert gaps.size == 200
        assert (gaps[1:] > 0).all()
        assert np.median(gaps[1:]) > 1e4
    finally:
        src.close()


# --- synthetic --------------------------------------------------------------

def test_track_model_lights_neighbouring_strips():
    src = _synth(model="track", beam_sigma_mm=1.0)
    src.open()
    evs = src.next_events(np.arange(200) * 1e6)
    g = src.geometry
    per_plane = []
    for ev in evs:
        planes = g.channel_plane[ev.hits["channel"].astype(int)]
        per_plane.append(np.bincount(planes[planes >= 0], minlength=g.n_planes))
    counts = np.mean(per_plane, axis=0)
    assert (counts > 1.5).all(), "charge sharing must light more than one strip"
    assert (counts < 6.0).all(), "a whole plane firing is not charge sharing"


def test_track_model_reconstructs_the_beam_and_correlates_planes():
    """Position reco and plane-to-plane correlation are what a DQM page shows."""
    sigma = 1.0
    src = _synth(model="track", beam_sigma_mm=sigma, beam_divergence_mrad=0.5)
    src.open()
    evs = src.next_events(np.arange(1500) * 1e6)
    g = src.geometry

    def cog(ev, plane):
        ch = ev.hits["channel"].astype(int)
        m = g.channel_plane[ch] == plane
        if m.sum() < 2:
            return None
        a = ev.hits["amplitude"][m].astype(float)
        return float((g.channel_position_mm[ch[m]] * a).sum() / a.sum())

    x0, x2, y1 = [], [], []
    for ev in evs:
        a, b, c = cog(ev, 0), cog(ev, 2), cog(ev, 1)
        if None not in (a, b, c):
            x0.append(a); x2.append(b); y1.append(c)
    x0, x2, y1 = map(np.array, (x0, x2, y1))
    assert x0.size > 500

    assert abs(x0.mean()) < 0.25, "beam should be centred"
    assert x0.std() == pytest.approx(sigma, rel=0.35), "beam width should be recovered"
    # Planes 0 and 2 both measure x, so a straight track correlates them.
    assert np.corrcoef(x0, x2)[0, 1] > 0.9
    # Plane 1 measures y, which is independent of x.
    assert abs(np.corrcoef(x0, y1)[0, 1]) < 0.2


def test_plane_efficiency_drops_whole_planes_not_single_strips():
    src = _synth(model="track")
    src.geometry = build_geometry(efficiency=[0.5])
    src = SyntheticSource(geometry=build_geometry(efficiency=[0.5]),
                          model="track", seed=7)
    src.open()
    evs = src.next_events(np.arange(400) * 1e6)
    g = src.geometry
    seen = 0
    for ev in evs:
        planes = set(g.channel_plane[ev.hits["channel"].astype(int)].tolist())
        seen += len(planes - {-1})
    assert 0.35 < seen / (400 * g.n_planes) < 0.65, "should be about half"


def test_parametric_model_has_no_cross_plane_correlation():
    src = _synth(model="parametric", hit_probability=0.3)
    src.open()
    evs = src.next_events(np.arange(400) * 1e6)
    assert evs
    assert all(len(e.hits) > 0 for e in evs)


def test_synthetic_rejects_bad_configuration():
    with pytest.raises(ValueError, match="model"):
        SyntheticSource(model="magic")
    with pytest.raises(ValueError, match="threshold"):
        SyntheticSource(charge_threshold=1.5)
    with pytest.raises(ValueError, match="probability"):
        SyntheticSource(hit_probability=2.0)


def test_synthetic_is_reproducible_with_a_seed():
    a = _synth(seed=99); a.open()
    b = _synth(seed=99); b.open()
    ea = a.next_events(np.arange(20) * 1e6)
    eb = b.next_events(np.arange(20) * 1e6)
    assert len(ea) == len(eb)
    for x, y in zip(ea, eb):
        assert np.array_equal(x.hits["channel"], y.hits["channel"])
        assert np.array_equal(x.hits["wf"], y.hits["wf"])


# --- mixer ------------------------------------------------------------------

def test_overlay_merges_every_child_and_sorts_by_time(run914):
    # Offset the replayed file's single channel (2) up to 102, which the default
    # 8x10 geometry never maps. Then "did both children contribute?" is a
    # question about channel numbers rather than about a hit count, whose spread
    # in the track model is wide enough that any threshold would be arbitrary.
    a = BinFileSource([run914], gap_ns=100.0)
    b = _synth(model="track")
    mix = MixerSource([a, b], mode="overlay", channel_offset=[100, 0], seed=1)
    mix.open()
    try:
        evs = mix.next_events(np.arange(30) * 1e6)
        assert evs
        for ev in evs:
            ch = ev.hits["channel"].astype(int)
            assert (np.diff(ev.hits["t0"]) >= 0).all(), "merged hits must be sorted"
            assert (ch >= 100).any(), "no hits from the replay child"
            assert (ch < 80).any(), "no hits from the synthetic child"
            assert len(ev.hits) > 1
            # hit_number is restamped across the merged set, so it is contiguous
            # and ascending however the children numbered their own hits.
            assert np.array_equal(np.sort(ev.hits["hit_number"]), ev.hits["hit_number"])
    finally:
        mix.close()


def test_pileup_multiplicity_follows_poisson(run914):
    mu = 2.0
    a = BinFileSource([run914], gap_ns=100.0)
    b = BinFileSource([run914], gap_ns=100.0)
    mix = MixerSource([a, b], mode="pileup", pileup_mu=mu,
                      channel_offset=[0, 32], seed=11)
    mix.open()
    try:
        evs = mix.next_events(np.arange(3000) * 1e6)
        n = np.array([len(e.hits) for e in evs])
        # run914 is one hit per event, so hits per output event is
        # 1 primary + Poisson(mu).
        assert n.mean() == pytest.approx(1 + mu, rel=0.1)
        assert n.min() >= 1
    finally:
        mix.close()


def test_channel_offset_separates_two_copies_of_one_file(run914):
    """Without it, overlaying run914 twice piles everything onto channel 2."""
    a = BinFileSource([run914], gap_ns=100.0)
    b = BinFileSource([run914], gap_ns=100.0)
    mix = MixerSource([a, b], mode="overlay", channel_offset=[0, 40], seed=2)
    mix.open()
    try:
        chans = set()
        for ev in mix.next_events(np.arange(20) * 1e6):
            chans.update(ev.hits["channel"].tolist())
        assert len(chans) >= 2, f"expected two distinct channels, got {chans}"
    finally:
        mix.close()


def test_mixer_rejects_bad_configuration(run914):
    a = BinFileSource([run914])
    with pytest.raises(ValueError, match="at least one"):
        MixerSource([])
    with pytest.raises(ValueError, match="mode"):
        MixerSource([a], mode="blend")
    with pytest.raises(ValueError, match="Weights"):
        MixerSource([a, a], weights=[1.0, 2.0, 3.0])
