"""Minimal MIDAS file writer (bk_init32a bank format).

Byte layout mirrors midasio's own writer (TMEvent::Init / TMEvent::AddBank in
midasio.cxx) and what pi_midas' PIMidasSelector/TMEvent::FindNextBank parse:

  event   : <HHIII  event_id, trigger_mask, serial_number, time_stamp, data_size
  payload : <II     bank_header_data_size (= data_size - 8), flags = 0x31
  bank    : name[4] + <III tid, bank_data_size, 0(reserved)  + data, zero-padded
            to 8-byte alignment
  BOR/EOR : event_id 0x8000 / 0x8001, serial_number = run number, small dummy
            payload standing in for the ODB dump (PIMidasSelector reads and
            skips them by event_id). The payload must NOT be empty: with
            data_size == 0, PIMidasSelector::next() evaluates &data[16] on a
            16-byte vector, which trips the bounds assertion in debug-STL
            builds (real MIDAS files always carry an ODB dump there).
            It must also be a valid JSON document: PIMidasSelector::ReadBOR()
            passes the BOR payload to PIODBHeader::SetJson(), which parses it
            with nlohmann::json and throws json::parse_error on anything else.
            Real ODB dumps are NUL-terminated JSON, so we emit that too.

Only plain uncompressed files are written; name them *.mid so midasio's
TMNewReader picks the plain reader by extension.
"""

import struct
import time
from typing import Iterable, Tuple, Union

EVENT_HEADER = struct.Struct("<HHIII")
BANK_HEADER = struct.Struct("<II")
BANK32A = struct.Struct("<III")

BANK_HEADER_FLAGS_32A = 0x31  # bk_init32a: bit 5 selects the 32a parse path
TID_BYTE = 1                  # payload is an opaque byte blob to the decoder

BOR_EVENT_ID = 0x8000
EOR_EVENT_ID = 0x8001

Bank = Tuple[str, int, Union[bytes, memoryview]]


def _align8(n: int) -> int:
    return (n + 7) & ~7


class MidasFileWriter:
    def __init__(self, path: str, buffer_bytes: int = 1 << 20,
                 odb_json: bytes = None):
        """`odb_json` is the BOR/EOR payload: a JSON document standing in for
        the ODB dump a real MIDAS run would carry. See the note above -- it
        must be valid JSON. Defaults to a minimal placeholder.
        """
        self._f = open(path, "wb", buffering=buffer_bytes)
        self.events_written = 0
        self.bytes_written = 0
        if odb_json is not None:
            if not odb_json.endswith(b"\x00"):
                odb_json += b"\x00"
            self._SPECIAL_PAYLOAD = odb_json

    def write_event(self, event_id: int, trigger_mask: int, serial: int,
                    time_stamp: int, banks: Iterable[Bank]):
        chunks = []
        payload_size = 0
        for name, tid, data in banks:
            if len(name) != 4:
                raise ValueError(f"MIDAS bank name must be 4 chars: {name!r}")
            data = bytes(data)
            padded = _align8(len(data))
            chunks.append(name.encode("ascii"))
            chunks.append(BANK32A.pack(tid, len(data), 0))
            chunks.append(data)
            if padded != len(data):
                chunks.append(b"\x00" * (padded - len(data)))
            payload_size += 4 + BANK32A.size + padded

        data_size = BANK_HEADER.size + payload_size
        self._f.write(EVENT_HEADER.pack(event_id, trigger_mask, serial,
                                        time_stamp, data_size))
        self._f.write(BANK_HEADER.pack(data_size - 8, BANK_HEADER_FLAGS_32A))
        for chunk in chunks:
            self._f.write(chunk)
        self.events_written += 1
        self.bytes_written += EVENT_HEADER.size + data_size

    def write_bor(self, run_number: int, time_stamp: int = None):
        self._write_special(BOR_EVENT_ID, run_number, time_stamp)

    def write_eor(self, run_number: int, time_stamp: int = None):
        self._write_special(EOR_EVENT_ID, run_number, time_stamp)

    # Valid JSON, NUL-terminated: see the ODB note in the module docstring.
    _SPECIAL_PAYLOAD = b'{"Experiment":{"Name":"fake-sampic"}}\x00'

    def _write_special(self, event_id: int, run_number: int, time_stamp: int):
        if time_stamp is None:
            time_stamp = int(time.time())
        payload = self._SPECIAL_PAYLOAD
        self._f.write(EVENT_HEADER.pack(event_id, 0, run_number, time_stamp,
                                        len(payload)))
        self._f.write(payload)
        self.events_written += 1
        self.bytes_written += EVENT_HEADER.size + len(payload)

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
