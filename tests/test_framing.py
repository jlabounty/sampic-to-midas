"""The brpc wire format.

fakesampic/framing.py and pages/js/sampic-brpc.js are one format in two
languages. These tests pin the python side; tests/js/framing.test.js decodes
bytes this module produced with the JS decoder. A field added on one side and
forgotten on the other fails one of the two.
"""

import struct

import numpy as np
import pytest

from fakesampic import framing
from fakesampic.hist import Hist1D, Hist2D, Persistence
from fakesampic.template import PulseTemplate


def test_every_reply_is_a_self_describing_envelope():
    for payload in (framing.encode_list(["a"]),
                    framing.encode_json({"a": 1}),
                    framing.encode_error("bad")):
        total, tag = framing.ENVELOPE.unpack_from(payload, 0)
        assert total == len(payload), "the envelope must state the true length"
        assert len(tag) == 4


def test_hist1_round_trip():
    h = Hist1D("amp/all", 32, 0.0, 0.2, xlabel="V", title="Amplitude")
    h.fill(np.linspace(0.0, 0.2, 500))
    h.fill([-1.0, 9.0])
    tag, back = framing.decode(framing.encode_hist1(h))
    assert tag == framing.TAG_HIST1
    assert back.name == h.name and back.xlabel == h.xlabel and back.title == h.title
    assert back.nbins == h.nbins
    assert (back.xlow, back.xhigh) == (h.xlow, h.xhigh)
    assert back.entries == h.entries
    assert back.underflow == h.underflow and back.overflow == h.overflow
    # Counts travel as float32, so exact equality only holds for small integers
    # -- which is what a histogram holds.
    assert back.counts == pytest.approx(h.counts, rel=1e-6)


def test_hist2_round_trip_preserves_orientation():
    h = Hist2D("occ", nx=5, xlow=0, xhigh=5, ny=3, ylow=-1, yhigh=2,
               xlabel="strip", ylabel="plane", title="Occupancy")
    h.fill([0.5, 4.5], [-0.5, 1.5])
    tag, back = framing.decode(framing.encode_hist2(h))
    assert tag == framing.TAG_HIST2
    assert back.counts.shape == (3, 5)
    assert back.counts == pytest.approx(h.counts)
    assert (back.ylow, back.yhigh) == (h.ylow, h.yhigh)
    assert back.ylabel == "plane"


def test_persistence_round_trip():
    p = Persistence(8, depth=3, n_samples=4)
    p.add([1, 1, 5], np.arange(12, dtype="<i2").reshape(3, 4))
    tag, back = framing.decode(framing.encode_persistence(p, [1, 5, 7]))
    assert tag == framing.TAG_PERSIST
    # Channel 7 has nothing, so it is omitted rather than sent as an empty block.
    assert sorted(back["channels"]) == [1, 5]
    assert back["channels"][1].shape == (2, 4)
    assert np.array_equal(back["channels"][1], p.waveforms(1))
    assert back["scale"] == pytest.approx(1e-4, rel=1e-6)


def test_persistence_of_nothing_is_a_valid_empty_reply():
    p = Persistence(4, depth=2, n_samples=4)
    tag, back = framing.decode(framing.encode_persistence(p, [0, 1]))
    assert tag == framing.TAG_PERSIST and back["channels"] == {}


def test_template_round_trip():
    t = PulseTemplate(samples=np.sin(np.linspace(0, np.pi, 20)),
                      name="n", source="s", created="c", n_averaged=5,
                      spread=np.full(20, 0.02))
    tag, back = framing.decode(framing.encode_template(t))
    assert tag == framing.TAG_TEMPLATE
    assert (back.name, back.source, back.created) == ("n", "s", "c")
    assert back.n_averaged == 5 and back.peak_index == t.peak_index
    assert back.samples == pytest.approx(t.samples, rel=1e-6)
    assert back.spread == pytest.approx(t.spread, rel=1e-6)


def test_template_without_spread():
    t = PulseTemplate(samples=np.array([0.0, 1.0, 0.5, 0.0]))
    tag, back = framing.decode(framing.encode_template(t))
    assert back.spread is None


def test_list_and_json_and_error():
    assert framing.decode(framing.encode_list(["a", "b"]))[1] == ["a", "b"]
    assert framing.decode(framing.encode_list([]))[1] == []
    assert framing.decode(framing.encode_json({"k": [1, 2]}))[1] == {"k": [1, 2]}
    assert framing.decode(framing.encode_error("nope"))[1] == "nope"


def test_unicode_labels_survive():
    h = Hist1D("t", 2, 0, 2, xlabel="Δt [ns]", title="µs")
    tag, back = framing.decode(framing.encode_hist1(h))
    assert back.xlabel == "Δt [ns]" and back.title == "µs"


def test_decode_refuses_a_mangled_envelope():
    good = framing.encode_json({"a": 1})
    with pytest.raises(ValueError, match="shorter than its envelope"):
        framing.decode(good[:4])
    with pytest.raises(ValueError, match="envelope says"):
        framing.decode(good + b"extra")
    bogus = struct.pack("<I4s", 8, b"zzzz")
    with pytest.raises(ValueError, match="unknown reply tag"):
        framing.decode(bogus)


def test_field_offsets_are_pinned():
    """The layout the JS decoder hardcodes. Changing it is a format change."""
    h = Hist1D("ab", 3, 1.0, 4.0, xlabel="x", title="t")
    buf = framing.encode_hist1(h)
    assert framing.ENVELOPE.size == 8
    version, nbins = struct.unpack_from("<II", buf, 8)
    assert (version, nbins) == (framing.VERSION, 3)
    xlow, xhigh = struct.unpack_from("<dd", buf, 16)
    assert (xlow, xhigh) == (1.0, 4.0)
    (entries,) = struct.unpack_from("<q", buf, 32)
    assert entries == 0
    under, over = struct.unpack_from("<dd", buf, 40)
    assert (under, over) == (0.0, 0.0)
    # then three length-prefixed strings, then nbins float32
    (name_len,) = struct.unpack_from("<H", buf, 56)
    assert name_len == 2
