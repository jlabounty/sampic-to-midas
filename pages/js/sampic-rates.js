// SampicRates: is the generator alive, what is it doing, and can it keep up.
//
// Pure ODB -- no event buffer, no backend. Everything comes from the FSST
// statistics bank the DQM equipment publishes every 2 s, plus the replay
// equipment's Common/Status and MIDAS's own Statistics.
//
// The FSST field order is fixed by fakesampic/frontend.py:DQM_STAT_NAMES and is
// also published as Settings/Names FSST, which is what this page reads -- so a
// field added there appears here without touching this file.

(function () {
  "use strict";

  const ROOT = "sdqm-root";
  let names = null;

  // Fields where a non-zero value means something is wrong, so they can be
  // coloured without hardcoding their positions.
  const BAD_IF_NONZERO = ["Skipped", "Dropped"];
  const WARN_IF_ZERO = ["Source OK"];

  function tile(key, value, cls) {
    const t = SDQM.el("div", "sdqm-tile" + (cls ? " " + cls : ""));
    t.appendChild(SDQM.el("div", "k", key));
    t.appendChild(SDQM.el("div", "v", value));
    return t;
  }

  function render(stats, statusRow, commonRow) {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";

    const status = SDQM.el("div", "sdqm-card");
    status.appendChild(SDQM.el("h3", null, "Generator"));
    const line = SDQM.el("div", null, statusRow || "(frontend not running)");
    line.style.fontSize = "14px";
    status.appendChild(line);
    if (commonRow) {
      const sub = SDQM.el("div", "sdqm-muted",
        "equipment " + SDQM.EQ_REPLAY + " · " + commonRow);
      status.appendChild(sub);
    }
    root.appendChild(status);

    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null, "Statistics"));
    const tiles = SDQM.el("div", "sdqm-tiles");
    for (let i = 0; i < stats.length; i++) {
      const name = (names && names[i]) ? names[i] : "field " + i;
      const v = stats[i];
      let cls = "";
      if (BAD_IF_NONZERO.indexOf(name) >= 0 && v > 0) cls = "bad";
      if (WARN_IF_ZERO.indexOf(name) >= 0 && v === 0) cls = "warn";
      let text;
      if (name === "Events per s" || name === "Hits per s") text = SDQM.fmt(v, 1);
      else if (name === "MB per s") text = SDQM.fmt(v, 3);
      else if (name === "Source OK") text = v ? "yes" : "NO";
      else if (v === Math.round(v)) text = String(Math.round(v));
      else text = SDQM.fmt(v, 2);
      tiles.appendChild(tile(name, text, cls));
    }
    card.appendChild(tiles);
    root.appendChild(card);

    const hint = SDQM.el("div", "sdqm-muted",
      "Rate, source and every other knob live in /Equipment/" + SDQM.EQ_REPLAY +
      "/Settings. Changes to the rate apply within one readout period; changes " +
      "that rebuild the source wait for the next run start unless " +
      "'Apply Cold Settings' is 'immediately'.");
    hint.style.marginTop = "10px";
    root.appendChild(hint);
  }

  function refresh() {
    const paths = [
      SDQM.DQM_VARS + "/FSST",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Common/Status",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Statistics/Events per sec.",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Statistics/kBytes per sec.",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Common/Period",
      SDQM.DQM_SETTINGS + "/Names FSST"
    ];
    SDQM.odbGet(paths).then(function (d) {
      if (d[0] === null || d[0] === undefined) {
        SDQM.setBanner(ROOT,
          "No " + SDQM.EQ_DQM + " data in the ODB. Start the frontend with " +
          "scripts/start-frontend.sh.", "warn");
        return;
      }
      names = SDQM.asArray(d[5]).map(String);
      const stats = SDQM.asArray(d[0]).map(Number);
      const common = "MIDAS reports " + SDQM.fmt(SDQM.num(d[2]), 1) + " ev/s, " +
                     SDQM.fmt(SDQM.num(d[3]), 1) + " kB/s · readout tick " +
                     SDQM.num(d[4]) + " ms";
      render(stats, d[1], common);
    }).catch(function (e) {
      SDQM.setBanner(ROOT, "ODB read failed: " + e, "error");
    });
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicRates", 2000);
    refresh();
    setInterval(refresh, 2000);
  });
})();
