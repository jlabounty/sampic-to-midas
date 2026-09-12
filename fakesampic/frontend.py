#!/usr/bin/env python3
"""The MIDAS frontend: a live stream of fake SAMPIC events.

    fake-sampic-fe            (after `pip install -e .`)
    python -m fakesampic.frontend

Two equipments:

FakeSampic (EQ_PERIODIC, RO_RUNNING, no history)
    The physics stream. Produces AD00/AT00 events into SYSTEM at the rate the
    ODB asks for, from whichever source the ODB selects.

FakeSampicDQM (EQ_PERIODIC, RO_ALWAYS, history on)
    Per-channel rates and amplitudes plus generator statistics, and the
    channel -> plane/strip map, so a custom page can draw the detector without
    knowing anything about this code. RO_ALWAYS because occupancy is most
    interesting when nobody is taking data.

Why EQ_PERIODIC and not EQ_POLLED or EQ_USER
    EQ_USER is stripped by the framework with a logged complaint
    (frontend.py:549), and EQ_POLLED's poll_func runs in the same single thread
    as readout_func, so for a software source that can always produce on demand
    it is a wasted call per iteration and buys nothing.

Why the rate does not come from Common/Period
    Period is the readout TICK, not the event rate. The framework measures it
    from the start of the previous readout so it drifts (frontend.py:604), and
    caps its yield at 10 ms regardless (frontend.py:944). The rate is owned by
    fakesampic.schedule on the simulated clock, and one tick emits however many
    events are due -- readout_func returns a LIST, which the framework sends with
    correctly incrementing serial numbers (frontend.py:658-684). That batching is
    what makes kHz rates reachable from a ~50 Hz tick.
"""

import ctypes
import os
import sys
import time
from typing import List, Optional

import numpy as np

import midas
import midas.client
import midas.event
import midas.frontend

from . import identity, settings as fssettings
from .banks import build_batch
from .chargesharing import SensorFit
from .clock import SimClock
from .geometry import build_geometry
from .midasbank import build_event, byte_bank
from .occupancy import OccupancyAccumulator
from .schedule import BurstRate, FixedRate, Pacer, PoissonRate, ReplayGaps
from .sources.binfile import BinFileSource
from .sources.mixer import MixerSource
from .sources.synthetic import SyntheticSource
from .waveform import PulseParams

DQM_STAT_NAMES = [
    "Events per s", "Hits per s", "MB per s", "Skipped", "Dropped",
    "Backlog", "Loops", "Sim clock s", "Readout duty %", "Mean hits per event",
    "Max hits per event", "Source OK",
]
N_STATS = len(DQM_STAT_NAMES)


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------

def _install_nowait_send(client) -> bool:
    """Replace `client.send_event` with one that drops instead of blocking.

    `midas.client.send_event` ends in `rpc_send_event(..., BM_WAIT)` followed by
    `bm_flush_cache(..., BM_WAIT)` (client.py:870-874). Both block
    UNINTERRUPTIBLY -- a Python signal handler cannot run inside the C call -- so
    when a consumer dies without detaching it pins the buffer's read pointer, the
    buffer never drains, and this frontend wedges in a way that also stops it
    answering the run-stop transition. That failure is documented at length in
    wavedream-midas-dqm/scripts/replay-run.py, which could only warn about it.

    A FAKE data source has no obligation to preserve every event, and hanging a
    DAQ is strictly worse than dropping one, so BM_NO_WAIT is the default and
    drops are counted and published. `Backpressure = wait` restores stock
    behaviour for anyone who would rather block.

    Returns False if the bindings do not look as expected, in which case the
    stock path stays in place -- better to keep the documented risk than to send
    events through a path we could not verify.
    """
    lib = getattr(client, "lib", None)
    if lib is None or not all(hasattr(lib, n) for n in
                              ("c_rpc_send_event", "c_rpc_flush_event", "c_bm_flush_cache")):
        return False

    def send_event(self, buffer_handle, event, _orig=client.send_event):
        if getattr(self, "_fs_backpressure", "drop") != "drop":
            return _orig(buffer_handle, event)
        buf = event.pack()
        try:
            self.lib.c_rpc_send_event(buffer_handle, buf, len(buf), midas.BM_NO_WAIT, 1)
        except midas.MidasError as exc:
            if getattr(exc, "code", None) == midas.status_codes.get("BM_ASYNC_RETURN"):
                self._fs_dropped = getattr(self, "_fs_dropped", 0) + 1
                return
            raise
        self.lib.c_rpc_flush_event()
        # One flush per batch is what Common/Write cache size is for; the stock
        # path flushes per event, which defeats it.
        self._fs_needs_flush = buffer_handle

    client.send_event = send_event.__get__(client, type(client))
    client._fs_backpressure = "drop"
    client._fs_dropped = 0
    client._fs_needs_flush = None
    return True


