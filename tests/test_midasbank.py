"""The TID_BYTE fast path, and the framing that matches the offline writer.

Skipped where MIDAS is not installed; everything else in this suite runs
without it.
"""

import struct

import numpy as np
import pytest

pytest.importorskip("midas.event", reason="MIDAS python bindings not installed")

import midas          # noqa: E402
import midas.event    # noqa: E402

from fakesampic.midasbank import build_event, byte_bank  # noqa: E402


def _packable(event):
    event.header.serial_number = 0
    event.header.timestamp = 0
    event.populate_bank_and_event_size()
    return event


def test_bank_framing_matches_the_offline_writer():
    """align64 -> flags 0x31, what converter/midas_writer.py writes and
    tools/validate_midas.py asserts. The midas.event.Event default is 0x11."""
    ev = build_event(1, 0, [byte_bank("AD00", np.zeros(344, np.uint8))])
    assert ev.flags == 0x31


def test_byte_bank_takes_the_memmove_fast_path(monkeypatch):
    """THE regression test for the performance landmine.

    Event.pack() has two branches for bank data: a numpy one that memmoves, and
    a fallback that does struct.pack_into("<NB", ..., *bank.data) -- expanding
    the payload into one Python int per byte, 88x slower at 256 hits.

    Asserting on elapsed time would be flaky on a shared machine, and the
    failure it guards against is silent rather than slow-and-obvious. So break
    the slow path instead: if pack() still succeeds, it did not use it.
    """
    ev = _packable(build_event(1, 0, [byte_bank("AD00", np.arange(344, dtype=np.uint8))]))

    real_pack_into = struct.pack_into

    def exploding_pack_into(fmt, *args, **kwargs):
        if "B" in fmt and fmt not in ("<HHIII", "<II"):
            raise AssertionError(
                "Event.pack() fell back to per-byte struct.pack_into for a "
                "TID_BYTE bank -- the numpy fast path was lost. See "
                "fakesampic/midasbank.py.")
        return real_pack_into(fmt, *args, **kwargs)

    monkeypatch.setattr(struct, "pack_into", exploding_pack_into)
    packed = ev.pack()
    assert len(packed) > 344


def test_fast_path_output_is_identical_to_create_bank():
    """The optimisation must not change a single byte."""
    payload = np.arange(344 * 3, dtype=np.uint8).tobytes()

    fast = _packable(build_event(1, 0, [byte_bank("AD00", payload)]))

    slow = midas.event.Event(bank32=True, align64=True)
    slow.header.event_id = 1
    slow.header.trigger_mask = 0
    slow.create_bank("AD00", midas.TID_BYTE, payload)
    _packable(slow)

    assert bytes(fast.pack()) == bytes(slow.pack())


def test_bank_names_must_be_four_characters():
    """bk_create truncates or overruns silently; a wrong name becomes a decoder
    mystery rather than an error here."""
    for bad in ("AD0", "AD000", ""):
        with pytest.raises(ValueError, match="4 characters"):
            byte_bank(bad, b"x" * 8)


def test_empty_payload_is_refused():
    """event.py guards the fast path with len(data) > 0, so an empty bank would
    silently take the slow path -- and is not a meaningful thing to send."""
    with pytest.raises(ValueError, match="empty payload"):
        byte_bank("AD00", b"")


def test_serial_and_timestamp_are_left_for_the_framework():
    """The framework assigns serials that increment across a batch and reset at
    begin-of-run; setting them here would fight it."""
    ev = build_event(1, 0, [byte_bank("AD00", np.zeros(8, np.uint8))])
    assert ev.header.serial_number is None
    assert ev.header.timestamp is None


def test_multiple_banks_keep_their_order():
    ev = _packable(build_event(1, 0, [
        byte_bank("AD00", np.zeros(344, np.uint8)),
        byte_bank("AT00", np.ones(56, np.uint8)),
    ]))
    assert list(ev.banks.keys()) == ["AD00", "AT00"]
