"""Reader for SAMPIC standalone binary files (.bin/.dat).

Format reference: unpacker/preprocess/src/TBDatReader.cpp and
unpacker/preprocess/include/TBHeader.hpp. Only DATA_IN_FILE_TYPE 1
(WAVEFORM_AND_MEASUREMENTS) with COMPACT BINARY DATA is supported, which is
what the SAMPIC software writes for the ATAR demonstrator runs.

A file is 7 ASCII header lines terminated by '\\n', followed by packed
little-endian hit records:

    HitNumber i32, Channel u8, FirstSampleTimeStamp f64 (ns), RawToTValue u16,
    TOTValue f32 (ns), Time f32 (ns), Baseline f32 (V), Amplitude f32 (V),
    FirstCellIndex u8, WaveformSize u8, WaveformSize x i16 samples

so a record is 33 + 2*WaveformSize bytes (161 bytes for the usual 64 samples).
Sample value / 10000.0 = volts.

Note the descriptive header lines 6-7 in the file itself are a generic
template and do not match the compact type-1 record (they claim Time is a
double and list a RawPeak field); the layout above was verified byte-for-byte
against real data and the TBDatReader code.
"""

import mmap
import re
import struct
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

N_HEADER_LINES = 7
FIXED_RECORD_BYTES = 33  # bytes before the waveform samples
WFSIZE_OFFSET = 32       # offset of the WaveformSize byte within a record
MAX_SAMPLES = 64         # kMaxSamples in pi_midas sampic/EventBankUnpacker.hh

DATA_TYPE_WAVEFORM_AND_MEASUREMENTS = 1


class SampicBinError(RuntimeError):
    pass


@dataclass
class BinHeader:
    software_version: str
    unix_time: float
    n_channels: int
    fe_board_index: int
    sampling_freq_msps: int
    enabled_channels_mask: int
    data_in_file_type: int
    compact_binary: bool
    inl_corrected: bool
    adc_corrected: bool
    header_bytes: int
    raw_lines: tuple

    @property
    def dt_ns(self) -> float:
        """Sampling period in ns (0.15625 ns at 6400 MS/s)."""
        return 1e3 / self.sampling_freq_msps


def _search(pattern, text, cast, default=None):
    m = re.search(pattern, text)
    if m is None:
        if default is not None:
            return default
        raise SampicBinError(f"header field not found: /{pattern}/")
    return cast(m.group(1))


def parse_header(f) -> BinHeader:
    """Read the 7 ASCII header lines from a file object opened in binary mode.

    Leaves the file positioned at the first binary record byte.
    """
    lines = []
    for i in range(N_HEADER_LINES):
        line = f.readline()
        if not line.endswith(b"\n"):
            raise SampicBinError(f"header line {i + 1} is not newline-terminated")
        lines.append(line.decode("ascii", errors="replace"))
    header_bytes = sum(len(l) for l in lines)

    l1, l2, l3, l4 = lines[0], lines[1], lines[2], lines[3]
    l7 = lines[6]

    header = BinHeader(
        software_version=_search(r"SOFTWARE VERSION:\s*(\S+)", l1, str, default="unknown"),
        unix_time=_search(r"UnixTime\s*=\s*([0-9.]+)", l1, float, default=0.0),
        n_channels=_search(r"NB OF CHANNELS IN SYSTEM\s*(\d+)", l2, int, default=0),
        fe_board_index=_search(r"FRONT-END FPGA INDEX:\s*(\d+)", l2, int, default=0),
        sampling_freq_msps=_search(r"SAMPLING FREQUENCY\s*(\d+)", l3, int),
        enabled_channels_mask=_search(r"Enabled Channels Mask:\s*([0-9A-Fa-f]+)", l3,
                                      lambda s: int(s, 16), default=0),
        data_in_file_type=_search(r"DATA_IN_FILE_TYPE:\s*(\d+)", l4, int),
        compact_binary=_search(r"COMPACT BINARY DATA:\s*(YES|NO)", l4, str, default="NO") == "YES",
        inl_corrected=_search(r"INL Correction:\s*(ON|OFF)", l7, str, default="OFF") == "ON",
        adc_corrected=_search(r"ADC Correction:\s*(ON|OFF)", l7, str, default="OFF") == "ON",
        header_bytes=header_bytes,
        raw_lines=tuple(lines),
    )

    if header.data_in_file_type != DATA_TYPE_WAVEFORM_AND_MEASUREMENTS:
        raise SampicBinError(
            f"unsupported DATA_IN_FILE_TYPE {header.data_in_file_type} "
            "(only 1 = WAVEFORM_AND_MEASUREMENTS is supported)")
    if not header.compact_binary:
        raise SampicBinError("file does not declare COMPACT BINARY DATA: YES")
    return header