# ---------------------------------------------------------------------------
# Physics equipment
# ---------------------------------------------------------------------------

class ReplayEquipment(midas.frontend.EquipmentBase):
    """Produces the fake SAMPIC physics stream."""

    def __init__(self, client, occupancy: OccupancyAccumulator, default_bin: str = ""):
        common = midas.frontend.InitialEquipmentCommon()
        common.equip_type = midas.EQ_PERIODIC
        common.event_id = identity.PHYSICS_EVENT_ID
        common.trigger_mask = 0
        common.buffer_name = identity.BUFFER_NAME
        # 20 ms: the framework yields at most Period/5 and never more than 10 ms
        # (frontend.py:944), so this is a ~50 Hz tick that does not busy-spin.
        # Period 0 would spin a whole core on cm_yield(0).
        common.period_ms = 20
        common.read_when = midas.RO_RUNNING
        # MUST be 0. With history on, the framework copies every bank into
        # /Equipment/.../Variables once a second (frontend.py:1009-1015) -- which
        # for AD00 means an 8 kB byte blob into the ODB, repeatedly. The DQM
        # equipment carries the history instead.
        common.log_history = 0

        self._default_bin = default_bin
        super().__init__(client, identity.REPLAY_EQUIP_NAME, common,
                         fssettings.defaults(default_bin))

        self.occupancy = occupancy
        self.clock = SimClock()
        self.source = None
        self.pacer = None
        self._timing_model = None
        self._replay_gaps = None
        self._pending_rebuild = True
        self._rebuild_reason = "startup"
        self._readout_us = 0.0          # EWMA of wall-clock cost per event
        self._readout_busy_s = 0.0
        self._last_duty_reset = time.monotonic()
        self._skipped_at_bor = 0
        self.geometry = None

        self._check_common()
        self.rebuild("startup")
        self.set_status(self.source.describe() if self.source else "no source",
                        "greenLight")

    # -- guards --------------------------------------------------------------

    def _check_common(self) -> None:
        """Refuse two Common settings that break this frontend in confusing ways."""
        limit = float(self.common.get("Event limit", 0) or 0)
        if limit > 0:
            # frontend.py:587 does `self.stats["events"].value` but stats["events"]
            # is a plain int (frontend.py:472), so any non-zero limit raises
            # AttributeError on the next loop iteration. Setting one from mhttpd
            # is an entirely reasonable thing to do, so correct it and say why.
            self.client.msg(
                f"{self.name}: Common/Event limit was {limit:g}; forcing 0. The python "
                "frontend framework crashes with a non-zero limit (frontend.py:587 "
                "calls .value on an int).", True)
            self.client.odb_set(self.odb_common_dir + "/Event limit", ctypes.c_double(0))
        if int(self.common.get("Log history", 0) or 0):
            self.client.msg(
                f"{self.name}: Common/Log history was on; forcing off. It would copy "
                "every AD00 bank into the ODB once a second. Use the "
                f"{identity.DQM_EQUIP_NAME} equipment for history.", True)
            self.client.odb_set(self.odb_common_dir + "/Log history", ctypes.c_int32(0))

    # -- configuration -------------------------------------------------------

    def config(self) -> dict:
        return fssettings.merged(self.settings, self._default_bin)

    def rebuild(self, reason: str) -> None:
        """Construct the source, geometry and timing model from the ODB."""
        cfg = self.config()
        problems = fssettings.validate(cfg)
        if problems:
            for p in problems:
                self.client.msg(f"{self.name}: bad setting: {p}", True)
            if self.source is not None:
                self.set_status("bad settings, keeping previous config", "yellowLight")
                self._pending_rebuild = False
                return
            raise ValueError("; ".join(problems))

        if self.source is not None:
            self.source.close()

        self.geometry = self._build_geometry(cfg)
        self.occupancy.set_mapped(self.geometry.mapped_channels)
        self.source = self._build_source(cfg, cfg["Source"])
        self.source.open()
        self._build_timing(cfg)
        self._pending_rebuild = False
        self.client.msg(f"{self.name}: {reason} -> {self.source.describe()}")

    def _build_geometry(self, cfg):
        syn = cfg["Synthetic"]
        n = int(syn["N Planes"])
        return build_geometry(
            n_planes=n,
            n_strips=int(syn["Strips Per Plane"]),
            pitch_mm=float(syn["Pitch mm"]),
            z_mm=fssettings.as_list(syn["Plane Z mm"], n, 0.0),
            orientation=fssettings.as_list(syn["Plane Orientation"], n, "X"),
            offsets_mm=fssettings.as_list(syn["Plane Offset mm"], n, 0.0),
            efficiency=fssettings.as_list(syn["Plane Efficiency"], n, 1.0),
            first_channel=int(syn["First Channel"]),
            n_daq_channels=identity.N_DAQ_CHANNELS)

    def _build_source(self, cfg, kind: str):
        seed = int(cfg["Seed"])
        restamp = bool(cfg["Restamp Timestamps"])
        board = int(cfg["FE Board Index"])
        if kind == "binfile":
            b = cfg["BinFile"]
            return BinFileSource(
                files=fssettings.non_empty(b["Files"]),
                gap_ns=float(b["Gap ns"]), loop=bool(b["Loop"]),
                loop_gap_ns=float(b["Loop Gap ns"]),
                shuffle=bool(b["Shuffle Files"]),
                start_event=int(b["Start Event"]),
                max_events_per_pass=int(b["Max Events Per Pass"]),
                fe_board_index=None if board < 0 else board,
                restamp_times=restamp,
                restamp_hit_number=bool(cfg["Restamp Hit Number"]) and restamp,
                seed=seed)
        if kind == "synthetic":
            syn = cfg["Synthetic"]
            pulse = PulseParams(
                baseline_v=float(syn["Baseline V"]),
                amplitude_v=float(syn["Amplitude V"]),
                amplitude_spread=float(syn["Amplitude Spread"]),
                noise_v=float(syn["Baseline Noise V"]),
                rise_ns=float(syn["Rise ns"]), fall_ns=float(syn["Fall ns"]),
                sampling_msps=int(syn["Sampling MSps"]),
                peak_sample=float(syn["Peak Sample"]))
            return SyntheticSource(
                geometry=self.geometry, model=str(syn["Model"]), pulse=pulse,
                sensor_fit=SensorFit.from_sequence(fssettings.as_list(syn["Sensor Fit"])),
                beam_x_mm=float(syn["Beam X mm"]), beam_y_mm=float(syn["Beam Y mm"]),
                beam_sigma_mm=float(syn["Beam Sigma mm"]),
                beam_divergence_mrad=float(syn["Beam Divergence mrad"]),
                charge_threshold=float(syn["Charge Threshold"]),
                hit_probability=float(syn["Hit Probability"]),
                time_jitter_ps=float(syn["Time Jitter ps"]),
                randomize_first_cell=bool(syn["Randomize First Cell"]),
                raw_tot=int(syn["Raw ToT"]), tot_ns=float(syn["ToT ns"]),
                fe_board_index=max(board, 0), seed=seed)
        if kind == "mixer":
            m = cfg["Mixer"]
            kids = fssettings.non_empty(m["Child Sources"])
            children = [self._build_source(cfg, k) for k in kids]
            n = len(children)
            return MixerSource(
                children, mode=str(m["Mode"]),
                weights=fssettings.as_list(m["Weights"], n, 1.0),
                pileup_mu=float(m["Pileup Mu"]),
                jitter_ns=fssettings.as_list(m["Jitter ns"], n, 0.0),
                channel_offset=fssettings.as_list(m["Channel Offset"], n, 0),
                sort_hits=bool(m["Sort Hits"]),
                n_daq_channels=identity.N_DAQ_CHANNELS, seed=seed)
        raise ValueError(f"unknown source kind {kind!r}")

    def _build_timing(self, cfg) -> None:
        rate = float(cfg["Rate Hz"])
        model_name = str(cfg["Rate Model"])
        seed = int(cfg["Seed"])
        rng = np.random.default_rng(seed or None)
        use_file = (cfg["Source"] == "binfile"
                    and str(cfg["BinFile"]["Timing"]) == "file")
        if use_file:
            self._replay_gaps = ReplayGaps(time_scale=float(cfg["Time Scale"]))
            model = self._replay_gaps
        else:
            self._replay_gaps = None
            if model_name == "fixed":
                model = FixedRate(rate)
            elif model_name == "burst":
                model = BurstRate(rate, float(cfg["Burst Period s"]),
                                  float(cfg["Burst Duty"]), rng)
            else:
                model = PoissonRate(rate, rng)
        self._timing_model = model
        self.pacer = Pacer(model, policy=str(cfg["Backlog Policy"]),
                           max_backlog=int(cfg["Max Backlog"]))
        self.clock.time_scale = float(cfg["Time Scale"])
        self.pacer.start(self.clock.sim_ns())

    # -- ODB hot reload ------------------------------------------------------

    def detailed_settings_changed_func(self, path, idx, new_value):
        key = fssettings.normalise_path(path)
        cfg = self.config()
        if fssettings.is_hot(key):
            self._apply_hot(key, cfg)
            return
        mode = str(cfg["Apply Cold Settings"])
        if mode == "immediately":
            self.rebuild(f"{key} changed")
        else:
            self._pending_rebuild = True
            self._rebuild_reason = f"{key} changed"
            # Say so in the message log: a cold change that silently waits looks
            # like a setting that did not work.
            self.client.msg(
                f"{self.name}: '{key}' changed; it needs the source rebuilt, so it "
                "takes effect at the next begin-of-run (Settings/Apply Cold "
                "Settings = 'immediately' to apply now).")
            self.set_status(f"{self.source.describe()} [pending: {key}]", "yellowLight")

    def _apply_hot(self, key: str, cfg) -> None:
        try:
            if key in ("Rate Hz", "Rate Model", "Burst Period s", "Burst Duty"):
                model = self._timing_model
                if hasattr(model, "set_rate") and key == "Rate Hz":
                    # Rebasing rather than rebuilding keeps the phase, so a rate
                    # change does not show up as a gap in the data.
                    model.set_rate(float(cfg["Rate Hz"]))
                else:
                    self._build_timing(cfg)
            elif key == "Time Scale":
                self.clock.set_time_scale(float(cfg["Time Scale"]))
                if self._replay_gaps is not None:
                    self._replay_gaps.time_scale = float(cfg["Time Scale"])
            elif key == "Backlog Policy":
                self.pacer.set_policy(str(cfg["Backlog Policy"]))
            elif key == "Max Backlog":
                self.pacer.max_backlog = int(cfg["Max Backlog"])
            elif key == "Backpressure":
                setattr(self.client, "_fs_backpressure", str(cfg["Backpressure"]))
            elif key.startswith("Synthetic/") or key.startswith("Mixer/"):
                # The generator holds these as plain attributes; rebuilding it is
                # cheap and has no file position to lose.
                if cfg["Source"] in ("synthetic", "mixer"):
                    self.rebuild(f"{key} changed")
            elif key == "BinFile/Timing":
                self._build_timing(cfg)
            elif key.startswith("BinFile/") and self.source is not None:
                if hasattr(self.source, "loop"):
                    self.source.loop = bool(cfg["BinFile"]["Loop"])
                    self.source.loop_gap_ns = float(cfg["BinFile"]["Loop Gap ns"])
        except Exception as exc:
            self.client.msg(f"{self.name}: could not apply '{key}': {exc}", True)

    # -- run transitions -----------------------------------------------------

    def on_begin_run(self) -> None:
        cfg = self.config()
        if self._pending_rebuild:
            self.rebuild(self._rebuild_reason)
        if bool(cfg["Reset Clock At BOR"]):
            self.clock.reset(0.0)
        self.source.reset(self.clock.sim_ns())
        self.pacer.start(self.clock.sim_ns())
        setattr(self.client, "_fs_backpressure", str(cfg["Backpressure"]))
        self.occupancy.reset()
        self._readout_busy_s = 0.0
        self._last_duty_reset = time.monotonic()

        if not bool(cfg["Reset Clock At BOR"]):
            warn = self.clock.precision_warning(self.clock.sim_ns() + 3600e9)
            if warn:
                self.client.msg(f"{self.name}: {warn}", True)
        self.set_status(self.source.describe(), "greenLight")

    def on_end_run(self) -> None:
        s = self.pacer.stats if self.pacer else None
        if s and s.dropped:
            self.client.msg(
                f"{self.name}: run ended having dropped {s.dropped} of "
                f"{s.scheduled} scheduled events (backlog policy "
                f"'{self.pacer.policy}')")

    # -- readout -------------------------------------------------------------

    def readout_func(self) -> Optional[List["midas.event.Event"]]:
        t_wall = time.monotonic()
        cfg = self.config()
        max_n = int(cfg["Max Events Per Call"])
        budget_s = float(cfg["Max Readout ms"]) * 1e-3

        # Two caps, not one: per-event cost spans two orders of magnitude between
        # a one-hit replay event and a 256-hit mixer overlay, so a fixed event
        # count alone cannot bound how long this call takes -- and a readout that
        # overruns is what delays a run transition.
        if self._readout_us > 0:
            max_n = max(1, min(max_n, int(budget_s * 1e6 / self._readout_us)))

        t_sim = self.clock.sim_ns(t_wall)

        if self._replay_gaps is not None and self._replay_gaps.hungry:
            gaps = self.source.native_gaps_ns(max_n)
            if gaps is not None and gaps.size:
                self._replay_gaps.feed(gaps)

        times = self.pacer.due(t_sim, max_n)
        if times.size == 0:
            return None

        events = self.source.next_events(times)
        if not events:
            if self.source.stats().exhausted:
                self.set_status(self.source.describe(), "yellowLight")
            return None

        bank_lists = build_batch(events, self.source.meta,
                                 timing_bank=bool(cfg["Timing Bank"]))
        out = []
        for ev, banks in zip(events, bank_lists):
            n_bytes = sum(len(payload) for _, _, payload in banks)
            out.append(build_event(identity.PHYSICS_EVENT_ID, 0,
                                   [byte_bank(name, payload)
                                    for name, _tid, payload in banks]))
            self.occupancy.add_event(ev.hits, n_bytes)

        elapsed = time.monotonic() - t_wall
        self._readout_busy_s += elapsed
        per_event_us = elapsed * 1e6 / max(len(out), 1)
        # EWMA rather than the last value: one slow tick (a page fault on the
        # mmap, the GC) should not collapse the batch size for the next second.
        self._readout_us = (per_event_us if self._readout_us == 0 else
                            0.8 * self._readout_us + 0.2 * per_event_us)
        return out

    def duty_percent(self) -> float:
        now = time.monotonic()
        window = max(now - self._last_duty_reset, 1e-9)
        duty = 100.0 * self._readout_busy_s / window
        self._readout_busy_s = 0.0
        self._last_duty_reset = now
        return duty


