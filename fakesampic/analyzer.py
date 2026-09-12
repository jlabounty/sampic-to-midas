#!/usr/bin/env python3
"""The histogram backend: a MIDAS client that watches events and serves plots.

    fake-sampic-analyzer            (after `pip install -e .`)
    python -m fakesampic.analyzer

NO EQUIPMENT AND NO TRANSITION CALLBACKS, deliberately.
    Registering either would put this client in the run-transition path, where a
    wedged analyzer delays a run start until the watchdog reaps it. Monitoring
    must never be able to stop data taking. Run state is POLLED from /Runinfo
    instead, which cannot block anybody.

GET_NONBLOCKING, deliberately.
    A sampling consumer. With GET_ALL this client would apply back-pressure to
    the SYSTEM buffer and therefore to the frontend -- again, monitoring
    interfering with data taking. Dropping events is the correct behaviour for a
    histogram: it changes how fast a distribution fills, not what it looks like.

Everything it accumulates lives in `fakesampic.analysis`, which imports no MIDAS
and can be run over a .mid file offline -- `tools/analyze_mid.py` does exactly
that, and is how the analysis is developed without an experiment running.

Pages talk to it over brpc:

    sampic::list                     names of every histogram
    sampic::hist <name>              one histogram, 1-D or 2-D
    sampic::persist <ch,ch,...|all>  the last N waveforms per channel
    sampic::template                 the canonical pulse shape, if loaded
    sampic::status                   JSON: events, rates, template, throttling
    sampic::clear                    zero everything
"""

import ctypes
import os
import sys
import time
from typing import List, Optional

import numpy as np

import midas
import midas.client

from converter.sampic_banks import AD_HIT_DTYPE

from . import framing, identity
from .analysis import SampicAnalysis
from .geometry import build_geometry
from .settings import as_list
from .template import PulseTemplate, default_template_path

ODB_DIR = "/Analyzer/SampicDQM"

DEFAULTS = {
    "Enabled": True,
    "Max Events Per Second": 200.0,
    "Persistence Depth": 20,
    "Template File": "",
    "Template Window": 12,
    "Amplitude Max V": 0.20,
    "Reset At BOR": True,
    "Per Strip Histograms": True,
}


