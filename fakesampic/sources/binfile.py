"""Replay raw SAMPIC .bin files as a live, endless stream.

Reuses the offline reader wholesale -- `sampic_bin.BinFile` for the mmap'd
parse, `event_builder.iter_clusters` for the time-gap event building -- so a
replayed event is the same event `bin_to_mid` would have written from the same
file. That shared clustering is what makes the byte-identity gate in
`tools/compare_mid.py` meaningful.

Looping is seamless because emitted timestamps never come from the file (see
`base.restamp`): only the gaps within an event do. At the seam between one pass
and the next, the gap BETWEEN events is undefined -- the file's last event and
its first are not adjacent in any real sense -- so `loop_gap_ns` supplies one.
"""

import glob
import os
import random
from collections import deque
from typing import List, Optional, Sequence

import numpy as np

from converter import event_builder, sampic_bin

from .base import EventSource, SourceEvent, SourceMeta, SourceStats, restamp, widen


class BinFileSource(EventSource):
    """Endlessly replays one or more SAMPIC .bin files."""

    name = "binfile"

    def __init__(self, files: Sequence[str], gap_ns: float = 100.0,
                 loop: bool = True, loop_gap_ns: float = 1.0e6,
                 shuffle: bool = False, start_event: int = 0,
                 max_events_per_pass: int = 0, chunk_hits: int = 65536,
                 fe_board_index: Optional[int] = None,
                 restamp_times: bool = True, restamp_hit_number: bool = True,
                 seed: int = 0):
        super().__init__()
        self.patterns = list(files)
        self.files = self._expand(self.patterns)
        if not self.files:
            raise FileNotFoundError(
                f"no SAMPIC .bin files matched {self.patterns!r}")
        self.gap_ns = float(gap_ns)
        self.loop = bool(loop)
        self.loop_gap_ns = float(loop_gap_ns)
        self.shuffle = bool(shuffle)
        self.start_event = int(start_event)
        self.max_events_per_pass = int(max_events_per_pass)
        self.chunk_hits = int(chunk_hits)
        self.fe_board_index_override = fe_board_index
        self.restamp_times = bool(restamp_times)
        self.restamp_hit_number = bool(restamp_hit_number)
        self._rng = random.Random(seed or None)

        self._bf: Optional[sampic_bin.BinFile] = None
        self._clusters = None
        self._file_idx = -1
        self._order: List[str] = []
        self._pass_events = 0
        self._hit_number = 0
        # Lookahead of (hits, starts_new_pass) so native_gaps_ns can see ahead
        # of what has been emitted.
        self._look: deque = deque()
        self._n_scheduled = 0
        self._sched_t0: Optional[float] = None
        self._stats = SourceStats()

    # -- files ---------------------------------------------------------------

    @staticmethod
    def _expand(patterns: Sequence[str]) -> List[str]:
        out: List[str] = []
        for pat in patterns:
            if not pat:
                continue
            hits = sorted(glob.glob(os.path.expanduser(pat)))
            if hits:
                out.extend(h for h in hits if os.path.isfile(h))
            elif os.path.isfile(os.path.expanduser(pat)):
                out.append(os.path.expanduser(pat))
        # dict.fromkeys rather than set(): a caller listing the same file twice
        # to double its weight is reasonable, but two patterns matching it is a
        # surprise, and order must stay deterministic.
        return list(dict.fromkeys(out))

    def _next_file(self) -> bool:
        """Advance to the next file, rewinding at the end if looping."""
        self._close_current()
        self._file_idx += 1
        if self._file_idx >= len(self._order):
            if not self.loop:
                self._stats.exhausted = True
                return False
            self._stats.loops += 1
            self._start_pass()
        path = self._order[self._file_idx]
        self._bf = sampic_bin.BinFile(path)
        self._pass_events = 0

        header = self._bf.header
        self.meta = SourceMeta(
            fe_board_index=(self.fe_board_index_override
                            if self.fe_board_index_override is not None
                            else header.fe_board_index),
            inl_corrected=header.inl_corrected,
            adc_corrected=header.adc_corrected,
            sampling_msps=header.sampling_freq_msps,
            unix_time=header.unix_time)

        chunks = self._bf.iter_chunks(self.chunk_hits)
        self._clusters = event_builder.iter_clusters(chunks, self.gap_ns)
        return True

    def _start_pass(self) -> None:
        self._order = list(self.files)
        if self.shuffle:
            self._rng.shuffle(self._order)
        self._file_idx = 0

    def _close_current(self) -> None:
        self._clusters = None
        if self._bf is not None:
            self._bf.close()
            self._bf = None

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        self._start_pass()
        self._file_idx = -1
        self._next_file()

    def close(self) -> None:
        self._close_current()
        self._look.clear()

    def reset(self, t_ns: float) -> None:
        self.close()
        self._stats = SourceStats()
        self._hit_number = 0
        self._n_scheduled = 0
        self._sched_t0 = None
        self.open()

    # -- cluster supply ------------------------------------------------------

    def _pull_cluster(self):
        """Next (hits, starts_new_pass) from the files, or None when exhausted."""
        new_pass = False
        while True:
            if self._clusters is None:
                if not self._next_file():
                    return None
                new_pass = True
            try:
                hits = next(self._clusters)
            except StopIteration:
                self._clusters = None
                if not self.loop and self._file_idx + 1 >= len(self._order):
                    self._stats.exhausted = True
                    return None
                continue
            self._pass_events += 1
            if self._pass_events <= self.start_event:
                continue
            if (self.max_events_per_pass
                    and self._pass_events > self.start_event + self.max_events_per_pass):
                self._clusters = None
                continue
            # Copy: the cluster is a view over an mmap that _close_current will
            # drop when we move to the next file.
            return widen(hits).copy(), new_pass

    def _fill(self, want: int) -> None:
        while len(self._look) < want:
            item = self._pull_cluster()
            if item is None:
                return
            self._look.append(item)

    # -- the EventSource interface -------------------------------------------

    def native_gaps_ns(self, n: int) -> Optional[np.ndarray]:
        """Gaps from the file's own timestamps, for the next `n` unscheduled events.

        Kept in lockstep with `next_events`: entries are marked scheduled here
        and unmarked as they are consumed, so a tick that emits fewer events
        than were scheduled leaves the rest queued rather than regenerating
        their gaps.
        """
        if n <= 0:
            return np.empty(0, dtype=np.float64)
        self._fill(self._n_scheduled + n)
        avail = len(self._look) - self._n_scheduled
        k = min(n, max(avail, 0))
        out = np.empty(k, dtype=np.float64)
        prev = self._sched_t0
        for i in range(k):
            hits, new_pass = self._look[self._n_scheduled + i]
            t = float(hits["t0"][0])
            out[i] = self.loop_gap_ns if (new_pass or prev is None) else max(t - prev, 0.0)
            prev = t
        self._n_scheduled += k
        if k:
            self._sched_t0 = prev
        return out

    def next_events(self, times_ns: Sequence[float]) -> List[SourceEvent]:
        times = np.asarray(times_ns, dtype=np.float64)
        if times.size == 0:
            return []
        self._fill(times.size)
        out: List[SourceEvent] = []
        for t in times[:len(self._look)]:
            hits, _ = self._look.popleft()
            if self._n_scheduled > 0:
                self._n_scheduled -= 1
            if self.restamp_times:
                hn = self._hit_number if self.restamp_hit_number else None
                stamped = restamp(hits, float(t), hn)
                first = float(t)
            else:
                # Passthrough: keep the file's own timestamps and hit numbers, so
                # the output can be compared byte for byte with bin_to_mid's.
                stamped = hits
                first = float(hits["t0"][0])
            self._hit_number += len(stamped)
            self._stats.events += 1
            self._stats.hits += len(stamped)
            out.append(SourceEvent(stamped, first))
        return out

    def describe(self) -> str:
        cur = (os.path.basename(self._order[self._file_idx])
               if 0 <= self._file_idx < len(self._order) else "-")
        state = "exhausted" if self._stats.exhausted else f"file {cur}"
        return (f"binfile: {state}, {len(self.files)} file(s), "
                f"gap {self.gap_ns:g} ns, loop {self._stats.loops}")
