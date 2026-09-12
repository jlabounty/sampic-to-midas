"""Rate models and the backlog policy."""

import numpy as np
import pytest

from fakesampic.clock import RESET_WARN_NS, SimClock
from fakesampic.schedule import (BurstRate, FixedRate, Pacer, PoissonRate,
                                 ReplayGaps)


def drain(model, seconds=20.0, tick_ms=20.0, limit=10 ** 9):
    out = []
    for i in range(1, int(seconds * 1000 / tick_ms) + 1):
        out.append(model.generate(i * tick_ms * 1e6, limit))
    return np.concatenate(out) if out else np.empty(0)


def test_fixed_rate_never_drifts():
    m = FixedRate(1000.0)
    m.start(0.0)
    t = drain(m, seconds=20.0)
    assert t.size == 20001                       # inclusive of t=0
    # Exact multiples of the interval, not an accumulated sum.
    assert np.array_equal(t, np.arange(t.size) * 1e6)


def test_fixed_rate_hits_the_requested_rate():
    m = FixedRate(137.0)
    m.start(0.0)
    t = drain(m, seconds=30.0)
    assert (t.size - 1) / 30.0 == pytest.approx(137.0, rel=1e-9)


def test_poisson_gaps_are_exponential():
    m = PoissonRate(5000.0, np.random.default_rng(7))
    m.start(0.0)
    t = drain(m, seconds=40.0)
    gaps = np.diff(t)
    mean = 1e9 / 5000.0
    assert gaps.mean() == pytest.approx(mean, rel=0.02)
    # An exponential distribution has std == mean.
    assert gaps.std() / gaps.mean() == pytest.approx(1.0, rel=0.05)
    assert (gaps > 0).all()


def test_poisson_rate_is_right_on_average():
    m = PoissonRate(800.0, np.random.default_rng(3))
    m.start(0.0)
    t = drain(m, seconds=60.0)
    assert t.size / 60.0 == pytest.approx(800.0, rel=0.03)


def test_burst_respects_its_gate_and_mean_rate():
    m = BurstRate(1000.0, period_s=1.0, duty=0.1, rng=np.random.default_rng(3))
    m.start(0.0)
    t = drain(m, seconds=30.0)
    phase = np.mod(t, 1e9) / 1e9
    assert (phase < 0.1 + 1e-9).all(), "arrivals outside the spill gate"
    # rate_hz is the mean over the whole cycle, not the in-spill rate.
    assert t.size / 30.0 == pytest.approx(1000.0, rel=0.05)


def test_burst_in_spill_rate_is_higher_than_the_mean():
    m = BurstRate(1000.0, period_s=1.0, duty=0.1, rng=np.random.default_rng(5))
    m.start(0.0)
    t = drain(m, seconds=20.0)
    in_spill = np.diff(t[np.mod(t, 1e9) / 1e9 < 0.09])
    assert 1e9 / np.median(in_spill) > 3000.0


def test_timing_models_reject_impossible_rates():
    for cls in (FixedRate, PoissonRate):
        with pytest.raises(ValueError, match="rate"):
            cls(0.0)
    with pytest.raises(ValueError, match="duty"):
        BurstRate(100.0, 1.0, 0.0)
    with pytest.raises(ValueError, match="period"):
        BurstRate(100.0, 0.0, 0.5)


def _run_pacer(policy, ticks=100, per_tick=5, rate=1000.0):
    p = Pacer(FixedRate(rate), policy=policy, max_backlog=10000)
    p.start(0.0)
    got = 0
    for i in range(1, ticks + 1):
        got += p.due(i * 20e6, per_tick).size
    return p, got


def test_drop_policy_accounts_for_every_scheduled_event():
    p, got = _run_pacer("drop")
    s = p.stats
    assert got == s.emitted
    assert s.emitted + s.dropped + s.backlog == s.scheduled
    assert s.backlog == 0, "drop must not queue"
    assert s.dropped > 0, "5 events/tick cannot keep up with 1 kHz at 50 Hz"


def test_catchup_policy_queues_instead_of_dropping():
    p, got = _run_pacer("catchup")
    s = p.stats
    assert got == s.emitted
    assert s.emitted + s.dropped + s.backlog == s.scheduled
    assert s.backlog > 0
    assert s.dropped == 0


def test_catchup_backlog_is_bounded():
    """An unbounded queue is a memory leak with a friendly name."""
    p = Pacer(FixedRate(10000.0), policy="catchup", max_backlog=50)
    p.start(0.0)
    for i in range(1, 40):
        p.due(i * 20e6, 2)
    # The bound is enforced twice over: the model is never asked for more than
    # max_n + max_backlog in one call, and anything past the bound is trimmed.
    assert p.stats.backlog <= 50
    assert p.stats.emitted + p.stats.dropped + p.stats.backlog == p.stats.scheduled


def test_switching_to_drop_discards_the_queue():
    p = Pacer(FixedRate(1000.0), policy="catchup", max_backlog=10000)
    p.start(0.0)
    for i in range(1, 20):
        p.due(i * 20e6, 2)
    assert p.stats.backlog > 0
    p.set_policy("drop")
    assert p.stats.backlog == 0


def test_pacer_honours_the_event_cap_exactly():
    p = Pacer(FixedRate(100000.0), policy="drop")
    p.start(0.0)
    for i in range(1, 10):
        assert p.due(i * 20e6, 7).size <= 7


def test_pacer_rejects_an_unknown_policy():
    with pytest.raises(ValueError, match="drop"):
        Pacer(FixedRate(10.0)).set_policy("sprint")


def test_replay_gaps_reproduce_the_fed_structure():
    m = ReplayGaps()
    m.start(0.0)
    assert m.hungry
    m.feed(np.array([100.0, 250.0, 1000.0]))
    assert not m.hungry
    t = m.generate(1e9, 10)
    assert t == pytest.approx([100.0, 350.0, 1350.0])
    assert m.hungry, "should ask for more once drained"


def test_replay_gaps_time_scale_compresses_time():
    m = ReplayGaps(time_scale=2.0)
    m.start(0.0)
    m.feed(np.array([100.0, 100.0]))
    assert m.generate(1e9, 10) == pytest.approx([50.0, 100.0])


def test_sim_clock_is_monotonic_and_scalable():
    t = [0.0]
    clock = SimClock(monotonic=lambda: t[0])
    clock.reset(0.0)
    t[0] = 1.0
    assert clock.sim_ns() == pytest.approx(1e9)
    clock.set_time_scale(2.0)
    t[0] = 2.0
    # Rebased, not restarted: no discontinuity at the change.
    assert clock.sim_ns() == pytest.approx(1e9 + 2e9)


def test_clock_warns_where_float32_time_degrades():
    clock = SimClock()
    assert clock.precision_warning(RESET_WARN_NS / 2) == ""
    assert "Reset Clock At BOR" in clock.precision_warning(6e10)
