"""Time-gap clustering of SAMPIC hits into events.

SAMPIC is a self-triggered continuous stream; a "MIDAS event" is defined here
as a cluster of consecutive hits whose `FirstSampleTimeStamp` gaps never exceed
`gap_ns`. A new event starts whenever the gap to the previous hit is larger.

`cluster_starts` is the primitive: it works on one in-memory `t0` array.
`iter_clusters` is the streaming form, and is what both the offline converter
(`bin_to_mid.py`) and the live replay frontend use, so that "what counts as an
event" has exactly one implementation. Splitting it in two would be a silent
correctness hazard rather than duplicated code: the live frontend's output is
gated against the offline converter's byte for byte, and that gate only means
something while both cluster identically.
"""

from typing import Iterable, Iterator

import numpy as np


def is_sorted(t0: np.ndarray) -> bool:
    return bool(np.all(np.diff(t0) >= 0))


def cluster_starts(t0: np.ndarray, gap_ns: float) -> np.ndarray:
    """Indices where a new event begins. Requires t0 sorted ascending.

    Returns an int64 array always beginning with 0 (empty input -> empty).
    Event i spans hits [starts[i], starts[i+1]) with an implicit final
    boundary at len(t0).
    """
    if len(t0) == 0:
        return np.empty(0, dtype=np.int64)
    breaks = np.flatnonzero(np.diff(t0) > gap_ns) + 1
    return np.concatenate((np.zeros(1, dtype=np.int64), breaks))


def iter_clusters(chunks: Iterable[np.ndarray],
                  gap_ns: float) -> Iterator[np.ndarray]:
    """Yield one hit array per time-gap cluster, across chunk boundaries.

    `chunks` is an iterable of structured hit arrays (the `converter.sampic_bin`
    record dtype), each sorted ascending in `t0` and continuing the previous
    one. The trailing cluster of every chunk is carried into the next, so no
    event is split at a chunk boundary and memory stays bounded by the chunk
    size plus one event.

    The final cluster is yielded when the input is exhausted -- callers that
    stop early (a `--max-events` cap, or a frontend that only wants N events)
    simply stop consuming, and the unfinished tail is discarded with the
    generator. That is deliberate: a partial cluster at an arbitrary stopping
    point is not a real event, and emitting it would make `--max-events N`
    produce a different Nth event than a full conversion does.

    Yielded arrays are views into the chunk where possible; a cluster spanning a
    boundary is a fresh copy. Callers must not hold a view past the lifetime of
    the underlying mmap (see `sampic_bin.BinFile.close`).
    """
    carry = None
    for chunk in chunks:
        if len(chunk) == 0:
            continue
        if carry is not None and len(carry):
            hits = np.concatenate((carry, chunk))
        else:
            hits = chunk
        starts = cluster_starts(hits["t0"], gap_ns)
        for i in range(len(starts) - 1):
            yield hits[starts[i]:starts[i + 1]]
        # The last cluster may continue into the next chunk. Copy it: `hits` is
        # often a zero-copy view over the mmap, and holding a view of the whole
        # chunk alive to keep one trailing event would defeat the bounded-memory
        # guarantee.
        carry = hits[starts[-1]:].copy()
    if carry is not None and len(carry):
        yield carry