# ---------------------------------------------------------------------------
# DQM equipment
# ---------------------------------------------------------------------------

class DqmEquipment(midas.frontend.EquipmentBase):
    """Per-channel rates/amplitudes and generator statistics, for pages and history."""

    def __init__(self, client, replay: ReplayEquipment, occupancy: OccupancyAccumulator):
        common = midas.frontend.InitialEquipmentCommon()
        common.equip_type = midas.EQ_PERIODIC
        common.event_id = identity.DQM_EVENT_ID
        common.trigger_mask = 0
        common.buffer_name = identity.BUFFER_NAME
        common.period_ms = 2000
        # RO_ALWAYS, not RO_RUNNING: occupancy between runs is exactly when you
        # want to see whether the generator is alive.
        common.read_when = midas.RO_ALWAYS
        common.log_history = 1
        super().__init__(client, identity.DQM_EQUIP_NAME, common, None)

        self.replay = replay
        self.occupancy = occupancy
        self._last = time.monotonic()
        self.publish_geometry()

    def publish_geometry(self) -> None:
        """Write the channel map so a browser page can draw the detector.

        Rewritten every start rather than seeded once: it is DERIVED from the
        geometry settings, so the settings are the source of truth for it and a
        stale copy would be worse than none.
        """
        g = self.replay.geometry
        if g is None:
            return
        n = identity.N_DAQ_CHANNELS
        plane_idx = g.channel_plane.astype(np.int32)
        names = g.plane_names()
        d = self.odb_settings_dir
        self.client.odb_set(d + "/N Channels", ctypes.c_int32(n))
        self.client.odb_set(d + "/Channel Plane Index", [int(v) for v in plane_idx])
        self.client.odb_set(d + "/Channel Strip", [int(v) for v in g.channel_strip])
        self.client.odb_set(d + "/Channel Position mm",
                            [float(v) for v in g.channel_position_mm])
        self.client.odb_set(d + "/Channel Plane",
                            [names[i] if i >= 0 else "" for i in plane_idx])
        self.client.odb_set(d + "/Channel Orientation",
                            [g.planes[i].orientation if i >= 0 else "" for i in plane_idx])
        self.client.odb_set(d + "/Plane Names", names)
        self.client.odb_set(d + "/Plane Z mm", [float(v) for v in g.plane_z_mm()])
        self.client.odb_set(d + "/Plane Pitch mm", [float(p.pitch_mm) for p in g.planes])
        self.client.odb_set(d + "/Plane N Strips", [int(p.n_strips) for p in g.planes])
        self.client.odb_set(d + "/Names FSRT", g.channel_names())
        self.client.odb_set(d + "/Names FSAM", g.channel_names())
        self.client.odb_set(d + "/Names FSST", DQM_STAT_NAMES)

    def readout_func(self):
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        snap = self.occupancy.drain(elapsed)

        pstats = self.replay.pacer.stats if self.replay.pacer else None
        sstats = self.replay.source.stats() if self.replay.source else None
        dropped = float(getattr(self.client, "_fs_dropped", 0))

        stats = np.zeros(N_STATS, dtype="<f4")
        stats[0] = snap.events / snap.elapsed_s
        stats[1] = snap.hits / snap.elapsed_s
        stats[2] = snap.bytes_ / snap.elapsed_s / 1e6
        stats[3] = float(pstats.dropped) if pstats else 0.0
        stats[4] = dropped
        stats[5] = float(pstats.backlog) if pstats else 0.0
        stats[6] = float(sstats.loops) if sstats else 0.0
        stats[7] = self.replay.clock.sim_ns() * 1e-9
        stats[8] = self.replay.duty_percent()
        stats[9] = snap.hits / max(snap.events, 1)
        stats[10] = float(snap.max_hits_per_event)
        stats[11] = 0.0 if (sstats and sstats.exhausted) else 1.0

        event = midas.event.Event()
        event.header.event_id = identity.DQM_EVENT_ID
        event.header.trigger_mask = 0
        # TID_FLOAT accepts numpy directly -- only TID_BYTE/TID_CHAR does not
        # (event.py:452) -- so these take the fast path without byte_bank.
        event.create_bank("FSRT", midas.TID_FLOAT, snap.rates_hz)
        event.create_bank("FSAM", midas.TID_FLOAT, snap.mean_amplitude_v)
        event.create_bank("FSST", midas.TID_FLOAT, stats)

        desc = self.replay.source.describe() if self.replay.source else "no source"
        colour = "greenLight"
        if sstats and sstats.exhausted:
            colour = "yellowLight"
        elif pstats and pstats.dropped:
            desc += f"  [{pstats.dropped} dropped]"
            colour = "yellowLight"
        self.replay.set_status(desc, colour)
        return event


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

