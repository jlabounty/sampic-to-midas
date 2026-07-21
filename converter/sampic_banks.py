"""AD/AT bank payload builders for the fake SAMPIC MIDAS frontend.

The layouts mirror what pi_midas expects, byte for byte:
  - AD (event) bank: back-to-back hits, each HitHeader (11 x i32) + a fixed
    64-slot float32 waveform (the reader truncates to data_size; unused slots
    must exist and are zero) + HitScalars. 344 bytes per hit.
    Contract: main/reco_testbeam/pi_midas/include/sampic/EventBankUnpacker.hh
  - AT (event timing) bank: exactly one 56-byte record.
    Contract: main/reco_testbeam/pi_midas/include/sampic/EventTimingBankUnpacker.hh

Bank names must be 4 chars; PITMidasSampic prefix-matches "AD"/"AT" and
excludes "AD%"/"AT%" (those belong to HDSoC).
"""

import struct

import numpy as np

AD_BANK_NAME = "AD00"
AT_BANK_NAME = "AT00"

AD_MAX_SAMPLES = 64  # kMaxSamples in EventBankUnpacker.hh

AD_HIT_DTYPE = np.dtype([
    # HitHeader (11 x int32)
    ("fe_board_index", "<i4"),
    ("channel", "<i4"),
    ("hit_number", "<i4"),
    ("sampic_index", "<i4"),
    ("channel_index", "<i4"),
    ("data_size", "<i4"),
    ("inl_corrected", "<i4"),
    ("adc_corrected", "<i4"),
    ("residual_pedestal_corrected", "<i4"),
    ("cell_info", "<i4"),
    ("first_cell_physical_index", "<i4"),
    # fixed 64-slot corrected waveform (volts)
    ("waveform", "<f4", (AD_MAX_SAMPLES,)),
    # HitScalars
    ("raw_tot_value", "<i4"),
    ("tot_value", "<f4"),
    ("amplitude", "<f4"),
    ("baseline", "<f4"),
    ("peak", "<f4"),
    ("time_index", "<f4"),
    ("time_instant", "<f8"),
    ("time_amplitude", "<f4"),
    ("first_cell_timestamp", "<f8"),
])
assert AD_HIT_DTYPE.itemsize == 344, AD_HIT_DTYPE.itemsize

AT_RECORD = struct.Struct("<QII10I")
assert AT_RECORD.size == 56

ADC_TO_VOLTS = np.float64(1.0e4)  # volts = int16 sample / 10000.0

CHANNELS_PER_SAMPIC = 16


def build_ad_records(hits: np.ndarray, fe_board_index: int,
                     inl_corrected: bool, adc_corrected: bool) -> np.ndarray:
    """Vectorized conversion of a chunk of .bin hit records to AD hit records.

    `hits` is a structured array from converter.sampic_bin (any constant or
    padded waveform width up to 64 slots).
    """
    n = len(hits)
    out = np.zeros(n, dtype=AD_HIT_DTYPE)

    channel = hits["channel"].astype(np.int32)
    out["fe_board_index"] = fe_board_index
    out["channel"] = channel
    out["hit_number"] = hits["hit_number"]
    out["sampic_index"] = channel // CHANNELS_PER_SAMPIC
    out["channel_index"] = channel % CHANNELS_PER_SAMPIC
    out["data_size"] = hits["wf_size"]
    out["inl_corrected"] = 1 if inl_corrected else 0
    out["adc_corrected"] = 1 if adc_corrected else 0
    # residual_pedestal_corrected, cell_info, time_index, time_amplitude:
    # not present in the standalone .bin format; left at 0.
    out["first_cell_physical_index"] = hits["first_cell"]

    n_src = hits["wf"].shape[1]
    wf_volts = (hits["wf"] / ADC_TO_VOLTS).astype("<f4")
    out["waveform"][:, :n_src] = wf_volts
    # slots at index >= wf_size are already zero: either n_src == wf_size for
    # every hit (fixed layout) or the slow path zero-padded the i16 source.

    out["raw_tot_value"] = hits["raw_tot"]
    out["tot_value"] = hits["tot"]
    out["amplitude"] = hits["amplitude"]
    out["baseline"] = hits["baseline"]
    out["peak"] = hits["baseline"] + hits["amplitude"]
    out["time_instant"] = hits["time"]
    out["first_cell_timestamp"] = hits["t0"]
    return out


def build_at_payload(t0_first_ns: float, nhits: int) -> bytes:
    """One EventTiming record: fe_timestamp_ns, nhits, nparents, 10 x 0."""
    return AT_RECORD.pack(int(round(t0_first_ns)), nhits, nhits, *([0] * 10))
