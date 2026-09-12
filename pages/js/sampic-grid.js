// SampicGrid: every channel of one event, laid out as the detector.
//
// One row per plane, one column per strip. A cell holds that channel's
// waveform if the event had a hit on it, and is left empty if not -- so the
// shape of the hit pattern is visible at a glance, which is the thing a
// per-channel list cannot show.
//
// Each cell is a plain 2D canvas rather than an MPlotGraph. An 8x10 detector is
// 80 cells; 80 full figures with axes, legends and mouse handlers would be far
// more machinery than a 64-sample sparkline needs, and slow enough to notice at
// a 2 Hz refresh.
//
// The y-range toggle is the point of the page:
//
//   shared    every cell uses one range taken from the whole event, so cell
//             heights are comparable and you can see which strips actually
//             collected charge. This is the one to trust for amplitudes.
//   per cell  every cell autoscales to its own waveform, so a small pulse is
//             still legible. Good for checking shape and timing on quiet
//             channels, but heights across cells then mean nothing.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  const EVENT_ID = 1;                 // identity.PHYSICS_EVENT_ID
  const CELL_W = 96;
  const CELL_H = 58;
  const PAD = 3;
  // Reserved strip along the top for the amplitude label. Without it, an
  // autoscaled trace fills the full height and runs straight through the text.
  const LABEL_H = 11;

  let geom = null;
  let poller = null;
  let yMode = "shared";
  let built = false;
  const STALE_LIMIT_SEC = 5;
  let info = "";
  // channel -> canvas, so an update is a lookup rather than a DOM search.
  const cells = new Map();

  // --- drawing ------------------------------------------------------------

  function prepare(canvas) {
    // Size the backing store by devicePixelRatio, or the traces are blurry on
    // exactly the high-DPI screens people look at these on.
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== CELL_W * dpr) {
      canvas.width = CELL_W * dpr;
      canvas.height = CELL_H * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, CELL_W, CELL_H);
    return ctx;
  }

  function drawEmpty(canvas, mapped) {
    const ctx = prepare(canvas);
    ctx.fillStyle = mapped ? "#f6f6f8" : "#ececf0";
    ctx.fillRect(0, 0, CELL_W, CELL_H);
    if (!mapped) {
      ctx.strokeStyle = "#d2d2da";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, CELL_H); ctx.lineTo(CELL_W, 0);
      ctx.stroke();
    }
  }

  function drawWave(canvas, hits, lo, hi) {
    const ctx = prepare(canvas);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, CELL_W, CELL_H);

    if (!(hi > lo)) { hi = lo + 1e-3; }
    const top = PAD + LABEL_H;
    const h = CELL_H - PAD - top;
    const yOf = function (v) { return top + h * (1 - (v - lo) / (hi - lo)); };

    // Baseline reference, so a flat trace still tells you where zero signal is.
    const base = hits[0].baseline;
    if (base >= lo && base <= hi) {
      ctx.strokeStyle = "#e3e3e9";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, yOf(base)); ctx.lineTo(CELL_W, yOf(base));
      ctx.stroke();
    }

    hits.forEach(function (hit, k) {
      const wf = hit.waveform;
      if (!wf.length) return;
      const dx = (CELL_W - 2 * PAD) / Math.max(wf.length - 1, 1);
      ctx.strokeStyle = k === 0 ? "#1f6fb4" : "#c0562b";
      ctx.lineWidth = k === 0 ? 1.25 : 1;
      ctx.beginPath();
      for (let s = 0; s < wf.length; s++) {
        const x = PAD + s * dx;
        const y = yOf(wf[s]);
        if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });

    // Amplitude in the corner. In per-cell mode the trace height is
    // meaningless across cells, so the number is the only comparable thing.
    ctx.fillStyle = "#6a6b73";
    ctx.font = "9px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText((hits[0].amplitude * 1e3).toFixed(1) + "mV", CELL_W - 3, LABEL_H);
  }

  // --- layout -------------------------------------------------------------

  function controls() {
    const bar = SDQM.el("div", "sdqm-controls");

    const pause = document.createElement("button");
    const isPaused = poller ? poller.isPaused() : false;
    pause.textContent = isPaused ? "resume" : "pause";
    pause.onclick = function () {
      if (poller) poller.setPaused(!poller.isPaused());
      refreshControls();
    };
    bar.appendChild(pause);

    const lbl = SDQM.el("label");
    lbl.appendChild(SDQM.el("span", null, "y range"));
    const sel = document.createElement("select");
    [["shared", "shared across the event"], ["cell", "per cell (autoscale)"]]
      .forEach(function (o) {
        const opt = document.createElement("option");
        opt.value = o[0]; opt.textContent = o[1];
        sel.appendChild(opt);
      });
    sel.value = yMode;
    sel.onchange = function () {
      yMode = sel.value;
      // Reflect the mode in the URL so a particular view can be bookmarked or
      // put on a shift screen, and so it survives a reload.
      try {
        const u = new URL(window.location.href);
        u.searchParams.set("y", yMode);
        window.history.replaceState(null, "", u.toString());
      } catch (e) { /* older browsers: the toggle still works */ }
      redrawLast();
    };
    lbl.appendChild(sel);
    bar.appendChild(lbl);

    bar.appendChild(SDQM.el("span", "sdqm-muted", info));
    const age = poller ? poller.ageSec() : null;
    bar.appendChild(SDQM.statusChip(
      age === null ? { state: "never", text: "waiting for an event" }
                   : (age <= STALE_LIMIT_SEC
                      ? { state: "live", text: "live" }
                      : { state: "stale",
                          text: "stale, " + SDQM.ageText(age) + " since last event" })));
    return bar;
  }

  function refreshControls() {
    const root = document.getElementById(ROOT);
    const old = root.querySelector(".sdqm-controls");
    if (old) old.replaceWith(controls());
  }

  function build() {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    root.appendChild(controls());
    cells.clear();

    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null,
      "Event waveforms — " + geom.nPlanes + " planes × " +
      (geom.planeNStrips[0] || 0) + " strips"));

    const grid = SDQM.el("div", "sdqm-grid");
    const nStrips = geom.planeNStrips[0] || 0;
    grid.style.gridTemplateColumns = "auto repeat(" + nStrips + ", " + CELL_W + "px)";

    grid.appendChild(SDQM.el("div", "sdqm-grid-corner", ""));
    for (let s = 0; s < nStrips; s++) {
      grid.appendChild(SDQM.el("div", "sdqm-grid-colhead", "s" + s));
    }

    for (let p = 0; p < geom.nPlanes; p++) {
      const ch0 = geom.planeChannels[p][0];
      const orient = (ch0 >= 0 ? geom.channelOrientation[ch0] : "") || "";
      grid.appendChild(SDQM.el("div", "sdqm-grid-rowhead",
        geom.planeNames[p] + " (" + orient + ")"));
      for (let s = 0; s < nStrips; s++) {
        const ch = geom.planeChannels[p][s];
        const holder = SDQM.el("div", "sdqm-grid-cell");
        const canvas = document.createElement("canvas");
        canvas.style.width = CELL_W + "px";
        canvas.style.height = CELL_H + "px";
        canvas.title = ch >= 0
          ? geom.planeNames[p] + " strip " + s + " · channel " + ch
          : geom.planeNames[p] + " strip " + s + " · no DAQ channel";
        holder.appendChild(canvas);
        grid.appendChild(holder);
        if (ch >= 0) cells.set(ch, canvas);
        drawEmpty(canvas, ch >= 0);
      }
    }
    card.appendChild(grid);
    card.appendChild(SDQM.el("div", "sdqm-muted",
      "Blue trace is the hit; a second hit on the same channel is drawn in red. " +
      "The faint horizontal line is that hit's baseline. Hatched cells have no " +
      "DAQ channel."));
    root.appendChild(card);
    built = true;
  }

  // --- update -------------------------------------------------------------

  let lastHits = null;

  function redrawLast() {
    refreshControls();
    if (lastHits) render(lastHits);
  }

  function render(hits) {
    if (!built) build();

    const byChannel = new Map();
    hits.forEach(function (h) {
      if (!byChannel.has(h.channel)) byChannel.set(h.channel, []);
      byChannel.get(h.channel).push(h);
    });

    let lo = Infinity, hi = -Infinity;
    if (yMode === "shared") {
      hits.forEach(function (h) {
        for (let s = 0; s < h.waveform.length; s++) {
          if (h.waveform[s] < lo) lo = h.waveform[s];
          if (h.waveform[s] > hi) hi = h.waveform[s];
        }
      });
      const pad = (hi - lo) * 0.06 || 1e-3;
      lo -= pad; hi += pad;
    }

    cells.forEach(function (canvas, ch) {
      const hs = byChannel.get(ch);
      if (!hs) { drawEmpty(canvas, true); return; }
      let cLo = lo, cHi = hi;
      if (yMode !== "shared") {
        cLo = Infinity; cHi = -Infinity;
        hs.forEach(function (h) {
          for (let s = 0; s < h.waveform.length; s++) {
            if (h.waveform[s] < cLo) cLo = h.waveform[s];
            if (h.waveform[s] > cHi) cHi = h.waveform[s];
          }
        });
        const pad = (cHi - cLo) * 0.08 || 1e-3;
        cLo -= pad; cHi += pad;
      }
      drawWave(canvas, hs, cLo, cHi);
    });

    const range = (yMode === "shared" && isFinite(lo))
      ? " · y " + lo.toFixed(4) + " to " + hi.toFixed(4) + " V"
      : " · each cell autoscaled";
    info = hits.length + " hits on " + byChannel.size + " channels" + range;
    refreshControls();
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicGrid", 1000);
    const wanted = mhttpd_getParameterByName("y");
    if (wanted === "cell" || wanted === "shared") yMode = wanted;
    SDQM.loadGeometry().then(function (g) {
      if (!g) {
        SDQM.setBanner(ROOT,
          "No geometry in " + SDQM.DQM_SETTINGS + ". Start the frontend once; " +
          "it publishes the channel map at startup.", "warn");
        return;
      }
      geom = g;
      build();
      poller = SDQM.eventPoller({
        eventId: EVENT_ID,
        intervalMs: 500,
        onEvent: function (event) {
          const hits = SAMPIC.hitsOf(event);
          if (!hits.length) return;
          lastHits = hits;
          SDQM.setBanner(ROOT, "");
          render(hits);
        },
        onError: function (e) {
          SDQM.setBanner(ROOT, "bm_receive_event failed: " + e, "error");
        }
      });
      poller.start();
      // Repaint the chip even when no event arrives, or "stale" never shows.
      setInterval(refreshControls, 1000);
    });
  });
})();
