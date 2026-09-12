"""The simulated detector clock.

Three clocks matter in this frontend and conflating them causes subtle bugs:

1. Wall clock (`time.monotonic`) -- when events actually leave the process.
2. Simulated detector clock (this module, ns) -- what goes into the hit
   timestamps, `AT00.fe_timestamp_ns`, and the event-building gaps.
3. The framework tick (`Common/Period`) -- how often we get control.

Scheduling works entirely in clock 2 and is merely *paced* against clock 1. So a
readout tick that runs 7 ms late emits events whose timestamps are exactly
right, that merely arrive 7 ms late. Deriving event content from wall-clock
jitter instead would make replayed data irreproducible and every timing
distribution in a DQM page a measurement of this machine's scheduler.
"""

import time

# float64 holds integer nanoseconds exactly up to 2^53 ns ~= 104 days, but the
# SAMPIC `time` field is float32 and that runs out far sooner -- see RESET_WARN_NS.
RESET_WARN_NS = 1.6e7
"""Simulated t0 beyond which the float32 `time` field loses 1 ns resolution.

The .bin format stores `Time` as float32 (converter/sampic_bin.py:126) and the
converter copies it through to `time_instant`. float32 has 24 bits of mantissa,
so spacing reaches 1 ns at 2^24 = 1.68e7 ns (~17 ms) and 4096 ns by t = 6e10 ns
(~60 s, where real run914 data sits). Past this point the per-hit arrival time
carries quantisation noise rather than a measurement, which is invisible unless
somebody says so.
"""


class SimClock:
    """Maps wall-clock monotonic time onto a simulated detector clock in ns."""

    def __init__(self, time_scale: float = 1.0, epoch_ns: float = 0.0,
                 monotonic=time.monotonic):
        self._monotonic = monotonic
        self.time_scale = float(time_scale)
        self.reset(epoch_ns)

    def reset(self, epoch_ns: float = 0.0) -> None:
        """Restart the simulated clock at `epoch_ns`.

        Called at begin-of-run when `Reset Clock At BOR` is set, which is the
        default: a real SAMPIC's t0 is time-since-acquisition-start, and keeping
        the number small is also what keeps the float32 `time` field meaningful.
        """
        self._origin = self._monotonic()
        self._epoch_ns = float(epoch_ns)

    def set_time_scale(self, scale: float) -> None:
        """Change the scale without discontinuity: rebase on the current time."""
        if scale <= 0:
            raise ValueError(f"time scale must be > 0, got {scale}")
        now = self._monotonic()
        self._epoch_ns = self._sim_ns_at(now)
        self._origin = now
        self.time_scale = float(scale)

    def _sim_ns_at(self, now: float) -> float:
        return self._epoch_ns + (now - self._origin) * 1e9 * self.time_scale

    def sim_ns(self, now: float = None) -> float:
        """Simulated detector time, ns since the epoch."""
        return self._sim_ns_at(self._monotonic() if now is None else now)

    def elapsed_s(self) -> float:
        return (self.sim_ns() - 0.0) * 1e-9

    def precision_warning(self, projected_ns: float) -> str:
        """Non-empty when `projected_ns` would degrade the float32 `time` field."""
        if projected_ns <= RESET_WARN_NS:
            return ""
        spacing = 2.0 ** (int(projected_ns).bit_length() - 24)
        return (f"simulated t0 will reach {projected_ns:.3g} ns, where the float32 "
                f"'time' field has ~{spacing:.0f} ns resolution; enable "
                f"'Reset Clock At BOR' or shorten the run if hit times matter")
