"""The byte-format guarantee: fake data must be indistinguishable from real.

These pin the layout that `main/reco_testbeam/pi_midas` reads. A change here is
a format change and breaks the Gaudi unpacker, so these tests exist to make that
impossible to do by accident.
"""

import hashlib
import subprocess
import sys

import numpy as np
import pytest

from converter.sampic_banks import (AD_BANK_NAME, AD_HIT_DTYPE, AD_MAX_SAMPLES,
                                    AT_BANK_NAME, AT_RECORD, build_ad_records,
                                    build_at_payload)
from converter.sampic_bin import hit_dtype
from fakesampic.banks import build_batch
from fakesampic.sources.base import SourceEvent, SourceMeta

# EventBankUnpacker.hh: HitHeader (11 x i32) + 64 x f32 + HitScalars.
EXPECTED_ITEMSIZE = 344
EXPECTED_AT_SIZE = 56


def test_record_sizes_are_the_pi_midas_contract():
    assert AD_HIT_DTYPE.itemsize == EXPECTED_ITEMSIZE
    assert AT_RECORD.size == EXPECTED_AT_SIZE
    assert AD_MAX_SAMPLES == 64
    assert AD_BANK_NAME == "AD00" and len(AD_BANK_NAME) == 4
    assert AT_BANK_NAME == "AT00" and len(AT_BANK_NAME) == 4


def test_field_offsets_are_frozen():
    """Offsets, not just the total size -- a swapped pair keeps itemsize 344."""
    expected = {
        "fe_board_index": 0, "channel": 4, "hit_number": 8, "sampic_index": 12,
        "channel_index": 16, "data_size": 20, "inl_corrected": 24,
        "adc_corrected": 28, "residual_pedestal_corrected": 32, "cell_info": 36,
        "first_cell_physical_index": 40, "waveform": 44,
        "raw_tot_value": 300, "tot_value": 304, "amplitude": 308,
        "baseline": 312, "peak": 316, "time_index": 320, "time_instant": 324,
        "time_amplitude": 332, "first_cell_timestamp": 336,
    }
    actual = {n: AD_HIT_DTYPE.fields[n][1] for n in AD_HIT_DTYPE.names}
    assert actual == expected


def test_golden_blob():
    """A fixed input must always produce these exact bytes."""
    hits = np.zeros(2, dtype=hit_dtype(64))
    hits["hit_number"] = [1, 2]
    hits["channel"] = [3, 19]
    hits["t0"] = [1234.5, 1240.25]
    hits["raw_tot"] = [65535, 65535]
    hits["tot"] = [-1.0, -1.0]
    hits["time"] = [1236.0, 1242.0]
    hits["baseline"] = [0.7459, 0.7461]
    hits["amplitude"] = [0.06, 0.0125]
    hits["first_cell"] = [7, 61]
    hits["wf_size"] = 64
    hits["wf"] = np.arange(128, dtype="<i2").reshape(2, 64)

    ad = build_ad_records(hits, fe_board_index=0,
                          inl_corrected=True, adc_corrected=True)
    digest = hashlib.sha256(ad.tobytes()).hexdigest()
    assert len(ad.tobytes()) == 2 * EXPECTED_ITEMSIZE
    assert digest == "3cf337dfbd3b2793e9fdf201b94726da93750e060bcf330345cd5f9a5215c9c7", (
        "AD00 bytes changed. If this was deliberate it is a FORMAT change: "
        "pi_midas and every recorded .mid file read the old layout.")


def test_derived_fields_follow_the_documented_mapping():
    hits = np.zeros(1, dtype=hit_dtype(64))
    hits["channel"] = 19
    hits["baseline"] = 0.5
    hits["amplitude"] = 0.25
    hits["wf_size"] = 40
    hits["t0"] = 999.0
    hits["time"] = 1001.0
    ad = build_ad_records(hits, 3, True, False)
    assert ad["fe_board_index"][0] == 3
    assert ad["sampic_index"][0] == 19 // 16, "sampic_index = channel // 16"
    assert ad["channel_index"][0] == 19 % 16
    assert ad["data_size"][0] == 40
    assert ad["inl_corrected"][0] == 1 and ad["adc_corrected"][0] == 0
    assert ad["peak"][0] == pytest.approx(0.75), "peak = baseline + amplitude"
    assert ad["first_cell_timestamp"][0] == pytest.approx(999.0)


def test_waveform_is_volts_and_unused_slots_are_zero():
    hits = np.zeros(1, dtype=hit_dtype(64))
    hits["wf_size"] = 10
    hits["wf"][0, :10] = 10000        # 10000 counts = 1.0 V
    ad = build_ad_records(hits, 0, True, True)
    assert ad["waveform"][0, :10] == pytest.approx(np.ones(10))
    assert (ad["waveform"][0, 10:] == 0).all(), "pi_midas reads all 64 slots"


def test_at_payload_reports_the_first_hit_time_and_count():
    payload = build_at_payload(123456.7, 5)
    assert len(payload) == EXPECTED_AT_SIZE
    fields = AT_RECORD.unpack(payload)
    assert fields[0] == 123457        # rounded to integer ns
    assert fields[1] == 5 and fields[2] == 5
    assert fields[3:] == (0,) * 10


def test_build_batch_is_grouping_independent():
    """Batching must be an optimisation, never a change in output."""
    rng = np.random.default_rng(5)
    meta = SourceMeta(fe_board_index=1, inl_corrected=True, adc_corrected=True)
    events = []
    for _ in range(7):
        n = int(rng.integers(1, 6))
        h = np.zeros(n, dtype=hit_dtype(64))
        h["channel"] = rng.integers(0, 64, n)
        h["t0"] = np.sort(rng.uniform(0, 1e5, n))
        h["amplitude"] = rng.uniform(0.01, 0.1, n)
        h["wf_size"] = 64
        h["wf"] = rng.integers(0, 9000, (n, 64))
        events.append(SourceEvent(h, float(h["t0"][0])))

    together = build_batch(events, meta)
    one_at_a_time = [build_batch([e], meta)[0] for e in events]
    assert together == one_at_a_time