class FakeSampicFrontend(midas.frontend.FrontendBase):
    def __init__(self, default_bin: str = ""):
        super().__init__(identity.client_name())
        self._warn_stale_buffer_clients()
        if not _install_nowait_send(self.client):
            self.client.msg(
                "fake-sampic: could not install the non-blocking send; falling back "
                "to the stock BM_WAIT path. A stale buffer client can now hang this "
                "frontend -- see fakesampic/frontend.py:_install_nowait_send.", True)

        self.occupancy = OccupancyAccumulator(identity.N_DAQ_CHANNELS)
        self.replay = ReplayEquipment(self.client, self.occupancy, default_bin)
        self.add_equipment(self.replay)
        self.dqm = DqmEquipment(self.client, self.replay, self.occupancy)
        self.add_equipment(self.dqm)
        self._warn_new_equipment()

    def _warn_stale_buffer_clients(self) -> None:
        """Name dead clients still attached to the buffer, before we depend on it.

        A stale reader pins the buffer's read pointer so it never drains. Worth
        saying at the one moment it is actionable; `odbedit -c cleanup` does not
        clear these, but stopping every MIDAS client and restarting does.
        """
        try:
            attached = set(self.client.odb_get(
                f"/System/Buffers/{identity.BUFFER_NAME}/Clients", recurse_dir=True) or {})
            alive = set()
            for _, info in (self.client.odb_get("/System/Clients", recurse_dir=True)
                            or {}).items():
                if isinstance(info, dict) and "Name" in info:
                    alive.add(str(info["Name"]))
        except Exception:
            return
        stale = {a for a in attached if a not in alive and not a.isdigit()}
        if stale:
            self.client.msg(
                "fake-sampic: stale clients still attached to "
                f"{identity.BUFFER_NAME}: {', '.join(sorted(stale))}. They pin the "
                "buffer read pointer so it never drains. 'odbedit -c cleanup' does "
                "not remove them; stopping all MIDAS clients and restarting does.",
                True)

    def _warn_new_equipment(self) -> None:
        """mlogger reads the equipment list when IT starts, not when we do."""
        try:
            hist = self.client.odb_get("/History/Events", recurse_dir=True) or {}
        except Exception:
            return
        if identity.DQM_EQUIP_NAME not in str(hist):
            self.client.msg(
                f"fake-sampic: {identity.DQM_EQUIP_NAME} looks new to the history "
                "system. mlogger reads the equipment list when it starts and does "
                "not notice one that appears later, so restart mlogger or there "
                "will be no history for it.")

    def begin_of_run(self, run_number):
        self.replay.on_begin_run()
        return midas.status_codes["SUCCESS"]

    def end_of_run(self, run_number):
        self.replay.on_end_run()
        return midas.status_codes["SUCCESS"]

    def frontend_exit(self):
        if self.replay.source is not None:
            self.replay.source.close()


def main() -> int:
    """Entry point.

    Deliberately no argparse of our own: FrontendBase.__init__ parses sys.argv
    for the standard MIDAS flags (-e, -h, -i, -D ...) and rejects anything it
    does not recognise, so an extra flag here would break `-e expt` for everyone.
    Configuration is the ODB's job anyway; the one value needed BEFORE the ODB
    exists is the .bin to seed Settings/BinFile/Files with, and that comes from
    the environment (scripts/fake-sampic-env.sh exports it).
    """
    fe = FakeSampicFrontend(os.environ.get("FS_DEFAULT_BIN", ""))
    fe.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
