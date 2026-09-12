// SampicStrips: the detector as a picture -- one row per plane, one cell per
// strip, coloured by hit rate or mean amplitude.
//
// The geometry is read from the ODB, never hardcoded: change N Planes or Strips
// Per Plane in Settings and this page draws the new detector on the next
// refresh. That is the whole point of generating data for a detector that does
// not exist yet.
//
// Three states are kept visually distinct, because confusing them is how a DQM
// page misleads someone at 3am:
//   not connected  grey, "n/c"    -- no DAQ channel maps to this strip
//   connected, 0   near-black     -- a real zero, which may be a real problem
//   busy           blue -> orange -- scaled to the busiest strip on the page

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  let geom = null;
  let metric = "rate";

  function buildControls() {
    const bar = SDQM.el("div", "sdqm-controls");
    const lbl = SDQM.el("label");
    lbl.appendChild(SDQM.el("span", null, "colour by"));
    const sel = document.createElement("select");
    [["rate", "hit rate (FSRT)"], ["amp", "mean amplitude (FSAM)"]]
      .forEach(function (o) {
        const opt = document.createElement("option");
        opt.value = o[0]; opt.textContent = o[1];
        sel.appendChild(opt);
      });
    sel.value = metric;
    sel.onchange = function () { metric = sel.value; refresh(); };
    lbl.appendChild(sel);
    bar.appendChild(lbl);
    return bar;
  }

  function render(values, unit) {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    root.appendChild(buildControls());

    let max = 0;
    for (const v of values) if (v > max) max = v;

    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null,
      "Strip occupancy — " + geom.nPlanes + " planes × " +
      (geom.planeNStrips[0] || 0) + " strips"));

    const planes = SDQM.el("div", "sdqm-planes");
    for (let p = 0; p < geom.nPlanes; p++) {
      const row = SDQM.el("div", "sdqm-plane");
      const orient = geom.channelOrientation[geom.planeChannels[p][0]] || "";
      row.appendChild(SDQM.el("div", "name",
        geom.planeNames[p] + " (" + orient + ") " +
        SDQM.fmt(geom.planeZ[p], 0) + "mm"));
      const strips = SDQM.el("div", "sdqm-strips");
      geom.planeChannels[p].forEach(function (ch, s) {
        const v = (ch >= 0 && ch < values.length) ? values[ch] : -1;
        const cell = SDQM.el("div", "sdqm-strip" + (v < 0 ? " unmapped" : ""));
        cell.style.background = SDQM.heatColour(v, max);
        cell.textContent = v < 0 ? "n/c" : (max > 0 ? String(s) : String(s));
        const pos = (ch >= 0) ? SDQM.fmt(geom.channelPos[ch], 2) + " mm" : "-";
        cell.title = geom.planeNames[p] + " strip " + s +
          (ch >= 0 ? "\nchannel " + ch + "\nposition " + pos +
                     "\n" + (metric === "rate" ? SDQM.fmtRate(v)
                                               : SDQM.fmt(v, 4) + " " + unit)
                   : "\nno DAQ channel");
        strips.appendChild(cell);
      });
      row.appendChild(strips);
      planes.appendChild(row);
    }
    card.appendChild(planes);

    const scale = SDQM.el("div", "sdqm-scale");
    scale.appendChild(SDQM.el("span", null, "0"));
    scale.appendChild(SDQM.el("div", "bar"));
    scale.appendChild(SDQM.el("span", null,
      metric === "rate" ? SDQM.fmtRate(max) : SDQM.fmt(max, 4) + " " + unit));
    scale.appendChild(SDQM.el("span", "sdqm-muted",
      "  · grey = no DAQ channel, black = mapped but silent"));
    card.appendChild(scale);
    root.appendChild(card);
  }

  function refresh() {
    const bank = metric === "rate" ? "/FSRT" : "/FSAM";
    SDQM.odbGet([SDQM.DQM_VARS + bank]).then(function (d) {
      if (d[0] === null || d[0] === undefined) {
        SDQM.setBanner(ROOT, "No " + bank.slice(1) + " bank in the ODB yet.", "warn");
        return;
      }
      let values = SDQM.asArray(d[0]).map(Number);
      if (metric === "amp") {
        // -999 is "mapped channel, no hits this period"; drawing it as a huge
        // negative would blow the colour scale for every other strip.
        values = values.map(function (v) { return v <= -900 ? 0 : v; });
      }
      render(values, metric === "rate" ? "Hz" : "V");
    }).catch(function (e) {
      SDQM.setBanner(ROOT, "ODB read failed: " + e, "error");
    });
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicStrips", 2000);
    SDQM.loadGeometry().then(function (g) {
      if (!g) {
        SDQM.setBanner(ROOT,
          "No geometry in " + SDQM.DQM_SETTINGS + ". Start the frontend once; " +
          "it publishes the channel map at startup.", "warn");
        return;
      }
      geom = g;
      refresh();
      setInterval(refresh, 2000);
    });
  });
})();
