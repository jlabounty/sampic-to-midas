// SampicStrips: the detector as a picture -- one row per plane, one cell per
// strip, coloured by hit rate or mean amplitude.
//
// The geometry is read from the ODB, never hardcoded: change N Planes or Strips
// Per Plane in Settings and this page draws the new detector. That is the point
// of generating data for a detector that does not exist yet.
//
// Three states are kept visually distinct, because confusing them is how a DQM
// page misleads someone at 3am:
//   not connected  grey, "n/c"    -- no DAQ channel maps to this strip
//   connected, 0   near-black     -- a real zero, which may be a real problem
//   busy           blue -> orange -- scaled to the busiest strip on the page
//
// The DOM IS BUILT ONCE. Only cell colours, titles and the scale text are
// updated on refresh. Rebuilding the page every two seconds would also rebuild
// the <select>, closing it under the pointer of anyone trying to use it.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  const METRICS = {
    rate: { bank: "FSRT", unit: "Hz", label: "hit rate (FSRT)" },
    amp:  { bank: "FSAM", unit: "V",  label: "mean amplitude (FSAM)" }
  };

  let geom = null;
  let metric = "rate";
  let built = false;
  let stalePeriodSec = 5;          // replaced by Common/Period once known
  // channel -> cell element, so an update is a lookup rather than a DOM search.
  const cells = new Map();
  let elChip = null, elScaleMax = null, elTitle = null, elGrid = null;

  function buildOnce() {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    cells.clear();

    const bar = SDQM.el("div", "sdqm-controls");
    const lbl = SDQM.el("label");
    lbl.appendChild(SDQM.el("span", null, "colour by"));
    const sel = document.createElement("select");
    Object.keys(METRICS).forEach(function (k) {
      const opt = document.createElement("option");
      opt.value = k; opt.textContent = METRICS[k].label;
      sel.appendChild(opt);
    });
    sel.value = metric;
    sel.onchange = function () { metric = sel.value; refresh(); };
    lbl.appendChild(sel);
    bar.appendChild(lbl);
    elChip = SDQM.statusChip({ state: "never", text: "no data yet" });
    bar.appendChild(elChip);
    root.appendChild(bar);

    const card = SDQM.el("div", "sdqm-card");
    elTitle = SDQM.el("h3", null, "Strip occupancy");
    card.appendChild(elTitle);

    elGrid = SDQM.el("div", "sdqm-planes");
    for (let p = 0; p < geom.nPlanes; p++) {
      const row = SDQM.el("div", "sdqm-plane");
      const ch0 = geom.planeChannels[p][0];
      const orient = (ch0 >= 0 ? geom.channelOrientation[ch0] : "") || "";
      row.appendChild(SDQM.el("div", "name",
        geom.planeNames[p] + " (" + orient + ") " +
        SDQM.fmt(geom.planeZ[p], 0) + "mm"));
      const strips = SDQM.el("div", "sdqm-strips");
      geom.planeChannels[p].forEach(function (ch, s) {
        const cell = SDQM.el("div", "sdqm-strip" + (ch < 0 ? " unmapped" : ""));
        cell.textContent = ch < 0 ? "n/c" : String(s);
        cell.dataset.plane = geom.planeNames[p];
        cell.dataset.strip = String(s);
        strips.appendChild(cell);
        if (ch >= 0) cells.set(ch, cell);
      });
      row.appendChild(strips);
      elGrid.appendChild(row);
    }
    card.appendChild(elGrid);

    const scale = SDQM.el("div", "sdqm-scale");
    scale.appendChild(SDQM.el("span", null, "0"));
    scale.appendChild(SDQM.el("div", "bar"));
    elScaleMax = SDQM.el("span", null, "-");
    scale.appendChild(elScaleMax);
    scale.appendChild(SDQM.el("span", "sdqm-muted",
      "  · grey = no DAQ channel, black = mapped but silent"));
    card.appendChild(scale);
    root.appendChild(card);
    built = true;
  }

  function update(values, fresh) {
    const m = METRICS[metric];
    let max = 0;
    for (const v of values) if (v > max) max = v;

    cells.forEach(function (cell, ch) {
      const v = (ch < values.length) ? values[ch] : -1;
      cell.style.background = SDQM.heatColour(v, max);
      cell.title = cell.dataset.plane + " strip " + cell.dataset.strip +
        "\nchannel " + ch +
        "\nposition " + SDQM.fmt(geom.channelPos[ch], 2) + " mm\n" +
        (metric === "rate" ? SDQM.fmtRate(v) : SDQM.fmt(v, 4) + " " + m.unit);
    });

    elTitle.textContent = "Strip occupancy — " + geom.nPlanes +
      " planes × " + (geom.planeNStrips[0] || 0) + " strips";
    elScaleMax.textContent = metric === "rate"
      ? SDQM.fmtRate(max) : SDQM.fmt(max, 4) + " " + m.unit;

    // Dim the whole map when the numbers are old, so a frozen picture cannot be
    // mistaken for a quiet detector.
    elGrid.classList.toggle("sdqm-stale-data", fresh.state !== "live");
    const chip = SDQM.statusChip(fresh);
    elChip.replaceWith(chip);
    elChip = chip;
  }

  function refresh() {
    const bank = METRICS[metric].bank;
    SDQM.odbGetFull([
      SDQM.DQM_VARS + "/" + bank,
      "/Equipment/" + SDQM.EQ_DQM + "/Common/Period"
    ]).then(function (r) {
      if (r.data[0] === null || r.data[0] === undefined) {
        SDQM.setBanner(ROOT, "No " + bank + " bank in the ODB yet. Start the " +
                             "frontend with scripts/start-frontend.sh.", "warn");
        return;
      }
      SDQM.setBanner(ROOT, "");
      const period = SDQM.num(r.data[1], 2000) / 1000;
      // 2.5 periods: one missed update is jitter, three is something wrong.
      stalePeriodSec = Math.max(period * 2.5, 3);

      let values = SDQM.asArray(r.data[0]).map(Number);
      if (metric === "amp") {
        // -999 is "mapped channel, no hits this period"; drawn as a huge
        // negative it would flatten the colour scale for every other strip.
        values = values.map(function (v) { return v <= -900 ? 0 : v; });
      }
      update(values, SDQM.freshness(r.lastWritten[0], stalePeriodSec));
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
      buildOnce();
      refresh();
      setInterval(refresh, 2000);
    });
  });
})();
