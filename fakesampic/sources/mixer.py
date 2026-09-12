"""Overlay several sources into one event: pile-up and high-occupancy studies.

Children are ordinary `EventSource`s, so a mixer of mixers works with no special
case, and "real hits with synthetic hits on top" is just the default child list.

Two modes:

overlay
    Each child contributes one sub-event with probability `weights[i]`. With the
    default weights of 1.0 every child contributes to every event -- the "same
    real data, but busier" case.

pileup
    The first child is the primary and always contributes; then Poisson(mu)
    additional sub-events are drawn from the children at random. This is the
    model for accidental coincidence, and unlike overlay it produces a
    multiplicity distribution rather than a fixed occupancy.

`channel_offset` matters more than it looks: run914 has exactly one enabled
channel, so overlaying two copies of it without an offset piles every hit onto
channel 2 and produces something no detector could emit. The offset spreads the
copies across the channel space instead.
"""

from typing import List, Optional, Sequence

import numpy as np

from .base import (HIT_NUMBER_WRAP, EventSource, SourceEvent, SourceMeta,
                   SourceStats)


class MixerSource(EventSource):
    """Merges sub-events drawn from several child sources."""

    name = "mixer"

    def __init__(self, children: Sequence[EventSource], mode: str = "overlay",
                 weights: Optional[Sequence[float]] = None,
                 pileup_mu: float = 0.5,
                 jitter_ns: Optional[Sequence[float]] = None,
                 channel_offset: Optional[Sequence[int]] = None,
                 sort_hits: bool = True,
                 n_daq_channels: int = 128,
                 seed: int = 0):
        if not children:
            raise ValueError("mixer needs at least one child source")
        if mode not in ("overlay", "pileup"):
            raise ValueError(f"mixer mode must be 'overlay' or 'pileup', got {mode!r}")
        super().__init__(SourceMeta())
        self.children = list(children)
        self.mode = mode
        n = len(self.children)
        self.weights = self._per_child(weights, 1.0, n, "Weights", float)
        self.jitter_ns = self._per_child(jitter_ns, 0.0, n, "Jitter ns", float)
        self.channel_offset = self._per_child(channel_offset, 0, n, "Channel Offset", int)
        self.pileup_mu = float(pileup_mu)
        if self.pileup_mu < 0:
            raise ValueError(f"pileup mu must be >= 0, got {pileup_mu}")
        self.sort_hits = bool(sort_hits)
        self.n_daq_channels = int(n_daq_channels)
        self._rng = np.random.default_rng(seed or None)
        self._hit_number = 0
        self._stats = SourceStats()
        # The mixer's own board index; children's AD header values are not used,
        # because one event cannot carry two different fe_board_index values.
        self.meta = self.children[0].meta

    @staticmethod
    def _per_child(values, default, n, name, cast):
        if values is None:
            return [cast(default)] * n
        vals = list(values)
        if len(vals) == 1 and n > 1:
            vals = vals * n
        if len(vals) != n:
            raise ValueError(f"{name} has {len(vals)} entries, expected {n} children")
        return [cast(v) for v in vals]

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        for c in self.children:
            c.open()
        self.meta = self.children[0].meta

    def close(self) -> None:
        for c in self.children:
            c.close()

    def reset(self, t_ns: float) -> None:
        for c in self.children:
            c.reset(t_ns)
        self._hit_number = 0
        self._stats = SourceStats()
        self.meta = self.children[0].meta

    # -- production ----------------------------------------------------------

    def _assignment(self, n_ev: int) -> List[List[int]]:
        """For each child, the output-event indices it should contribute to.

        A child may appear more than once for one output event in pileup mode;
        that is a second accidental from the same source.
        """
        rng = self._rng
        n_child = len(self.children)
        per_child: List[List[int]] = [[] for _ in range(n_child)]
        if self.mode == "overlay":
            for ci, w in enumerate(self.weights):
                if w >= 1.0:
                    per_child[ci].extend(range(n_ev))
                elif w > 0.0:
                    per_child[ci].extend(np.flatnonzero(rng.random(n_ev) < w).tolist())
        else:
            per_child[0].extend(range(n_ev))
            extra = rng.poisson(self.pileup_mu, n_ev)
            for ev in range(n_ev):
                for _ in range(int(extra[ev])):
                    per_child[int(rng.integers(0, n_child))].append(ev)
        return per_child

    def next_events(self, times_ns: Sequence[float]) -> List[SourceEvent]:
        times = np.asarray(times_ns, dtype=np.float64)
        n_ev = times.size
        if n_ev == 0:
            return []

        per_child = self._assignment(n_ev)
        parts: List[List[np.ndarray]] = [[] for _ in range(n_ev)]

        for ci, child in enumerate(self.children):
            idx = per_child[ci]
            if not idx:
                continue
            # One batched call per child, not one per sub-event.
            subs = child.next_events(times[idx])
            for ev_idx, sub in zip(idx, subs):
                hits = sub.hits
                if self.channel_offset[ci] or self.jitter_ns[ci]:
                    hits = hits.copy()
                    if self.channel_offset[ci]:
                        hits["channel"] = ((hits["channel"].astype(np.int64)
                                            + self.channel_offset[ci])
                                           % self.n_daq_channels).astype("u1")
                    if self.jitter_ns[ci]:
                        j = self._rng.uniform(-self.jitter_ns[ci], self.jitter_ns[ci])
                        hits["t0"] = hits["t0"] + j
                        hits["time"] = (hits["time"].astype(np.float64) + j).astype("<f4")
                parts[ev_idx].append(hits)

        out: List[SourceEvent] = []
        for ev_idx in range(n_ev):
            chunks = parts[ev_idx]
            if not chunks:
                continue
            hits = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
            if self.sort_hits and len(hits) > 1:
                # event_builder.cluster_starts requires ascending t0, and the AT00
                # bank reports the first hit's timestamp; an unsorted merge would
                # make both wrong.
                hits = hits[np.argsort(hits["t0"], kind="stable")]
            else:
                hits = hits.copy() if hits.base is not None else hits
            n = len(hits)
            hits["hit_number"] = ((self._hit_number + np.arange(n, dtype=np.int64))
                                  & HIT_NUMBER_WRAP).astype("<i4")
            self._hit_number += n
            out.append(SourceEvent(hits, float(hits["t0"][0])))

        self._stats.events += len(out)
        self._stats.hits += sum(len(e.hits) for e in out)
        self._stats.exhausted = all(c.stats().exhausted for c in self.children)
        return out

    def describe(self) -> str:
        kids = ", ".join(c.name for c in self.children)
        extra = f", mu {self.pileup_mu:g}" if self.mode == "pileup" else ""
        return f"mixer ({self.mode}{extra}): [{kids}]"
