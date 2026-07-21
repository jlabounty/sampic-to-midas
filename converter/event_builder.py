"""Time-gap clustering of SAMPIC hits into events.

SAMPIC is a self-triggered continuous stream; a "MIDAS event" is defined here
as a cluster of consecutive hits whose FirstSampleTimeStamp gaps never exceed
`gap_ns`. A new event starts whenever the gap to the previous hit is larger.
"""

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
