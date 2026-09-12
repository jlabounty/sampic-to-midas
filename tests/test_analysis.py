"""What the analyzer accumulates, driven by the generator rather than MIDAS."""

import numpy as np
import pytest

from converter.sampic_banks import AD_HIT_DTYPE
from fakesampic.analysis import SampicAnalysis
from fakesampic.banks import build_batch
from fakesampic.geometry import build_geometry
from fakesampic.sources.synthetic import SyntheticSource
from fakesampic.template import PulseTemplate


def ad_events(n=120, seed=5, geometry=None):
    """Events as the analyzer sees them: decoded AD00 records."""
    g = geometry or build_geometry()
    src = SyntheticSource(geometry=g, seed=seed)
    src.open()
    out = []
    for ev in src.next_events(np.arange(n) * 1e6):
        payload = build_batch([ev], src.meta)[0][0][2]
        out.append(np.frombuffer(payload, dtype=AD_HIT_DTYPE))
    return g, out


@pytest.fixture(scope="module")
def filled():
    g, evs = ad_events()
    a = SampicAnalysis(geometry=g, per_strip=True)
    for ad in evs:
        a.add_event(ad)
    return g, a, evs


def test_counts_every_event_and_hit(filled):
    g, a, evs = filled
    assert a.events == len(evs)
    assert a.hits == sum(len(e) for e in evs)


def test_amplitude_and_occupancy_agree_with_the_input(filled):
    g, a, evs = filled
    all_hits = np.concatenate(evs)
    assert a.store.get("amp/all").entries == len(all_hits)
    occ = a.store.get("occ/channel")
    # Occupancy must reproduce the actual channel distribution.
    counts = np.bincount(all_hits["channel"].astype(int), minlength=occ.nbins)
    assert occ.counts == pytest.approx(counts[:occ.nbins])


def test_per_strip_histograms_exist_for_every_mapped_channel(filled):
    g, a, _ = filled
    for ch in g.mapped_channels:
        ch = int(ch)
        label = f"{g.planes[int(g.channel_plane[ch])].name}_s{int(g.channel_strip[ch]):02d}"
        assert a.store.get(f"amp/strip/{label}") is not None
        assert a.store.get(f"baseline/strip/{label}") is not None


def test_per_strip_entries_sum_to_the_aggregate(filled):
    """The per-strip set must partition the same hits, not double count."""
    g, a, _ = filled
    total = sum(a.store.get(n).entries for n in a.names()
                if n.startswith("amp/strip/"))
    assert total == a.store.get("amp/all").entries


def test_per_strip_distinguishes_centre_from_edge():
    """The point of per-strip: a plane aggregate hides this difference.

    Uses a NARROW beam on purpose. The default 1.5 mm sigma is wider than the
    detector's own +-2.25 mm half-width, so occupancy across the strips is
    fairly flat (measured ratio ~2.4) and a threshold on it would be testing
    the beam parameters rather than the per-strip machinery.
    """
    g = build_geometry()
    src = SyntheticSource(geometry=g, beam_sigma_mm=0.4, seed=5)
    src.open()
    a = SampicAnalysis(geometry=g, per_strip=True)
    for ev in src.next_events(np.arange(400) * 1e6):
        payload = build_batch([ev], src.meta)[0][0][2]
        a.add_event(np.frombuffer(payload, dtype=AD_HIT_DTYPE))

    per_strip = [a.store.get(f"amp/strip/{g.planes[0].name}_s{s:02d}").entries
                 for s in range(g.n_strips)]
    assert sum(per_strip) > 0
    centre = max(per_strip[4:6])
    edge = max(per_strip[0], per_strip[-1])
    assert centre > 20 * max(edge, 1), f"per-strip profile was {per_strip}"
    # And the aggregate genuinely cannot show this: it is one number.
    assert a.store.get("amp/all").entries == sum(
        a.store.get(n).entries for n in a.names() if n.startswith("amp/strip/"))


def test_can_be_switched_off(filled):
    g, _, evs = filled
    a = SampicAnalysis(geometry=g, per_strip=False)
    a.add_event(evs[0])
    assert not any(n.startswith("amp/strip/") for n in a.names())
    assert a.store.get("amp/all").entries > 0


def test_persistence_holds_the_most_recent_waveforms(filled):
    g, a, evs = filled
    p = a.store.persistence
    assert p.filled_channels(), "nothing recorded"
    ch = p.filled_channels()[0]
    assert p.count(ch) <= p.depth
    # Stored as raw int16 counts, which is what the format holds.
    assert p.waveforms(ch).dtype == np.dtype("<i2")
    assert abs(int(np.median(p.waveforms(ch)))) > 1000


def test_plane_vs_strip_occupancy_is_oriented_correctly(filled):
    g, a, _ = filled
    h = a.store.get("occ/plane_vs_strip")
    assert h.counts.shape == (g.n_planes, g.n_strips)
    assert h.counts.sum() > 0


def test_template_histograms_appear_only_with_a_template():
    g, evs = ad_events(n=40)
    plain = SampicAnalysis(geometry=g)
    assert plain.store.get("shape/template_rms") is None

    wf = np.rint(np.concatenate(evs)["waveform"] * 1e4).astype("<i2")
    hits = np.concatenate(evs)
    t = PulseTemplate.from_waveforms(wf, hits["baseline"], hits["amplitude"])
    withT = SampicAnalysis(geometry=g, template=t)
    for ad in evs:
        withT.add_event(ad)
    assert withT.store.get("shape/template_rms").entries == len(hits)
    assert withT.store.get("shape/rms_vs_amp").entries == len(hits)
    # And per strip, because which channel changed shape is the whole question.
    assert any(n.startswith("shape/strip/") for n in withT.names())


def test_reset_clears_counters_and_persistence(filled):
    g, _, evs = filled
    a = SampicAnalysis(geometry=g)
    for ad in evs[:10]:
        a.add_event(ad)
    a.reset()
    assert a.events == 0 and a.hits == 0
    assert a.store.get("amp/all").entries == 0
    assert a.store.persistence.filled_channels() == []


def test_empty_event_is_ignored(filled):
    g, _, _ = filled
    a = SampicAnalysis(geometry=g)
    a.add_event(np.zeros(0, dtype=AD_HIT_DTYPE))
    assert a.events == 0


def test_status_describes_the_configuration(filled):
    g, a, _ = filled
    st = a.status()
    assert st["events"] == a.events and st["per_strip"] is True
    assert st["persistence_depth"] > 0
    assert st["template"] is None
