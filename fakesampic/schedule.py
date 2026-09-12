"""When events happen, and what to do when we cannot keep up.

The rate is owned here, never derived from `Common/Period`. The framework's tick
drifts (`_is_time_to_work` measures from the start of the previous readout,
frontend.py:604) and its yield is hard-capped at 10 ms (frontend.py:944), so
`Period` only says how often we get a chance to act -- the arrival times come
from a `TimingModel` on the simulated clock.

Models generate arrivals in vectorised blocks rather than one at a time; at a
few kHz with a 20 ms tick that is ~100 events per call, and per-event Python in
this path would dominate everything the generator does.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

MAX_GENERATE = 100000
"""Hard cap on arrivals produced in one call.

Guards against a simulated clock that jumps -- a large `Time Scale`, a machine
resuming from suspend, or the very first tick after begin-of-run -- turning into
a multi-gigabyte array. Anything beyond the cap is handled by the backlog
policy, which is exactly the situation that policy exists for.
"""


class TimingModel:
    """Stream of event arrival times on the simulated clock, in ns."""

    def generate(self, upto_ns: float, limit: int) -> np.ndarray:
        """Consume and return up to `limit` arrivals at or before `upto_ns`."""
        raise NotImplementedError

    def skip_to(self, t_ns: float) -> int:
        """Discard every arrival at or before `t_ns`; return how many."""
        raise NotImplementedError

    def start(self, t_ns: float) -> None:
        """Begin the stream at `t_ns` (called at begin-of-run)."""
        raise NotImplementedError


class FixedRate(TimingModel):
    """Evenly spaced arrivals.

    Counter-based rather than accumulating a period, so the emitted times are
    exact multiples of the interval and cannot drift over a long run however
    many times `generate` is called.
    """

    def __init__(self, rate_hz: float):
        self.set_rate(rate_hz)
        self._t0 = 0.0
        self._n = 0

    def set_rate(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            raise ValueError(f"rate must be > 0 Hz, got {rate_hz}")
        self.rate_hz = float(rate_hz)
        self._interval = 1e9 / self.rate_hz

    def start(self, t_ns: float) -> None:
        self._t0, self._n = float(t_ns), 0

    def _due_count(self, upto_ns: float) -> int:
        if upto_ns < self._t0:
            return 0
        return int((upto_ns - self._t0) / self._interval) + 1 - self._n

    def generate(self, upto_ns: float, limit: int) -> np.ndarray:
        n = min(self._due_count(upto_ns), limit, MAX_GENERATE)
        if n <= 0:
            return np.empty(0, dtype=np.float64)
        idx = np.arange(self._n, self._n + n, dtype=np.float64)
        self._n += n
        return self._t0 + idx * self._interval

    def skip_to(self, t_ns: float) -> int:
        n = max(self._due_count(t_ns), 0)
        self._n += n
        return n


class PoissonRate(TimingModel):
    """Exponentially distributed gaps -- a genuinely random trigger.

    Gaps are drawn in blocks and consumed from a buffer: one RNG call per block
    rather than per event.
    """

    BLOCK = 1024

    def __init__(self, rate_hz: float, rng: Optional[np.random.Generator] = None):
        self.set_rate(rate_hz)
        self._rng = rng if rng is not None else np.random.default_rng()
        self._last = 0.0
        self._buf = np.empty(0, dtype=np.float64)

    def set_rate(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            raise ValueError(f"rate must be > 0 Hz, got {rate_hz}")
        self.rate_hz = float(rate_hz)
        self._mean_gap = 1e9 / self.rate_hz

    def start(self, t_ns: float) -> None:
        self._last = float(t_ns)
        self._buf = np.empty(0, dtype=np.float64)

    def _extend(self) -> None:
        gaps = self._rng.exponential(self._mean_gap, self.BLOCK)
        arrivals = self._last + np.cumsum(gaps)
        self._last = float(arrivals[-1])
        self._buf = np.concatenate((self._buf, arrivals)) if self._buf.size else arrivals

    def generate(self, upto_ns: float, limit: int) -> np.ndarray:
        out = []
        taken = 0
        while taken < limit:
            if self._buf.size == 0 or self._buf[-1] <= upto_ns:
                if self._buf.size and self._buf[-1] <= upto_ns:
                    room = min(limit - taken, self._buf.size)
                    out.append(self._buf[:room])
                    taken += room
                    self._buf = self._buf[room:]
                    if self._buf.size:
                        continue
                if taken >= limit or taken >= MAX_GENERATE:
                    break
                self._extend()
                continue
            k = int(np.searchsorted(self._buf, upto_ns, side="right"))
            room = min(k, limit - taken)
            if room > 0:
                out.append(self._buf[:room])
                taken += room
                self._buf = self._buf[room:]
            break
        if not out:
            return np.empty(0, dtype=np.float64)
        return np.concatenate(out) if len(out) > 1 else out[0]

    def skip_to(self, t_ns: float) -> int:
        """Discard arrivals up to `t_ns` without drawing them all.

        The buffered ones are counted exactly; the rest of the interval is
        replaced by a single Poisson draw of its expected count. Drawing every
        gap would be unbounded work for a result nobody looks at except as a
        dropped-event counter.
        """
        n = 0
        if self._buf.size:
            k = int(np.searchsorted(self._buf, t_ns, side="right"))
            n += k
            self._buf = self._buf[k:]
            if self._buf.size:
                return n
        if t_ns > self._last:
            expected = (t_ns - self._last) / self._mean_gap
            n += int(self._rng.poisson(min(expected, 1e9)))
            self._last = float(t_ns)
        return n


class BurstRate(TimingModel):
    """Poisson arrivals gated into periodic spills.

    `rate_hz` is the MEAN rate over the whole cycle, so raising the duty cycle
    spreads the same number of events over more time rather than producing more
    of them -- which is what makes it comparable with the other models. Within a
    spill the instantaneous rate is `rate_hz / duty`.

    Implemented by thinning: draw at the in-spill rate and keep the arrivals that
    land inside a gate. Vectorised, and the mean rate comes out exact.
    """

    def __init__(self, rate_hz: float, period_s: float, duty: float,
                 rng: Optional[np.random.Generator] = None):
        if not 0 < duty <= 1:
            raise ValueError(f"burst duty must be in (0,1], got {duty}")
        if period_s <= 0:
            raise ValueError(f"burst period must be > 0 s, got {period_s}")
        self.period_ns = float(period_s) * 1e9
        self.duty = float(duty)
        self._inner = PoissonRate(rate_hz / self.duty, rng)
        self.rate_hz = float(rate_hz)

    def set_rate(self, rate_hz: float) -> None:
        self.rate_hz = float(rate_hz)
        self._inner.set_rate(rate_hz / self.duty)

    def start(self, t_ns: float) -> None:
        self._t0 = float(t_ns)
        self._inner.start(t_ns)

    def _gate(self, times: np.ndarray) -> np.ndarray:
        phase = np.mod(times - self._t0, self.period_ns)
        return times[phase < self.duty * self.period_ns]

    def generate(self, upto_ns: float, limit: int) -> np.ndarray:
        # Ask for more than `limit` because thinning throws most away, but stay
        # bounded: at duty 0.01 an exact fill would mean 100x the work.
        raw = self._inner.generate(upto_ns, min(int(limit / self.duty) + 1, MAX_GENERATE))
        kept = self._gate(raw)
        return kept[:limit]

    def skip_to(self, t_ns: float) -> int:
        return int(round(self._inner.skip_to(t_ns) * self.duty))


class ReplayGaps(TimingModel):
    """Arrival times taken from a file's own inter-event gaps.

    This is the interesting replay mode: run914's real structure (gaps from
    1.2 us to 15 ms around a ~700 Hz mean) rather than an idealised Poisson at
    the same average. `feed()` is called by the source with the next batch of
    gaps; `Loop Gap ns` is what it supplies across a file seam, where the file's
    own gap is undefined.
    """

    def __init__(self, time_scale: float = 1.0):
        self.time_scale = float(time_scale)
        self._last = 0.0
        self._pending = np.empty(0, dtype=np.float64)

    def start(self, t_ns: float) -> None:
        self._last = float(t_ns)
        self._pending = np.empty(0, dtype=np.float64)

    def feed(self, gaps_ns: np.ndarray) -> None:
        gaps = np.asarray(gaps_ns, dtype=np.float64)
        if gaps.size == 0:
            return
        arrivals = self._last + np.cumsum(gaps) / self.time_scale
        self._last = float(arrivals[-1])
        self._pending = (np.concatenate((self._pending, arrivals))
                         if self._pending.size else arrivals)

    @property
    def hungry(self) -> bool:
        """True when `feed()` should be called before the next `generate()`."""
        return self._pending.size == 0

    def generate(self, upto_ns: float, limit: int) -> np.ndarray:
        if self._pending.size == 0:
            return np.empty(0, dtype=np.float64)
        k = int(np.searchsorted(self._pending, upto_ns, side="right"))
        k = min(k, limit, MAX_GENERATE)
        out = self._pending[:k]
        self._pending = self._pending[k:]
        return out

    def skip_to(self, t_ns: float) -> int:
        k = int(np.searchsorted(self._pending, t_ns, side="right"))
        self._pending = self._pending[k:]
        if self._pending.size == 0:
            self._last = max(self._last, float(t_ns))
        return k


@dataclass
class PacerStats:
    scheduled: int = 0
    emitted: int = 0
    dropped: int = 0
    backlog: int = 0


@dataclass
class Pacer:
    """Turns a TimingModel into "what should I send on this tick".

    `Max Events Per Call` bounds how long one readout can take, which is what
    keeps the frontend answering run transitions. What happens to the overflow
    is the backlog policy:

    drop (default)
        Give up the events we could not produce in time and say so. A fake data
        source that sprints to catch up is no longer producing the rate it was
        asked for, and the dropped count is the honest signal that the machine
        cannot do what the ODB asked.

    catchup
        Keep them queued, bounded by `max_backlog`. Useful when total event
        count matters more than instantaneous rate. Unbounded queueing would be
        a memory leak with a friendly name, hence the bound.
    """

    model: TimingModel
    policy: str = "drop"
    max_backlog: int = 10000
    stats: PacerStats = field(default_factory=PacerStats)
    _queue: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))

    def start(self, t_ns: float) -> None:
        self.model.start(t_ns)
        self._queue = np.empty(0, dtype=np.float64)
        self.stats = PacerStats()

    def set_policy(self, policy: str) -> None:
        if policy not in ("drop", "catchup"):
            raise ValueError(f"backlog policy must be 'drop' or 'catchup', got {policy!r}")
        if policy == "drop" and self._queue.size:
            self.stats.dropped += int(self._queue.size)
            self._queue = np.empty(0, dtype=np.float64)
            # Keep the published backlog in step. It is read by the DQM bank
            # between due() calls, so leaving it stale would show a queue that
            # has already been discarded.
            self.stats.backlog = 0
        self.policy = policy

    def due(self, t_sim_ns: float, max_n: int) -> np.ndarray:
        """Arrival times to emit on this tick, at most `max_n`, ascending."""
        if max_n <= 0:
            return np.empty(0, dtype=np.float64)

        room = max_n - self._queue.size
        if room > 0:
            # In catchup we deliberately pull more than this tick can emit, so the
            # deficit lands in the queue instead of being lost in the model.
            limit = room + self.max_backlog if self.policy == "catchup" else room
            fresh = self.model.generate(t_sim_ns, limit)
            self.stats.scheduled += int(fresh.size)
            if fresh.size:
                self._queue = (np.concatenate((self._queue, fresh))
                               if self._queue.size else fresh)

        if self.policy == "drop":
            # Anything the model still holds before now is time we lost.
            skipped = self.model.skip_to(t_sim_ns)
            if skipped:
                self.stats.scheduled += skipped
                self.stats.dropped += skipped

        out = self._queue[:max_n]
        rest = self._queue[max_n:]
        if self.policy == "drop":
            if rest.size:
                self.stats.dropped += int(rest.size)
            self._queue = np.empty(0, dtype=np.float64)
        else:
            if rest.size > self.max_backlog:
                self.stats.dropped += int(rest.size - self.max_backlog)
                rest = rest[:self.max_backlog]
            self._queue = rest

        self.stats.emitted += int(out.size)
        self.stats.backlog = int(self._queue.size)
        return out
