// Shared helpers for the fake-SAMPIC custom pages.
//
// These pages are guests: they are registered by absolute path under /Custom and
// never touch /Custom/Path, so they can be installed into any experiment without
// disturbing pages that are already there.
//
// Everything they need about the detector comes from
// /Equipment/FakeSampicDQM/Settings, which the frontend rewrites at every start.
// No geometry is hardcoded here -- change the plane count in the ODB and the
// pages follow.

const SDQM = (function () {
  "use strict";

  const EQ_REPLAY = "FakeSampic";
  const EQ_DQM = "FakeSampicDQM";
  const DQM_SETTINGS = "/Equipment/" + EQ_DQM + "/Settings";
  const DQM_VARS = "/Equipment/" + EQ_DQM + "/Variables";

  // --- ODB access ---------------------------------------------------------

  function odbGet(paths) {
    return odbGetFull(paths).then(function (r) { return r.data; });
  }

  // The full reply, including `last_written`. That is how a page knows whether
  // a value is CURRENT rather than merely readable: the ODB keeps serving the
  // last number a dead frontend wrote, forever, and a page that only reads the
  // value cannot tell the difference. See `freshness`.
  function odbGetFull(paths) {
    return mjsonrpc_db_get_values(paths).then(function (rpc) {
      return {
        data: rpc.result.data,
        lastWritten: rpc.result.last_written || [],
        status: rpc.result.status || []
      };
    });
  }

  // MIDAS returns a one-element array as a bare scalar over mjsonrpc, exactly as
  // it does in python. Every array the geometry publishes can legitimately have
  // one entry (a single-plane detector), so normalise rather than special-case.
  function asArray(v) {
    if (v === null || v === undefined) return [];
    return Array.isArray(v) ? v : [v];
  }

  function num(v, dflt) {
    const x = typeof v === "string" ? parseFloat(v) : v;
    return (typeof x === "number" && isFinite(x)) ? x : (dflt === undefined ? 0 : dflt);
  }

  // --- geometry -----------------------------------------------------------

  // Load the channel map the frontend publishes. Resolves to null when the
  // frontend has never run, which the pages report as a message rather than
  // rendering an empty detector that looks like a dead one.
  function loadGeometry() {
    const keys = ["N Channels", "Channel Plane Index", "Channel Strip",
                  "Channel Position mm", "Channel Plane", "Channel Orientation",
                  "Plane Names", "Plane Z mm", "Plane Pitch mm", "Plane N Strips"];
    return odbGet(keys.map(function (k) { return DQM_SETTINGS + "/" + k; }))
      .then(function (d) {
        if (!d || d[0] === null || d[0] === undefined) return null;
        const planeNames = asArray(d[6]).map(String);
        if (!planeNames.length) return null;
        const chPlane = asArray(d[1]).map(Number);
        const geom = {
          nChannels: num(d[0], 128),
          channelPlane: chPlane,
          channelStrip: asArray(d[2]).map(Number),
          channelPos: asArray(d[3]).map(Number),
          channelPlaneName: asArray(d[4]).map(String),
          channelOrientation: asArray(d[5]).map(String),
          planeNames: planeNames,
          planeZ: asArray(d[7]).map(Number),
          planePitch: asArray(d[8]).map(Number),
          planeNStrips: asArray(d[9]).map(Number),
          nPlanes: planeNames.length
        };
        // plane index -> [channel per strip], so a strip map can be drawn
        // without searching the channel list for every cell.
        geom.planeChannels = planeNames.map(function (_, p) {
          const n = geom.planeNStrips[p] || 0;
          const row = new Array(n).fill(-1);
          for (let ch = 0; ch < chPlane.length; ch++) {
            if (chPlane[ch] === p) {
              const s = geom.channelStrip[ch];
              if (s >= 0 && s < n) row[s] = ch;
            }
          }
          return row;
        });
        return geom;
      });
  }

  // --- formatting ---------------------------------------------------------

  function fmt(x, digits) {
    if (x === null || x === undefined || !isFinite(x)) return "-";
    const d = digits === undefined ? 2 : digits;
    if (Math.abs(x) >= 1e5 || (Math.abs(x) < 1e-3 && x !== 0)) return x.toExponential(2);
    return x.toFixed(d);
  }

  function fmtRate(hz) {
    if (hz < 0) return "n/c";             // unmapped channel sentinel (-1)
    if (hz >= 1e6) return (hz / 1e6).toFixed(2) + " MHz";
    if (hz >= 1e3) return (hz / 1e3).toFixed(2) + " kHz";
    return hz.toFixed(1) + " Hz";
  }

  // Sequential colour ramp for occupancy. Distinguishes three states a detector
  // page must not confuse: not connected (grey), connected but silent (dark),
  // and increasingly busy (blue -> yellow).
  function heatColour(value, max) {
    if (value < 0) return "#2a2a2e";
    if (max <= 0 || value === 0) return "#16171b";
    const t = Math.max(0, Math.min(1, value / max));
    const stops = [[22, 23, 27], [31, 78, 121], [43, 140, 190], [120, 198, 121],
                   [247, 215, 88], [240, 130, 60]];
    const x = t * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(x));
    const f = x - i;
    const c = [0, 1, 2].map(function (k) {
      return Math.round(stops[i][k] + f * (stops[i + 1][k] - stops[i][k]));
    });
    return "rgb(" + c.join(",") + ")";
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function setBanner(rootId, message, kind) {
    const root = document.getElementById(rootId);
    if (!root) return;
    let b = root.querySelector(".sdqm-banner");
    if (!message) { if (b) b.remove(); return; }
    if (!b) {
      b = el("div", "sdqm-banner");
      root.insertBefore(b, root.firstChild);
    }
    b.className = "sdqm-banner " + (kind || "info");
    b.textContent = message;
  }

  // --- freshness ----------------------------------------------------------

  // Is this data current? Three states, deliberately distinct:
  //   live   updated within the limit
  //   stale  readable, but older than it should be -- the frontend is gone or
  //          wedged and the ODB is still serving its last value
  //   never  nothing has ever been written
  //
  // A DQM page that cannot say "this number is old" is worse than no page: the
  // numbers look fine and someone acts on them.
  function freshness(lastWrittenSec, limitSec) {
    if (!lastWrittenSec) return { state: "never", ageSec: null, text: "no data yet" };
    // Server clock vs browser clock: a small skew must not read as the future.
    const ms = Math.min(lastWrittenSec * 1000, Date.now());
    const age = (Date.now() - ms) / 1000;
    if (age <= limitSec) {
      return { state: "live", ageSec: age, text: "live" };
    }
    return { state: "stale", ageSec: age, text: "stale, " + ageText(age) + " old" };
  }

  function ageText(sec) {
    if (sec < 90) return Math.round(sec) + " s";
    if (sec < 5400) return Math.round(sec / 60) + " min";
    return (sec / 3600).toFixed(1) + " h";
  }

  // A small coloured chip. Reused by every page so "is it live" looks the same
  // everywhere -- an operator should not have to learn each page's convention.
  function statusChip(fresh, extra) {
    const chip = el("span", "sdqm-chip " + fresh.state);
    chip.textContent = fresh.text + (extra ? " \u00b7 " + extra : "");
    return chip;
  }

  // --- event polling ------------------------------------------------------

  // One implementation of "give me the newest event from the buffer", shared by
  // every page that reads events. It was copy-pasted between two pages before
  // this existed, which is how the event_id workaround below drifts out of sync.
  //
  //   const poller = SDQM.eventPoller({ eventId: 1, onEvent: fn });
  //   poller.start();  poller.setPaused(true);  poller.stop();
  //
  // `onEvent(event)` gets the bkToObj() result. `onIdle()` fires when the
  // buffer had nothing, which is normal between events and while the run is
  // stopped -- it is NOT an error and must not be rendered as one.
  function eventPoller(opts) {
    const o = opts || {};
    const eventId = (o.eventId === undefined) ? 1 : o.eventId;
    const bufferName = o.bufferName || "SYSTEM";
    const intervalMs = o.intervalMs || 500;
    let timer = null, inFlight = false, paused = false;
    let lastHeader = null, lastEventMs = 0, lastStampSec = 0;

    function tick() {
      if (paused || inFlight) return;
      inFlight = true;
      // event_id: -1 on the wire, filtered below. mhttpd answers a SPECIFIC
      // event_id with status 209 ("nothing available") even when matching
      // events are in the buffer -- verified against midas-2026-07-a. The
      // buffer also carries other equipment's events, so the filter is needed
      // regardless.
      const req = {
        buffer_name: bufferName, event_id: -1, trigger_mask: -1, get_recent: true
      };
      if (lastHeader) req.last_event_header = lastHeader;

      mjsonrpc_call("bm_receive_event", req, "arraybuffer").then(function (rpc) {
        inFlight = false;
        // A JSON reply rather than binary means no event was available.
        if (!(rpc instanceof ArrayBuffer)) { if (o.onIdle) o.onIdle(); return; }
        const event = bkToObj(rpc);
        if (!event || (eventId >= 0 && event.event_id !== eventId)) {
          if (o.onIdle) o.onIdle();
          return;
        }
        // Only a CHANGING event counts as liveness. With get_recent the buffer
        // keeps handing back the same last event after the frontend dies, so
        // counting every reply as an arrival would leave the page reporting
        // "live" over a stopped DAQ -- which is the exact failure the freshness
        // chip exists to prevent. (Same lesson as the WaveDream scaler page,
        // where only a changing timestamp counts as a fresh read.)
        const isNew = !lastHeader ||
                      lastHeader[2] !== event.serial_number ||
                      lastHeader[3] !== event.time_stamp;
        lastHeader = [event.event_id, event.trigger_mask,
                      event.serial_number, event.time_stamp];
        if (!isNew) { if (o.onIdle) o.onIdle(); return; }
        lastEventMs = Date.now();
        // The MIDAS event header carries the wall-clock second the frontend
        // built the event. Age is measured from THAT, not from when this page
        // received it -- otherwise a page opened onto a dead experiment reports
        // "live", because the first stale event out of the buffer is genuinely
        // new to a freshly loaded page.
        lastStampSec = event.time_stamp || 0;
        if (o.onEvent) o.onEvent(event);
      }).catch(function (err) {
        inFlight = false;
        if (o.onError) o.onError(err);
      });
    }

    return {
      start: function () { if (!timer) { tick(); timer = setInterval(tick, intervalMs); } },
      stop: function () { if (timer) { clearInterval(timer); timer = null; } },
      setPaused: function (v) { paused = !!v; },
      isPaused: function () { return paused; },
      // Age of the newest event we hold, in seconds, or null if we have none.
      // Taken from the event's own timestamp where it has one, so it measures
      // how old the DATA is rather than how long this page has been open.
      ageSec: function () {
        if (lastStampSec) {
          // Clamp for server/browser clock skew, as the ODB path does.
          return (Date.now() - Math.min(lastStampSec * 1000, Date.now())) / 1000;
        }
        return lastEventMs ? (Date.now() - lastEventMs) / 1000 : null;
      }
    };
  }

  return {
    EQ_REPLAY: EQ_REPLAY, EQ_DQM: EQ_DQM,
    DQM_SETTINGS: DQM_SETTINGS, DQM_VARS: DQM_VARS,
    odbGet: odbGet, odbGetFull: odbGetFull, asArray: asArray, num: num,
    freshness: freshness, ageText: ageText, statusChip: statusChip,
    eventPoller: eventPoller,
    loadGeometry: loadGeometry,
    fmt: fmt, fmtRate: fmtRate, heatColour: heatColour,
    el: el, setBanner: setBanner
  };
})();
