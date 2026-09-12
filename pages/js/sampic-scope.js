// SampicScope: live waveforms straight out of the event buffer.
//
// No backend. bm_receive_event pulls the most recent event from SYSTEM and the
// AD00 bank is decoded in the browser, so this page works against any
// experiment where the frontend is running -- nothing has to be analysing or
// recording.
//
// Two things about bm_receive_event worth knowing before changing this:
//
//   * get_recent:true asks for the LATEST event rather than the next one in
//     sequence. A scope should show now, not work through a backlog.
//   * a JSON reply instead of a binary one means "no event available" (status
//     209), which is the normal state between events and must not be reported
//     as an error.
//
// The frontend only produces physics events while a run is active (RO_RUNNING),
// so an idle page with the run stopped is expected, and the page says so rather
// than looking broken.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  const EVENT_ID = 1;               // identity.PHYSICS_EVENT_ID
  const MAX_TRACES = 12;

  let lastHeader = null;
  let plot = null;
  let plotCount = -1;
  let geom = null;
  let paused = false;
  let maxHits = 64;
  let lastEventInfo = "";
  let inFlight = false;

  function controls() {
    const bar = SDQM.el("div", "sdqm-controls");

    const pause = document.createElement("button");
    pause.textContent = paused ? "resume" : "pause";
    pause.onclick = function () { paused = !paused; draw(null); };
    bar.appendChild(pause);

    const lbl = SDQM.el("label");
    lbl.appendChild(SDQM.el("span", null, "max traces"));
    const sel = document.createElement("select");
    [4, 8, 12, 24, 64].forEach(function (n) {
      const o = document.createElement("option");
      o.value = String(n); o.textContent = String(n);
      sel.appendChild(o);
    });
    sel.value = String(maxHits);
    sel.onchange = function () { maxHits = parseInt(sel.value, 10); };
    lbl.appendChild(sel);
    bar.appendChild(lbl);

    bar.appendChild(SDQM.el("span", "sdqm-muted", lastEventInfo));
    return bar;
  }

  function ensureLayout() {
    const root = document.getElementById(ROOT);
    if (document.getElementById("sdqm-scope-plot")) {
      const old = root.querySelector(".sdqm-controls");
      if (old) old.replaceWith(controls());
      return;
    }
    root.innerHTML = "";
    root.appendChild(controls());
    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null, "Waveforms"));
    const holder = SDQM.el("div", "sdqm-plot");
    holder.id = "sdqm-scope-plot";
    card.appendChild(holder);
    card.appendChild(SDQM.el("div", "sdqm-muted",
      "Amplitudes are volts as stored in the AD00 bank; the x axis is samples " +
      "at the configured sampling rate."));
    root.appendChild(card);
    const tbl = SDQM.el("div", "sdqm-card");
    tbl.id = "sdqm-scope-hits";
    root.appendChild(tbl);
  }

  function label(hit) {
    if (!geom || hit.channel >= geom.channelPlane.length) return "ch " + hit.channel;
    const p = geom.channelPlane[hit.channel];
    if (p < 0) return "ch " + hit.channel;
    return geom.planeNames[p] + " s" + geom.channelStrip[hit.channel] +
           " (ch" + hit.channel + ")";
  }

  function hitTable(hits) {
    const host = document.getElementById("sdqm-scope-hits");
    if (!host) return;
    host.innerHTML = "";
    host.appendChild(SDQM.el("h3", null,
      "Hits in this event (" + hits.length + " shown of " +
      (hits.totalHits || hits.length) + ")"));
    // t0 is shown RELATIVE to the first hit of the event. The absolute value is
    // ~5e9 ns into the run, so every row would read "4.93e+09" and the
    // sub-nanosecond spread between planes -- the only interesting part, and
    // the thing a timing page is built on -- would be invisible. The absolute
    // event time is in the header line above the plot.
    const t0Ref = hits.length ? hits[0].t0 : 0;
    const t = document.createElement("table");
    t.className = "sdqm";
    const head = document.createElement("tr");
    ["channel", "plane / strip", "\u0394t0 (ns)", "amplitude (V)", "baseline (V)", "samples"]
      .forEach(function (h, i) {
        const th = SDQM.el("th", i < 2 ? "name" : null, h);
        head.appendChild(th);
      });
    t.appendChild(head);
    hits.slice(0, 40).forEach(function (h) {
      const tr = document.createElement("tr");
      const cells = [
        [String(h.channel), "name"],
        [label(h), "name"],
        [(h.t0 - t0Ref).toFixed(3), null],
        [SDQM.fmt(h.amplitude, 4), null],
        [SDQM.fmt(h.baseline, 4), null],
        [String(h.dataSize), null]
      ];
      cells.forEach(function (c) { tr.appendChild(SDQM.el("td", c[1], c[0])); });
      t.appendChild(tr);
    });
    host.appendChild(t);
  }

  // MPlotGraph's real API (midas/resources/mplot.js):
  //     new MPlotGraph(divElement, figureParams)   <- an ELEMENT, not an id
  //     graph.addPlot({label, xData, yData, line:{...}, marker:{...}})
  //     graph.setData(index, x, y, z)              <- redraws
  // There is no addPlot(x, y, label) and no draw(); getting this wrong draws
  // nothing and reports nothing.
  //
  // The graph is rebuilt whenever the trace count changes and only re-fed
  // otherwise. Rebuilding every event would also work at this refresh rate, but
  // it discards the user's zoom/pan on every tick, which makes the page useless
  // for actually looking at a pulse.
  function draw(hits) {
    ensureLayout();
    if (!hits) return;
    const host = document.getElementById("sdqm-scope-plot");
    if (!host) return;

    const shown = hits.slice(0, MAX_TRACES);
    const series = shown.map(function (h) {
      const xs = new Array(h.waveform.length);
      const ys = new Array(h.waveform.length);
      for (let s = 0; s < h.waveform.length; s++) { xs[s] = s; ys[s] = h.waveform[s]; }
      return { label: label(h), xData: xs, yData: ys };
    });

    if (!plot || plotCount !== series.length) {
      host.innerHTML = "";
      plot = new MPlotGraph(host, {
        title: { text: "" },
        legend: { show: true },
        stats: { show: false },
        xAxis: { title: { text: "sample" } },
        yAxis: { title: { text: "volts" } }
      });
      host.mpg = plot;
      series.forEach(function (sr) {
        plot.addPlot({
          label: sr.label, xData: sr.xData, yData: sr.yData,
          // A 64-sample trace with circular markers is unreadable; the line is
          // the signal.
          marker: { draw: false },
          line: { width: 1 }
        });
      });
      plotCount = series.length;
      if (typeof plot.resize === "function") plot.resize();
    } else {
      series.forEach(function (sr, i) {
        plot.param.plot[i].label = sr.label;
        plot.setData(i, sr.xData, sr.yData);
      });
    }
    hitTable(hits);
  }

  function poll() {
    if (paused || inFlight) return;
    inFlight = true;
    // event_id: -1, not EVENT_ID. mhttpd answers a specific event_id with
    // status 209 ("nothing available") even when matching events are in the
    // buffer -- verified against midas-2026-07-a -- so ask for anything and
    // filter below. The buffer also carries this frontend's DQM events, which
    // is exactly what the event_id check after bkToObj() is for.
    const req = {
      buffer_name: "SYSTEM",
      event_id: -1,
      trigger_mask: -1,
      get_recent: true
    };
    if (lastHeader) req.last_event_header = lastHeader;

    mjsonrpc_call("bm_receive_event", req, "arraybuffer").then(function (rpc) {
      inFlight = false;
      // A JSON reply means no event was available (status 209). Normal between
      // events and while the run is stopped; not an error.
      if (!(rpc instanceof ArrayBuffer)) { ensureLayout(); return; }
      const event = bkToObj(rpc);
      if (!event || event.event_id !== EVENT_ID) { ensureLayout(); return; }
      lastHeader = [event.event_id, event.trigger_mask,
                    event.serial_number, event.time_stamp];
      const ad = SAMPIC.bankBytes(SAMPIC.bankByName(event, "AD00"));
      if (!ad) { ensureLayout(); return; }
      const hits = SAMPIC.decodeAD(ad, maxHits);
      const at = SAMPIC.decodeAT(SAMPIC.bankBytes(SAMPIC.bankByName(event, "AT00")));
      lastEventInfo = "serial " + event.serial_number +
        " · " + (hits.totalHits || hits.length) + " hits" +
        (at ? " · event t0 " + SDQM.fmt(at.feTimestampNs / 1e6, 3) + " ms" : "");
      SDQM.setBanner(ROOT, "");
      draw(hits);
    }).catch(function (e) {
      inFlight = false;
      SDQM.setBanner(ROOT, "bm_receive_event failed: " + e, "error");
    });
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicScope", 1000);
    ensureLayout();
    SDQM.loadGeometry().then(function (g) { geom = g; });
    setInterval(poll, 500);
  });
})();