def hit_dtype(n_samples: int) -> np.dtype:
    """Packed record dtype for a constant waveform size (itemsize 33 + 2N)."""
    dt = np.dtype([
        ("hit_number", "<i4"),
        ("channel", "u1"),
        ("t0", "<f8"),          # FirstSampleTimeStamp, ns
        ("raw_tot", "<u2"),
        ("tot", "<f4"),
        ("time", "<f4"),
        ("baseline", "<f4"),
        ("amplitude", "<f4"),
        ("first_cell", "u1"),
        ("wf_size", "u1"),
        ("wf", "<i2", (n_samples,)),
    ])
    assert dt.itemsize == FIXED_RECORD_BYTES + 2 * n_samples
    return dt


_SLOW_PREFIX = struct.Struct("<iBdHffffBB")
assert _SLOW_PREFIX.size == FIXED_RECORD_BYTES


class BinFile:
    """A SAMPIC binary file, mmap'ed.

    When every record has the same WaveformSize (always the case in practice),
    `hits` is a zero-copy structured-array view over the whole file: memory use
    is bounded by the OS page cache regardless of file size. Otherwise `hits`
    is None and `iter_chunks()` falls back to a per-record parser that pads
    waveforms to MAX_SAMPLES.
    """

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        self.header = parse_header(self._f)
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self._body_offset = self.header.header_bytes
        body_size = len(self._mm) - self._body_offset
        if body_size <= 0:
            raise SampicBinError("file has no hit records after the header")

        # Probe WaveformSize of the first record and test the fixed-stride layout.
        n = self._mm[self._body_offset + WFSIZE_OFFSET]
        self.hits: Optional[np.ndarray] = None
        self.n_samples = None
        if n > 0 and body_size % (FIXED_RECORD_BYTES + 2 * n) == 0:
            view = np.frombuffer(self._mm, dtype=hit_dtype(n), offset=self._body_offset)
            if (view["wf_size"] == n).all():
                self.hits = view
                self.n_samples = n
        if self.hits is None and n > MAX_SAMPLES:
            raise SampicBinError(
                f"WaveformSize {n} exceeds the {MAX_SAMPLES}-sample limit of the "
                "pi_midas SAMPIC bank format")

    @property
    def fixed_layout(self) -> bool:
        return self.hits is not None

    def n_hits(self) -> Optional[int]:
        """Total number of records; None if only the slow path can tell."""
        return len(self.hits) if self.fixed_layout else None

    def t0_column(self) -> np.ndarray:
        """Timestamps of all hits (fixed layout only). Strided read, ~8 B/hit."""
        return self.hits["t0"]

    def iter_chunks(self, chunk_hits: int = 65536) -> Iterator[np.ndarray]:
        """Yield structured-array chunks (fixed layout: zero-copy slices)."""
        if self.fixed_layout:
            for start in range(0, len(self.hits), chunk_hits):
                yield self.hits[start:start + chunk_hits]
            return
        yield from self._iter_chunks_slow(chunk_hits)

    def _iter_chunks_slow(self, chunk_hits: int) -> Iterator[np.ndarray]:
        import warnings
        warnings.warn("variable WaveformSize detected: using slow per-record parser")
        dt = hit_dtype(MAX_SAMPLES)
        pos = self._body_offset
        end = len(self._mm)
        buf = np.zeros(chunk_hits, dtype=dt)
        n_buf = 0
        while pos < end:
            if end - pos < FIXED_RECORD_BYTES:
                raise SampicBinError(f"{end - pos} trailing bytes: truncated record")
            fields = _SLOW_PREFIX.unpack_from(self._mm, pos)
            wf_size = fields[9]
            if wf_size > MAX_SAMPLES:
                raise SampicBinError(
                    f"WaveformSize {wf_size} exceeds the {MAX_SAMPLES}-sample limit")
            rec_end = pos + FIXED_RECORD_BYTES + 2 * wf_size
            if rec_end > end:
                raise SampicBinError("truncated waveform at end of file")
            rec = buf[n_buf]
            (rec["hit_number"], rec["channel"], rec["t0"], rec["raw_tot"],
             rec["tot"], rec["time"], rec["baseline"], rec["amplitude"],
             rec["first_cell"], rec["wf_size"]) = fields
            wf = np.frombuffer(self._mm, dtype="<i2", count=wf_size,
                               offset=pos + FIXED_RECORD_BYTES)
            rec["wf"][:wf_size] = wf
            rec["wf"][wf_size:] = 0
            n_buf += 1
            pos = rec_end
            if n_buf == chunk_hits:
                yield buf[:n_buf].copy()
                n_buf = 0
        if n_buf:
            yield buf[:n_buf].copy()

    def close(self):
        self.hits = None
        try:
            self._mm.close()
        except BufferError:
            # numpy views over the mmap may still be alive in the caller; the
            # mapping is reclaimed when they are garbage-collected.
            pass
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
