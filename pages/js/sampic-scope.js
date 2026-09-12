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
    const t = document.createElement("table");
    t.className = "sdqm";
    const head = document.createElement("tr");
    ["channel", "plane / strip", "t0 (ns)", "amplitude (V)", "baseline (V)", "samples"]
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
        [SDQM.fmt(h.t0, 3), null],
        [SDQM.fmt(h.amplitude, 4), null],
        [SDQM.fmt(h.baseline, 4), null],
        [String(h.dataSize), null]
      ];
      cells.forEach(function (c) { tr.appendChild(SDQM.el("td", c[1], c[0])); });
      t.appendChild(tr);
    });
    host.appendChild(t);
  }

  function draw(hits) {
    ensureLayout();
    if (!hits) return;
    const shown = hits.slice(0, MAX_TRACES);
    if (!plot) {
      plot = new MPlotGraph("sdqm-scope-plot");
      plot.showZeroSuppression = false;
      plot.xLabel = "sample";
      plot.yLabel = "volts";
    }
    plot.deletePlot(-1);
    shown.forEach(function (h, i) {
      const xs = [], ys = [];
      for (let s = 0; s < h.waveform.length; s++) { xs.push(s); ys.push(h.waveform[s]); }
      plot.addPlot(xs, ys, label(h));
    });
    plot.draw();
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
