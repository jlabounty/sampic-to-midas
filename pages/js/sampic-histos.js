// SampicHistos: browse what the analyzer has accumulated.
//
// Unlike every other page here this one NEEDS the backend
// (scripts/start-analyzer.sh): histograms are accumulated across events, which
// nothing in the browser can do for a stream it only samples twice a second.
//
// 1-D histograms are drawn with MPlotGraph as a step plot; 2-D as its colormap.
// Both come over brpc as binary (see sampic-brpc.js) because the persistence
// histogram alone is 64x220 float32.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  const REFRESH_MS = 3000;

  let names = [];
  let current = null;
  let plot = null;
  let plotKind = null;         // "1d" | "2d", so the figure is rebuilt on change
  let h2canvas = null, h2last = null, h2geom = null;
  let logZ = false;
  let built = false;
  let timer = null;
  let elSelect = null, elChip = null, elInfo = null, elStats = null;

  function buildOnce() {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";

    const bar = SDQM.el("div", "sdqm-controls");
    const lbl = SDQM.el("label");
    lbl.appendChild(SDQM.el("span", null, "histogram"));
    elSelect = document.createElement("select");
    elSelect.onchange = function () { current = elSelect.value; refresh(); };
    lbl.appendChild(elSelect);
    bar.appendChild(lbl);

    const zl = SDQM.el("label");
    const zcb = document.createElement("input");
    zcb.type = "checkbox";
    zcb.checked = logZ;
    zcb.onchange = function () { logZ = zcb.checked; refresh(); };
    zl.appendChild(zcb);
    zl.appendChild(SDQM.el("span", null, "log colour scale (2-D)"));
    bar.appendChild(zl);

    const clear = document.createElement("button");
    clear.textContent = "clear";
    clear.title = "zero every histogram in the analyzer";
    clear.onclick = function () {
      if (!confirm("Clear every accumulated histogram in the analyzer?")) return;
      SBRPC.call("sampic::clear").then(refresh).catch(showError);
    };
    bar.appendChild(clear);

    elChip = SDQM.statusChip({ state: "never", text: "no data yet" });
    bar.appendChild(elChip);
    elInfo = SDQM.el("span", "sdqm-muted", "");
    bar.appendChild(elInfo);
    root.appendChild(bar);

    const card = SDQM.el("div", "sdqm-card");
    const holder = SDQM.el("div", "sdqm-plot");
    holder.id = "sdqm-histo-plot";
    card.appendChild(holder);
    elStats = SDQM.el("div", "sdqm-muted", "");
    card.appendChild(elStats);
    root.appendChild(card);
    built = true;
  }

  function showError(err) {
    SDQM.setBanner(ROOT, String(err && err.message ? err.message : err) +
      "  — start it with scripts/start-analyzer.sh", "warn");
    if (elChip) {
      const chip = SDQM.statusChip({ state: "never", text: "analyzer not answering" });
      elChip.replaceWith(chip); elChip = chip;
    }
  }

  function drawHist1(h) {
    const host = document.getElementById("sdqm-histo-plot");
    if (plotKind !== "1d") { host.innerHTML = ""; plot = null; h2canvas = null; }
    const xs = new Array(h.nbins), ys = new Array(h.nbins);
    for (let i = 0; i < h.nbins; i++) {
      xs[i] = SBRPC.binCentre(h, i);
      ys[i] = h.counts[i];
    }
    if (!plot) {
      host.style.height = "";          // back to the stylesheet's height
      plot = new MPlotGraph(host, {
        title: { text: h.title || h.name },
        legend: { show: false }, stats: { show: false },
        xAxis: { title: { text: h.xlabel || "" } },
        yAxis: { title: { text: "counts" } }
      });
      host.mpg = plot;
      plot.addPlot({ label: h.name, xData: xs, yData: ys,
                     marker: { draw: false }, line: { width: 1.5 } });
      plotKind = "1d";
      if (typeof plot.resize === "function") plot.resize();
    } else {
      plot.param.title.text = h.title || h.name;
      plot.param.xAxis.title.text = h.xlabel || "";
      plot.setData(0, xs, ys);
    }
    const total = h.counts.reduce(function (a, b) { return a + b; }, 0);
    elStats.textContent =
      h.entries + " entries · " + total.toFixed(0) + " in range · " +
      h.underflow + " underflow · " + h.overflow + " overflow" +
      (h.overflow > 0.02 * Math.max(h.entries, 1)
        ? "  — a lot is off the top of the range" : "");
  }

  function drawHist2(h) {
    const host = document.getElementById("sdqm-histo-plot");
    // Leaving an MPlotGraph in place would keep its mouse handlers alive over
    // a canvas it no longer owns.
    if (plotKind !== "2d") { host.innerHTML = ""; plot = null; h2canvas = null; }
    if (!h2canvas) {
      h2canvas = document.createElement("canvas");
      // Fill the container rather than setting an independent height: a canvas
      // taller than .sdqm-plot overflows it, and the stats line below then
      // paints over the axis labels.
      h2canvas.style.width = "100%";
      h2canvas.style.height = "100%";
      h2canvas.style.display = "block";
      host.appendChild(h2canvas);
      h2canvas.addEventListener("mousemove", function (ev) {
        if (!h2last || !h2geom) return;
        const b = SH2D.binAt(h2last, h2geom, ev.offsetX, ev.offsetY);
        h2canvas.title = b
          ? (h2last.xlabel || "x") + " " + b.x.toPrecision(4) + "\n" +
            (h2last.ylabel || "y") + " " + b.y.toPrecision(4) + "\n" +
            b.count + " counts"
          : "";
      });
      plotKind = "2d";
    }
    host.style.height = "440px";
    h2last = h;
    h2geom = SH2D.draw(h2canvas, h, { logZ: logZ });
    elStats.textContent = h.entries + " entries \u00b7 " + h.nx + "\u00d7" + h.ny +
      " bins \u00b7 busiest cell " + h2geom.zmax.toFixed(0) +
      " \u00b7 drawn in " + h2geom.ms.toFixed(1) + " ms";
  }

  function refreshNames() {
    return SBRPC.call("sampic::list").then(function (r) {
      const changed = r.names.join() !== names.join();
      names = r.names;
      if (changed) {
        // With per-strip histograms this list is a few hundred entries, so
        // group it. The names are hierarchical ("amp/strip/P0_s03"), and the
        // group is everything before the last slash.
        elSelect.innerHTML = "";
        const groups = new Map();
        names.forEach(function (n) {
          const cut = n.lastIndexOf("/");
          const g = cut < 0 ? "" : n.slice(0, cut);
          if (!groups.has(g)) groups.set(g, []);
          groups.get(g).push(n);
        });
        groups.forEach(function (members, g) {
          const parent = g ? document.createElement("optgroup") : elSelect;
          if (g) { parent.label = g + "  (" + members.length + ")"; }
          members.forEach(function (n) {
            const o = document.createElement("option");
            o.value = n;
            // Inside a group the prefix is redundant and makes the list hard
            // to scan.
            o.textContent = g ? n.slice(g.length + 1) : n;
            parent.appendChild(o);
          });
          if (g) elSelect.appendChild(parent);
        });
      }
      if (!current || names.indexOf(current) < 0) current = names[0] || null;
      elSelect.value = current || "";
      return current;
    });
  }

  function refresh() {
    if (!built) buildOnce();
    const p = names.length ? Promise.resolve(current) : refreshNames();
    p.then(function () {
      if (!current) { showError(new Error("the analyzer has no histograms")); return; }
      return SBRPC.call("sampic::hist", current).then(function (h) {
        SDQM.setBanner(ROOT, "");
        if (h.tag === "hst1") drawHist1(h); else drawHist2(h);
        return SBRPC.call("sampic::status");
      }).then(function (s) {
        if (!s) return;
        const v = s.value;
        elInfo.textContent = v.events + " events analysed · " +
          v.events_per_s + " ev/s" +
          (v.throttled ? " · " + v.throttled + " skipped by the rate limit" : "");
        const chip = SDQM.statusChip(
          v.events_per_s > 0 ? { state: "live", text: "live" }
                             : { state: "stale", text: "no events arriving" });
        elChip.replaceWith(chip); elChip = chip;
      });
    }).catch(showError);
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicHistos", REFRESH_MS);
    buildOnce();
    const wanted = mhttpd_getParameterByName("h");
    if (wanted) current = wanted;
    refreshNames().then(refresh).catch(showError);
    timer = setInterval(refresh, REFRESH_MS);
  });
})();
