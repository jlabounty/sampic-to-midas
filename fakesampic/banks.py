"""Turning source events into AD00/AT00 bank payloads.

Used by BOTH the offline generator (`fakesampic.offline`) and the live frontend
(`fakesampic.frontend`), which is the point: the byte-identity gate compares a
file written by one against a file written by the other, and that only proves
something if the two share this code rather than merely resembling each other.

Batching is not an optimisation detail, it is the interface. `build_batch` takes
every event of a readout tick and calls `converter.sampic_banks.build_ad_records`
once on all their hits together. That function is elementwise, so grouping
cannot change an output byte, but calling it per event costs numpy's per-call
overhead once per event -- which at a few kHz is the difference between a few
percent of a core and not keeping up.
"""

from typing import List, Sequence, Tuple

import numpy as np

from converter import sampic_banks

from .sources.base import SourceEvent, SourceMeta

TID_BYTE = 1
"""MIDAS TID_BYTE. Hardcoded so this module does not import midas."""

BankList = List[Tuple[str, int, bytes]]


def build_batch(events: Sequence[SourceEvent], meta: SourceMeta,
                timing_bank: bool = True) -> List[BankList]:
    """Bank payloads for each event, converting the whole batch in one pass."""
    if not events:
        return []

    hit_arrays = [ev.hits for ev in events]
    joined = hit_arrays[0] if len(hit_arrays) == 1 else np.concatenate(hit_arrays)
    ad_all = sampic_banks.build_ad_records(
        joined, meta.fe_board_index, meta.inl_corrected, meta.adc_corrected)

    out: List[BankList] = []
    off = 0
    for ev in events:
        n = len(ev.hits)
        ad = ad_all[off:off + n]
        off += n
        banks: BankList = [(sampic_banks.AD_BANK_NAME, TID_BYTE, ad.tobytes())]
        if timing_bank:
            banks.append((sampic_banks.AT_BANK_NAME, TID_BYTE,
                          sampic_banks.build_at_payload(ev.t0_first_ns, n)))
        out.append(banks)
    return out
