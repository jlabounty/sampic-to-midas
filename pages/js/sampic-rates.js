// SampicRates: is the generator alive, what is it doing, and can it keep up.
//
// Pure ODB -- no event buffer, no backend. Everything comes from the FSST
// statistics bank the DQM equipment publishes every 2 s, plus the replay
// equipment's Common/Status and MIDAS's own Statistics.
//
// The FSST field order is fixed by fakesampic/frontend.py:DQM_STAT_NAMES and is
// also published as Settings/Names FSST, which is what this page reads -- so a
// field added there appears here without touching this file.
//
// The tiles are BUILT ONCE and only their values updated. Rebuilding them every
// two seconds would throw away text selection and any focus the user had.

(function () {
  "use strict";

  const ROOT = "sdqm-root";

  // Fields where a non-zero value means something is wrong, and where a zero
  // does. Named rather than positional so the FSST layout can grow.
  const BAD_IF_NONZERO = ["Skipped", "Dropped"];
  const WARN_IF_ZERO = ["Source OK"];

  let built = false;
  let names = null;
  const tiles = new Map();          // field name -> value element
  let elChip = null, elStatus = null, elCommon = null, elTiles = null;

  function buildOnce(fieldNames) {
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    tiles.clear();

    const card = SDQM.el("div", "sdqm-card");
    const head = SDQM.el("h3", null, "Generator ");
    elChip = SDQM.statusChip({ state: "never", text: "no data yet" });
    head.appendChild(elChip);
    card.appendChild(head);
    elStatus = SDQM.el("div", null, "");
    elStatus.style.fontSize = "14px";
    card.appendChild(elStatus);
    elCommon = SDQM.el("div", "sdqm-muted", "");
    card.appendChild(elCommon);
    root.appendChild(card);

    const stats = SDQM.el("div", "sdqm-card");
    stats.appendChild(SDQM.el("h3", null, "Statistics"));
    elTiles = SDQM.el("div", "sdqm-tiles");
    fieldNames.forEach(function (name) {
      const tile = SDQM.el("div", "sdqm-tile");
      tile.appendChild(SDQM.el("div", "k", name));
      const v = SDQM.el("div", "v", "-");
      tile.appendChild(v);
      elTiles.appendChild(tile);
      tiles.set(name, { tile: tile, value: v });
    });
    stats.appendChild(elTiles);
    root.appendChild(stats);

    root.appendChild(SDQM.el("div", "sdqm-muted",
      "Rate, source and every other knob live in /Equipment/" + SDQM.EQ_REPLAY +
      "/Settings. Changes to the rate apply within one readout period; changes " +
      "that rebuild the source wait for the next run start unless " +
      "'Apply Cold Settings' is 'immediately'."));
    built = true;
  }

  function format(name, v) {
    if (name === "Events per s" || name === "Hits per s") return SDQM.fmt(v, 1);
    if (name === "MB per s") return SDQM.fmt(v, 3);
    if (name === "Source OK") return v ? "yes" : "NO";
    if (v === Math.round(v)) return String(Math.round(v));
    return SDQM.fmt(v, 2);
  }

  function update(stats, statusText, commonText, fresh) {
    names.forEach(function (name, i) {
      const t = tiles.get(name);
      if (!t) return;
      const v = stats[i];
      t.value.textContent = format(name, v);
      let cls = "sdqm-tile";
      if (BAD_IF_NONZERO.indexOf(name) >= 0 && v > 0) cls += " bad";
      if (WARN_IF_ZERO.indexOf(name) >= 0 && v === 0) cls += " warn";
      t.tile.className = cls;
    });
    elStatus.textContent = statusText || "(frontend not running)";
    elCommon.textContent = commonText || "";
    // Old numbers are dimmed rather than hidden: the last known state is still
    // useful, but it must not read as the current one.
    elTiles.classList.toggle("sdqm-stale-data", fresh.state !== "live");
    const chip = SDQM.statusChip(fresh);
    elChip.replaceWith(chip);
    elChip = chip;
  }

  function refresh() {
    const paths = [
      SDQM.DQM_VARS + "/FSST",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Common/Status",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Statistics/Events per sec.",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Statistics/kBytes per sec.",
      "/Equipment/" + SDQM.EQ_REPLAY + "/Common/Period",
      SDQM.DQM_SETTINGS + "/Names FSST",
      "/Equipment/" + SDQM.EQ_DQM + "/Common/Period"
    ];
    SDQM.odbGetFull(paths).then(function (r) {
      const d = r.data;
      if (d[0] === null || d[0] === undefined) {
        SDQM.setBanner(ROOT,
          "No " + SDQM.EQ_DQM + " data in the ODB. Start the frontend with " +
          "scripts/start-frontend.sh.", "warn");
        return;
      }
      SDQM.setBanner(ROOT, "");
      const fieldNames = SDQM.asArray(d[5]).map(String);
      if (!built || (names && names.join() !== fieldNames.join())) {
        names = fieldNames;
        buildOnce(fieldNames);
      }
      names = fieldNames;

      const dqmPeriod = SDQM.num(d[6], 2000) / 1000;
      const common = "MIDAS reports " + SDQM.fmt(SDQM.num(d[2]), 1) + " ev/s, " +
                     SDQM.fmt(SDQM.num(d[3]), 1) + " kB/s · readout tick " +
                     SDQM.num(d[4]) + " ms";
      update(SDQM.asArray(d[0]).map(Number), d[1], common,
             SDQM.freshness(r.lastWritten[0], Math.max(dqmPeriod * 2.5, 3)));
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
