"""Minimal pure-python reader for plain (uncompressed) MIDAS .mid files.

Parse rules mirror midasio's TMEvent::FindFirstBank/FindNextBank so that a
file accepted here is also accepted by pi_midas. Used by the round-trip
validator, inspect_mid.py, and (optionally) notebooks — no MIDAS install
needed.
"""

import struct
from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

import numpy as np

from .midas_writer import BOR_EVENT_ID, EOR_EVENT_ID
from .sampic_banks import AD_HIT_DTYPE, AT_RECORD

EVENT_HEADER = struct.Struct("<HHIII")
TID_LAST = 19


class MidError(RuntimeError):
    pass


@dataclass
class MidEvent:
    event_id: int
    trigger_mask: int
    serial: int
    time_stamp: int
    data_size: int
    flags: int = 0
    banks: List[Tuple[str, int, bytes]] = field(default_factory=list)

    @property
    def is_special(self) -> bool:
        return self.event_id in (BOR_EVENT_ID, EOR_EVENT_ID)


def _parse_banks(payload: bytes, flags: int, event_index: int):
    banks = []
    pos = 0
    end = len(payload)
    while pos < end:
        if end - pos < 8:
            raise MidError(f"event {event_index}: {end - pos} residual bytes at end")
        name = payload[pos:pos + 4].decode("ascii", errors="replace")
        if flags & (1 << 5):      # bk_init32a
            tid, size = struct.unpack_from("<II", payload, pos + 4)
            data_off = pos + 16
        elif flags & (1 << 4):    # bk_init32
            tid, size = struct.unpack_from("<II", payload, pos + 4)
            data_off = pos + 12
        else:                     # bk_init (16-bit)
            tid, size = struct.unpack_from("<HH", payload, pos + 4)
            data_off = pos + 8
        if not (1 <= tid < TID_LAST):
            raise MidError(f"event {event_index}: bank {name!r} invalid tid {tid}")
        aligned = (size + 7) & ~7
        if data_off + aligned > end:
            raise MidError(f"event {event_index}: bank {name!r} overruns event")
        banks.append((name, tid, payload[data_off:data_off + size]))
        pos = data_off + aligned
    return banks


def iter_events(path: str, include_special: bool = False) -> Iterator[MidEvent]:
    with open(path, "rb") as f:
        index = 0
        while True:
            hdr = f.read(EVENT_HEADER.size)
            if not hdr:
                return
            if len(hdr) != EVENT_HEADER.size:
                raise MidError(f"event {index}: truncated event header")
            event_id, mask, serial, ts, data_size = EVENT_HEADER.unpack(hdr)
            payload = f.read(data_size)
            if len(payload) != data_size:
                raise MidError(f"event {index}: truncated payload")
            ev = MidEvent(event_id, mask, serial, ts, data_size)
            if ev.is_special:
                if include_special:
                    yield ev
                index += 1
                continue
            if data_size < 8:
                raise MidError(f"event {index}: payload too small for bank header")
            bank_hdr_size, flags = struct.unpack_from("<II", payload, 0)
            if bank_hdr_size + 8 != data_size:
                raise MidError(
                    f"event {index}: bank header size {bank_hdr_size} "
                    f"inconsistent with data size {data_size}")
            ev.flags = flags
            ev.banks = _parse_banks(payload[8:], flags, index)
            yield ev
            index += 1


def decode_ad(payload: bytes) -> np.ndarray:
    if len(payload) % AD_HIT_DTYPE.itemsize != 0:
        raise MidError(
            f"AD payload of {len(payload)} bytes is not a multiple of "
            f"{AD_HIT_DTYPE.itemsize}")
    return np.frombuffer(payload, dtype=AD_HIT_DTYPE)


def decode_at(payload: bytes) -> tuple:
    if len(payload) != AT_RECORD.size:
        raise MidError(f"AT payload must be {AT_RECORD.size} bytes, got {len(payload)}")
    return AT_RECORD.unpack(payload)