class SampicAnalyzer:
    def __init__(self, client):
        self.client = client
        self.settings = dict(DEFAULTS)
        self.analysis: Optional[SampicAnalysis] = None
        self.run_state = -1
        self.run_number = 0
        self._last_settings_poll = 0.0
        self._last_status_write = 0.0
        self._seen = 0          # events pulled from the buffer
        self._used = 0          # events actually histogrammed
        self._throttled = 0
        self._rate_window_start = time.monotonic()
        self._rate_window_used = 0
        self.events_per_s = 0.0
        self.build()

    # -- configuration -------------------------------------------------------

    def load_settings(self) -> None:
        """Seed defaults if absent, then read. Never overwrite what is there.

        Seeded with remove_unspecified_keys=False so an operator's extra key or
        a setting from a newer version is left alone rather than deleted.
        """
        # update_structure_only ADDS keys that are missing without touching the
        # values of keys that exist, so a setting introduced in a later version
        # appears in an experiment whose ODB predates it. Seeding only when the
        # whole subtree is absent would leave such a key missing, and a setting
        # that is absent cannot be toggled in mhttpd -- it silently does nothing.
        #
        # remove_unspecified_keys=False so an operator's extra key, or one from
        # a newer version, is left alone rather than deleted.
        self.client.odb_set(ODB_DIR, DEFAULTS, remove_unspecified_keys=False,
                            update_structure_only=True)
        stored = self.client.odb_get(ODB_DIR, recurse_dir=True) or {}
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
        self.settings = merged

    def load_template(self) -> Optional[PulseTemplate]:
        path = str(self.settings.get("Template File", "")).strip()
        if not path:
            # A template shipped with the repo is the sensible default, but its
            # absence is not an error: shape comparison is optional.
            path = default_template_path()
            if not os.path.isfile(path):
                return None
        if not os.path.isfile(path):
            self.client.msg(f"sampic-analyzer: no template file at {path}", True)
            return None
        try:
            t = PulseTemplate.load(path)
            self.client.msg(f"sampic-analyzer: template '{t.name}' from {path} "
                            f"({t.n_samples} samples, averaged over {t.n_averaged})")
            return t
        except Exception as exc:
            self.client.msg(f"sampic-analyzer: cannot read template {path}: {exc}", True)
            return None

    def build(self) -> None:
        """(Re)create the analysis from the current settings and geometry."""
        self.load_settings()
        geom = self.read_geometry()
        self.analysis = SampicAnalysis(
            geometry=geom,
            n_channels=identity.N_DAQ_CHANNELS,
            persistence_depth=int(self.settings["Persistence Depth"]),
            template=self.load_template(),
            amp_max_v=float(self.settings["Amplitude Max V"]),
            template_window=int(self.settings["Template Window"]),
            per_strip=bool(self.settings["Per Strip Histograms"]))

    def read_geometry(self):
        """Rebuild the frontend's geometry from what it published.

        Read rather than assumed: the analyzer must describe the same detector
        the frontend is generating, and that is a setting.
        """
        base = f"/Equipment/{identity.DQM_EQUIP_NAME}/Settings"
        try:
            if not self.client.odb_exists(base + "/Plane Names"):
                return None
            names = as_list(self.client.odb_get(base + "/Plane Names"))
            n_planes = len(names)
            n_strips = int(as_list(self.client.odb_get(base + "/Plane N Strips"))[0])
            pitch = float(as_list(self.client.odb_get(base + "/Plane Pitch mm"))[0])
            z = [float(v) for v in as_list(self.client.odb_get(base + "/Plane Z mm"))]
            orient = [str(v) for v in
                      as_list(self.client.odb_get(base + "/Channel Orientation"))]
            planes_orient = []
            ch_plane = [int(v) for v in
                        as_list(self.client.odb_get(base + "/Channel Plane Index"))]
            for p in range(n_planes):
                idx = next((i for i, v in enumerate(ch_plane) if v == p), None)
                planes_orient.append(orient[idx] if idx is not None and idx < len(orient)
                                     else "XY"[p % 2])
            return build_geometry(n_planes=n_planes, n_strips=n_strips,
                                  pitch_mm=pitch, z_mm=z, orientation=planes_orient,
                                  names=[str(n) for n in names],
                                  n_daq_channels=identity.N_DAQ_CHANNELS)
        except Exception as exc:
            self.client.msg(f"sampic-analyzer: could not read the geometry ({exc}); "
                            "per-plane histograms will be missing", True)
            return None

    # -- event consumption ---------------------------------------------------

    def handle_event(self, event) -> None:
        self._seen += 1
        if not self.settings.get("Enabled", True):
            return
        # Rate limit BEFORE decoding: the point is to bound our own cost, and
        # decoding is most of it.
        limit = float(self.settings.get("Max Events Per Second", 0) or 0)
        now = time.monotonic()
        window = now - self._rate_window_start
        if window >= 1.0:
            self.events_per_s = self._rate_window_used / window
            self._rate_window_start = now
            self._rate_window_used = 0
            window = 0.0
        if limit > 0 and self._rate_window_used >= limit:
            self._throttled += 1
            return

        banks = getattr(event, "banks", {}) or {}
        bank = banks.get("AD00")
        if bank is None:
            return
        data = bank.data
        raw = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
        if len(raw) % AD_HIT_DTYPE.itemsize:
            return
        hits = np.frombuffer(raw, dtype=AD_HIT_DTYPE)
        self.analysis.add_event(hits)
        self._used += 1
        self._rate_window_used += 1

    # -- brpc ----------------------------------------------------------------

    def serve(self, client, cmd, args, max_len):
        """Answer one brpc request. Runs in the client's own thread.

        Never raises: an exception here would propagate into the MIDAS callback
        machinery, and a monitoring page asking a bad question must not be able
        to disturb the analyzer.
        """
        try:
            payload = self._serve(cmd, (args or "").strip(), max_len)
        except Exception as exc:
            payload = framing.encode_error(f"{type(exc).__name__}: {exc}")
        if len(payload) > max_len:
            payload = framing.encode_error(
                f"reply of {len(payload)} bytes exceeds the {max_len} byte limit "
                "the caller allowed; ask for fewer channels")
        buf = ctypes.create_string_buffer(payload, len(payload))
        return midas.status_codes["SUCCESS"], buf

    def _serve(self, cmd: str, args: str, max_len: int) -> bytes:
        a = self.analysis
        if cmd == "sampic::list":
            return framing.encode_list(a.names())
        if cmd == "sampic::status":
            st = a.status()
            st.update({
                "run_state": self.run_state,
                "run_number": self.run_number,
                "seen": self._seen,
                "used": self._used,
                "throttled": self._throttled,
                "events_per_s": round(self.events_per_s, 2),
                "rate_limit": float(self.settings.get("Max Events Per Second", 0)),
                "enabled": bool(self.settings.get("Enabled", True)),
            })
            return framing.encode_json(st)
        if cmd == "sampic::hist":
            h = a.store.get(args)
            if h is None:
                return framing.encode_error(f"no histogram named {args!r}")
            from .hist import Hist1D
            return (framing.encode_hist1(h) if isinstance(h, Hist1D)
                    else framing.encode_hist2(h))
        if cmd == "sampic::persist":
            p = a.store.persistence
            if p is None:
                return framing.encode_error("persistence is not enabled")
            if not args or args == "all":
                chans = p.filled_channels()
            else:
                chans = [int(x) for x in args.replace(" ", "").split(",") if x != ""]
            return framing.encode_persistence(p, chans)
        if cmd == "sampic::template":
            if a.template is None:
                return framing.encode_error(
                    "no pulse template loaded; set /Analyzer/SampicDQM/Template File "
                    "or build one with tools/make_template.py")
            return framing.encode_template(a.template)
        if cmd == "sampic::clear":
            a.reset()
            self._seen = self._used = self._throttled = 0
            return framing.encode_json({"cleared": True})
        return framing.encode_error(f"unknown command {cmd!r}")

    # -- the loop ------------------------------------------------------------

    def poll_run_state(self) -> None:
        try:
            state = int(self.client.odb_get("/Runinfo/State"))
            number = int(self.client.odb_get("/Runinfo/Run number"))
        except Exception:
            return
        if state != self.run_state:
            # Begin of run: start from an empty set so a plot describes one run.
            if state == midas.STATE_RUNNING and self.settings.get("Reset At BOR", True):
                self.build()
                self.analysis.reset()
                self._seen = self._used = self._throttled = 0
                self.client.msg(f"sampic-analyzer: cleared for run {number}")
            self.run_state = state
        self.run_number = number

    def periodic(self) -> None:
        now = time.monotonic()
        if now - self._last_settings_poll >= 2.0:
            self._last_settings_poll = now
            before = dict(self.settings)
            self.load_settings()
            # Rebuild only for the settings that change the shape of the
            # accumulators; a rate-limit change must not throw away the plots.
            for key in ("Persistence Depth", "Template File", "Amplitude Max V",
                        "Template Window", "Per Strip Histograms"):
                if before.get(key) != self.settings.get(key):
                    self.client.msg(f"sampic-analyzer: '{key}' changed, rebuilding")
                    self.build()
                    break
        if now - self._last_status_write >= 5.0:
            self._last_status_write = now
            try:
                self.client.odb_set(ODB_DIR + "/Status", ctypes.create_string_buffer(
                    f"{self._used} events, {len(self.analysis.store)} histograms".encode(),
                    256))
            except Exception:
                pass


