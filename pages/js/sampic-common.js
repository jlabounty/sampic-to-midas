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
    return mjsonrpc_db_get_values(paths).then(function (rpc) {
      return rpc.result.data;
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

  return {
    EQ_REPLAY: EQ_REPLAY, EQ_DQM: EQ_DQM,
    DQM_SETTINGS: DQM_SETTINGS, DQM_VARS: DQM_VARS,
    odbGet: odbGet, asArray: asArray, num: num,
    loadGeometry: loadGeometry,
    fmt: fmt, fmtRate: fmtRate, heatColour: heatColour,
    el: el, setBanner: setBanner
  };
})();
