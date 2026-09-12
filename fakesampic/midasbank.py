"""Building MIDAS events without the slow path.

One of only two modules here that import `midas`.

`Event.create_bank()` refuses numpy input for TID_BYTE (event.py:454, "Data must
be a bytes() or bytearray()"), so the obvious call --

    event.create_bank("AD00", midas.TID_BYTE, ad_records.tobytes())

-- stores a `bytes`, which then misses `Event.pack()`'s numpy branch
(event.py:611) and lands on

    struct.pack_into("<%dB" % n, buf, off, *(bank.data))      # event.py:626

which explodes an N-byte payload into N separate Python integers. `add_bank()`
(event.py:400) does no type validation, so we build the Bank ourselves, keep the
payload as a uint8 array, and get `tobytes()` + `ctypes.memmove` instead.

Measured here (python 3.14.7, numpy 2.5.2), both paths producing byte-for-byte
identical events:

    hits   payload   create_bank()      add_bank(uint8)
       1      344 B    4.8 us/ev          5.4 us/ev
      24     8256 B   54.5 us/ev  (18 kev/s)    3.8 us/ev  (261 kev/s)
     256    88064 B  704.5 us/ev  (1.4 kev/s)   8.0 us/ev  (126 kev/s)

So the naive path alone caps an 8-plane track event (~24 hits) at 18 kHz and a
mixer overlay (~256 hits) at 1.4 kHz -- below what this frontend is asked to
produce, and it looks like "Python is slow" rather than like a fixable bug.

At exactly one hit the naive path is marginally FASTER, because `np.frombuffer`
costs more than packing 344 ints. That is not worth branching on: both are
~200 kev/s there, far above any rate this frontend targets, and a size-dependent
code path would be a second thing to keep correct for no measurable gain.

`tests/test_midasbank_fastpath.py` is the regression test, and it asserts the
branch taken rather than the time elapsed -- a timing assertion on a shared
machine is a flaky test, and the failure being guarded against is silent.
"""

import numpy as np

import midas
import midas.event


def byte_bank(name: str, payload) -> "midas.event.Bank":
    """A TID_BYTE bank that takes `Event.pack()`'s memmove fast path.

    `payload` is anything supporting the buffer protocol -- typically
    `numpy_structured_array.tobytes()` or the array itself. No copy is made of a
    numpy input beyond the uint8 reinterpretation, so the caller must not mutate
    it before the event is packed.
    """
    if len(name) != 4:
        # bk_create truncates or overruns silently; a wrong bank name is then a
        # decoder mystery rather than an error here.
        raise ValueError(f"MIDAS bank names are exactly 4 characters, got {name!r}")
    data = np.frombuffer(payload, dtype=np.uint8)
    if data.size == 0:
        # event.py:611 guards the fast path with `len(bank.data) > 0`, so an
        # empty bank silently falls back to the slow path. It is also not a
        # meaningful thing to send.
        raise ValueError(f"bank {name} has an empty payload")
    bank = midas.event.Bank()
    bank.name = name
    bank.type = midas.TID_BYTE
    bank.data = data
    return bank


def build_event(event_id: int, trigger_mask: int, banks) -> "midas.event.Event":
    """One MIDAS event carrying `banks`, framed like `converter.midas_writer`.

    `align64=True` is load-bearing: it sets bank-header flags 0x31 (bk_init32a,
    a 16-byte bank header), which is what `converter/midas_writer.py:29` writes
    and what `tools/validate_midas.py:75` asserts. The `midas.event.Event`
    default is align64=False -> 0x11, which the Gaudi unpacker would read
    differently.

    Serial number and timestamp are deliberately left unset: the frontend
    framework fills them in (frontend.py:672-682), assigning serials that
    increment correctly across a batch and reset at begin-of-run.
    """
    event = midas.event.Event(bank32=True, align64=True)
    event.header.event_id = event_id
    event.header.trigger_mask = trigger_mask
    for bank in banks:
        event.add_bank(bank)
    return event
