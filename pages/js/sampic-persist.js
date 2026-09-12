// SampicPersist: the detector laid out as a grid, each cell showing that
// channel's last N waveforms overlaid.
//
// This is SampicGrid's layout with the analyzer behind it. The difference
// matters: SampicGrid shows ONE event, sampled at whatever rate the page polls;
// this shows the last N pulses each channel actually produced, accumulated by
// the analyzer at full rate and surviving a page reload. A channel that misfires
// one pulse in fifty is invisible on the former and obvious here.
//
// Traces are drawn oldest-faintest, so the newest pulse is readable against the
// history rather than lost in it.
//
// With a canonical pulse shape loaded, the template is overlaid in red, scaled
// to each cell's own amplitude. That is the comparison the whole template
// machinery exists for: amplitude and timing can both be right while the SHAPE
// is wrong, and a shape is only judged against a reference.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  const CELL_W = 96;
  const CELL_H = 58;
  const PAD = 3;
  const LABEL_H = 11;
  const REFRESH_MS = 2000;

  let geom = null;
  let built = false;
  let yMode = "shared";
  let showTemplate = true;
  let template = null;
  let timer = null;
  let paused = false;
  let info = "";
  const cells = new Map();
  let elChip = null, elControls = null;
  let lastData = null;

  // --- drawing ------------------------------------------------------------

  function prepare(canvas) {
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
      ctx.beginPath(); ctx.moveTo(0, CELL_H); ctx.lineTo(CELL_W, 0); ctx.stroke();
    }
  }

  function drawCell(canvas, waves, lo, hi) {
    const ctx = prepare(canvas);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, CELL_W, CELL_H);
    if (!(hi > lo)) hi = lo + 1e-3;

    const top = PAD + LABEL_H;
    const h = CELL_H - PAD - top;
    const yOf = function (v) { return top + h * (1 - (v - lo) / (hi - lo)); };
    const n = waves.length;

    for (let k = 0; k < n; k++) {
      const wf = waves[k];
      // Oldest faintest. The newest trace is full strength so it reads against
      // the accumulated history instead of disappearing into it.
      const age = n === 1 ? 1 : k / (n - 1);
      ctx.strokeStyle = "rgba(31,111,180," + (0.16 + 0.84 * age * age).toFixed(3) + ")";
      ctx.lineWidth = k === n - 1 ? 1.3 : 0.8;
      const dx = (CELL_W - 2 * PAD) / Math.max(wf.length - 1, 1);
      ctx.beginPath();
      for (let s = 0; s < wf.length; s++) {
        const x = PAD + s * dx, y = yOf(wf[s]);
        if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    if (showTemplate && template && n) {
      // Scale the unit-peak template onto this cell's newest pulse, and align
      // their peaks, so what is being compared is SHAPE and nothing else.
      const last = waves[n - 1];
      let base = 0;
      for (let s = 0; s < Math.min(8, last.length); s++) base += last[s];
      base /= Math.min(8, last.length);
      let peak = -Infinity, peakAt = 0;
      for (let s = 0; s < last.length; s++) {
        if (last[s] > peak) { peak = last[s]; peakAt = s; }
      }
      const amp = peak - base;
      const shift = peakAt - template.peakIndex;
      ctx.strokeStyle = "rgba(200,60,40,0.85)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]);
      const dx = (CELL_W - 2 * PAD) / Math.max(last.length - 1, 1);
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < template.samples.length; i++) {
        const s = i + shift;
        if (s < 0 || s >= last.length) continue;
        const x = PAD + s * dx, y = yOf(base + amp * template.samples[i]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.fillStyle = "#6a6b73";
    ctx.font = "9px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(n + "×", CELL_W - 3, LABEL_H);
  }

  // --- layout -------------------------------------------------------------

  function controls() {
    const bar = SDQM.el("div", "sdqm-controls");

    const pause = document.createElement("button");
    pause.textContent = paused ? "resume" : "pause";
    pause.onclick = function () { paused = !paused; refreshControls(); };
    bar.appendChild(pause);

    const yl = SDQM.el("label");
    yl.appendChild(SDQM.el("span", null, "y range"));
    const ysel = document.createElement("select");
    [["shared", "shared across all channels"], ["cell", "per cell (autoscale)"]]
      .forEach(function (o) {
        const opt = document.createElement("option");
        opt.value = o[0]; opt.textContent = o[1];
        ysel.appendChild(opt);
      });
    ysel.value = yMode;
    ysel.onchange = function () { yMode = ysel.value; redraw(); };
    yl.appendChild(ysel);
    bar.appendChild(yl);

    const tl = SDQM.el("label");
    const tcb = document.createElement("input");
    tcb.type = "checkbox";
    tcb.checked = showTemplate;
    tcb.disabled = !template;
    tcb.onchange = function () { showTemplate = tcb.checked; redraw(); };
    tl.appendChild(tcb);
    tl.appendChild(SDQM.el("span", null,
      template ? "overlay template '" + template.name + "'"
               : "no template loaded"));
    bar.appendChild(tl);

    const clear = document.createElement("button");
    clear.textContent = "clear";
    clear.onclick = function () {
      SBRPC.call("sampic::clear").then(refresh).catch(showError);
    };
    bar.appendChild(clear);

    elChip = SDQM.statusChip({ state: "never", text: "no data yet" });
    bar.appendChild(elChip);
    bar.appendChild(SDQM.el("span", "sdqm-muted", info));
    return bar;
  }

  function refreshControls() {
    const root = document.getElementById(ROOT);
    const old = root.querySelector(".sdqm-controls");
    const fresh = controls();
    if (old) old.replaceWith(fresh); else root.insertBefore(fresh, root.firstChild);
  }

  function build() {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    cells.clear();
    root.appendChild(controls());

    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null,
      "Persistence — last N waveforms per channel"));

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
      "Oldest traces are faintest; the newest is solid. The dashed red curve is " +
      "the canonical pulse shape, scaled to the newest pulse's amplitude and " +
      "aligned on its peak — so only the SHAPE is being compared."));
    root.appendChild(card);
    built = true;
  }

  function redraw() {
    refreshControls();
    if (!lastData) return;
    const per = lastData;

    let lo = Infinity, hi = -Infinity;
    if (yMode === "shared") {
      Object.keys(per.channels).forEach(function (ch) {
        const b = per.channels[ch];
        for (let i = 0; i < b.samples.length; i++) {
          const v = b.samples[i] * per.scale;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      });
      const pad = (hi - lo) * 0.06 || 1e-3;
      lo -= pad; hi += pad;
    }

    let nCh = 0, nWaves = 0;
    cells.forEach(function (canvas, ch) {
      const b = per.channels[ch];
      if (!b || !b.nWaves) { drawEmpty(canvas, true); return; }
      nCh++; nWaves += b.nWaves;
      const waves = [];
      for (let k = 0; k < b.nWaves; k++) {
        waves.push(SBRPC.persistWave(b, k, per.scale));
      }
      let cLo = lo, cHi = hi;
      if (yMode !== "shared") {
        cLo = Infinity; cHi = -Infinity;
        waves.forEach(function (w) {
          for (let s = 0; s < w.length; s++) {
            if (w[s] < cLo) cLo = w[s];
            if (w[s] > cHi) cHi = w[s];
          }
        });
        const pad = (cHi - cLo) * 0.08 || 1e-3;
        cLo -= pad; cHi += pad;
      }
      drawCell(canvas, waves, cLo, cHi);
    });

    info = nWaves + " waveforms on " + nCh + " channels" +
      (yMode === "shared" && isFinite(lo)
        ? " · y " + lo.toFixed(4) + " to " + hi.toFixed(4) + " V"
        : " · each cell autoscaled");
    refreshControls();
  }

  function showError(err) {
    SDQM.setBanner(ROOT, String(err && err.message ? err.message : err) +
      "  — this page needs the analyzer (scripts/start-analyzer.sh)", "warn");
  }

  function refresh() {
    if (paused) return;
    SBRPC.call("sampic::persist", "all").then(function (per) {
      SDQM.setBanner(ROOT, "");
      lastData = per;
      redraw();
      return SBRPC.call("sampic::status");
    }).then(function (s) {
      if (!s) return;
      const chip = SDQM.statusChip(
        s.value.events_per_s > 0 ? { state: "live", text: "live" }
                                 : { state: "stale", text: "no events arriving" });
      elChip.replaceWith(chip); elChip = chip;
    }).catch(showError);
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicPersist", REFRESH_MS);
    const wanted = mhttpd_getParameterByName("y");
    if (wanted === "cell" || wanted === "shared") yMode = wanted;

    SDQM.loadGeometry().then(function (g) {
      if (!g) {
        SDQM.setBanner(ROOT, "No geometry in " + SDQM.DQM_SETTINGS +
          ". Start the frontend once.", "warn");
        return;
      }
      geom = g;
      // A missing template is normal, not an error: shape comparison is opt-in.
      return SBRPC.call("sampic::template")
        .then(function (t) { template = t; })
        .catch(function () { template = null; })
        .then(function () {
          build();
          refresh();
          timer = setInterval(refresh, REFRESH_MS);
        });
    }).catch(showError);
  });
})();
