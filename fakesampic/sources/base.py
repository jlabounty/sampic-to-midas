"""The contract every event source satisfies.

THE CENTRAL RULE: every source emits hits in `converter.sampic_bin.hit_dtype`,
never AD00 records. `converter.sampic_banks.build_ad_records` is then the single
place any source's hits become bank bytes, so the live frontend's output is
byte-identical to `converter.bin_to_mid`'s by construction rather than by a test
that someone has to remember to run. It also means a synthetic hit is subject to
exactly the same conversion -- including the int16 -> volts quantisation -- as a
replayed one.

`next_events()` receives the arrival times the pacer already decided on, rather
than inventing its own. A source therefore cannot forget to re-stamp: the
requested time is the only time it is handed.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

from converter.sampic_bin import hit_dtype
from converter.sampic_banks import AD_MAX_SAMPLES

HIT_DTYPE = hit_dtype(AD_MAX_SAMPLES)
"""The dtype every source emits. Full 64-slot waveforms, zero-padded if narrower.

`build_ad_records` copies `wf[:, :n_src]` into a fixed 64-slot field and leaves
the rest zero (sampic_banks.py:82-86), so widening a narrower file here produces
the same bytes it would have produced from the narrow array -- while letting the
mixer concatenate hits from sources that disagree about waveform length.
"""

HIT_NUMBER_WRAP = 0x7FFFFFFF
"""`hit_number` is int32 in the AD00 bank; wrap rather than overflow.

Reached after ~2.1e9 hits, which is hours at a high rate on an endless loop --
far from hypothetical for a frontend whose whole purpose is to run forever.
"""


@dataclass(frozen=True)
class SourceMeta:
    """Per-source values that become AD00 header fields or event timestamps."""

    fe_board_index: int = 0
    inl_corrected: bool = False
    adc_corrected: bool = False
    sampling_msps: int = 6400
    unix_time: float = 0.0


@dataclass
class SourceEvent:
    """One event's worth of hits, already stamped at its emission time."""

    hits: np.ndarray
    t0_first_ns: float


@dataclass
class SourceStats:
    events: int = 0
    hits: int = 0
    loops: int = 0
    exhausted: bool = False
    detail: str = ""


def widen(hits: np.ndarray) -> np.ndarray:
    """Return `hits` in HIT_DTYPE, zero-padding the waveform if it is narrower."""
    if hits.dtype == HIT_DTYPE:
        return hits
    n_src = hits["wf"].shape[1]
    if n_src > AD_MAX_SAMPLES:
        raise ValueError(
            f"waveform has {n_src} samples, more than the {AD_MAX_SAMPLES} the "
            "pi_midas AD00 format reserves")
    out = np.zeros(len(hits), dtype=HIT_DTYPE)
    for name in HIT_DTYPE.names:
        if name != "wf":
            out[name] = hits[name]
    out["wf"][:, :n_src] = hits["wf"]
    return out


def restamp(hits: np.ndarray, t_ns: float, hit_number: Optional[int] = None) -> np.ndarray:
    """Move `hits` onto the simulated clock at `t_ns`, preserving their structure.

    The absolute `t0` a file carries is never emitted; only the gaps WITHIN an
    event are. That is what makes an endless loop seamless: the join between the
    last event of a pass and the first of the next stops being a special case,
    because neither event's timestamp came from the file in the first place.

    `time` is kept as an offset from `t0` rather than rewritten from scratch, so
    whatever per-hit arrival structure the source had survives.

    Caveat inherited from the format: `time` is float32 (sampic_bin.py:126), so
    at large `t0` the offset degrades into quantisation noise -- at t = 60 s the
    float32 spacing is already 4096 ns. `Reset Clock At BOR` keeps `t0` small for
    exactly this reason; see `fakesampic.clock.RESET_WARN_NS`.
    """
    out = widen(hits).copy()
    if len(out) == 0:
        return out
    t0 = out["t0"]
    dt = t0 - t0[0]
    delta_time = out["time"].astype(np.float64) - t0
    out["t0"] = t_ns + dt
    out["time"] = (t_ns + dt + delta_time).astype("<f4")
    if hit_number is not None:
        n = len(out)
        out["hit_number"] = ((hit_number + np.arange(n, dtype=np.int64))
                             & HIT_NUMBER_WRAP).astype("<i4")
    return out


class EventSource:
    """Base class; see the module docstring for the contract."""

    name = "source"

    def __init__(self, meta: Optional[SourceMeta] = None):
        self.meta = meta if meta is not None else SourceMeta()
        self._stats = SourceStats()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def reset(self, t_ns: float) -> None:
        """Called at begin-of-run. Sources that stream from a file rewind here."""

    # -- production ----------------------------------------------------------

    def next_events(self, times_ns: Sequence[float]) -> List[SourceEvent]:
        """One event per requested arrival time, in order.

        May return FEWER than requested -- an exhausted non-looping file is the
        normal case. That is not an error and must not stop the run; the caller
        reports it as a status instead.
        """
        raise NotImplementedError

    def native_gaps_ns(self, n: int) -> Optional[np.ndarray]:
        """Gaps this source would like between its next `n` events, or None.

        Only a replay source has an opinion (the file's own timing). Returning
        None means "no native timing, use the configured rate".
        """
        return None

    # -- reporting -----------------------------------------------------------

    def stats(self) -> SourceStats:
        return self._stats

    def describe(self) -> str:
        return self.name

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