def main() -> int:
    client = midas.client.MidasClient(
        os.environ.get("FS_ANALYZER_NAME", "sampic-analyzer"))
    analyzer = SampicAnalyzer(client)
    client.register_brpc_callback(analyzer.serve)

    buf = client.open_event_buffer(identity.BUFFER_NAME, None, 100 * 1024 * 1024)
    # GET_NONBLOCKING: sample, never back-pressure the frontend. See the module
    # docstring.
    client.register_event_request(buf, event_id=-1, trigger_mask=-1,
                                  sampling_type=midas.GET_NONBLOCKING)
    client.msg(f"sampic-analyzer: watching {identity.BUFFER_NAME}, "
               f"{len(analyzer.analysis.store)} histograms, brpc ready")

    # `client.communicate()` handles the shutdown RPC itself -- it disconnects
    # and exits the process on RPC_SHUTDOWN (client.py:286) -- so this loop has
    # no termination condition of its own and must not invent one. Ctrl-C is the
    # only other way out.
    try:
        while True:
            analyzer.poll_run_state()
            got = 0
            while got < 500:
                event = client.receive_event(buf, async_flag=True, use_numpy=True)
                if event is None:
                    break
                if event.header.event_id == identity.PHYSICS_EVENT_ID:
                    analyzer.handle_event(event)
                got += 1
            analyzer.periodic()
            # Yield briefly when the buffer was empty; spinning would burn a core
            # for nothing between events.
            client.communicate(10 if got == 0 else 0)
    except KeyboardInterrupt:
        client.msg("sampic-analyzer: stopping on Ctrl-C")
    finally:
        try:
            client.disconnect()
        except Exception:
            # Already disconnected by communicate()'s shutdown path.
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
