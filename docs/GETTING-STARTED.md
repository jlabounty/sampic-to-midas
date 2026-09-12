# Getting started

From a machine with nothing installed to a custom page you wrote yourself.
Allow about half an hour, most of it waiting for MIDAS to compile.

**What you end up with:** MIDAS built in your own directory, an experiment
running with a fake SAMPIC detector producing events at a rate you choose, six
DQM pages in the browser, and a seventh that you wrote.

Nothing here touches a system directory, `/etc/exptab`, or any MIDAS experiment
already on the machine.

---

## 1. Get the code

```bash
mkdir -p ~/github/pioneer/fake_sampic && cd ~/github/pioneer/fake_sampic
git clone <this repo> sampic-to-midas
cd sampic-to-midas
```

The layout that results — MIDAS, the experiment and this repo side by side under
one workspace — is what every script assumes:

```
fake_sampic/
├── midas/            MIDAS source + build + install   (created in step 3)
├── online/           the experiment: exptab, ODB, history, run files
└── sampic-to-midas/  this repository
```

## 2. Python environment

```bash
scripts/setup-conda.sh
```

Creates a conda environment called `fake-sampic` with python 3.14, numpy and
pytest. A dedicated environment, not your `base`: installing into `base` affects
every other project on the machine.

## 3. Build MIDAS

```bash
sudo apt install zlib1g-dev      # the one thing that needs root
scripts/setup-midas.sh           # clones and builds tag midas-2026-07-a
scripts/setup-conda.sh           # again, to install the python bindings
```

Takes a few minutes. Without root, skip the `apt` line — `setup-midas.sh` falls
back to the conda environment's zlib. ROOT is **not** required.

[INSTALL.md](INSTALL.md) explains the three things about this build that fail in
ways that do not point at their cause. You do not need to read it unless
something goes wrong.

## 4. Start everything

```bash
source scripts/fake-sampic-env.sh     # MIDASSYS, MIDAS_EXPTAB, $FS_PYTHON
scripts/start-midas.sh                # mhttpd + mlogger
scripts/start-frontend.sh --daemon    # the fake detector
scripts/start-analyzer.sh --daemon    # histograms (optional)
scripts/register-custom-pages.sh      # the DQM pages, once
odbedit -e fakesampic -c "start now"  # or press Start in mhttpd
```

Open <http://localhost:8080>. `/Equipment/FakeSampic/Statistics/Events per sec.`
should be counting, and six `Sampic…` entries should be in the side menu.

Run files are **off** by default — the data is fabricated, and recording it
costs over a MB/s for nothing. `scripts/logging.sh on --restart-run` when you
actually want one.

## 5. Look at what you have

| page | shows |
|---|---|
| **SampicScope** | waveforms from the newest event |
| **SampicGrid** | that event laid out as the detector, one cell per strip |
| **SampicStrips** | occupancy across the plane stack |
| **SampicRates** | generator rate, backlog, drops |
| **SampicHistos** | accumulated histograms *(needs the analyzer)* |
| **SampicPersist** | last N waveforms per channel vs the canonical pulse shape *(needs the analyzer)* |

Then change something and watch it follow:

```bash
S=/Equipment/FakeSampic/Settings
odbedit -e fakesampic -c "set \"$S/Rate Hz\" 2000"          # applies live
odbedit -e fakesampic -c "set \"$S/Synthetic/Beam Sigma mm\" 0.4"
odbedit -e fakesampic -c "set \"$S/Synthetic/N Planes\" 12"  # needs a run restart
odbedit -e fakesampic -c "stop now" && odbedit -e fakesampic -c "start now"
```

SampicStrips and SampicGrid now draw twelve planes, because the pages take the
geometry from the ODB rather than assuming it. Every knob is in
[ODB.md](ODB.md); the ones you will actually use are in [RUNNING.md](RUNNING.md).

You can also replay the real SAMPIC file in `data/` instead of generating:

```bash
odbedit -e fakesampic -c "set \"$S/Source\" binfile"
odbedit -e fakesampic -c "stop now" && odbedit -e fakesampic -c "start now"
```

## 6. Write a page

A custom page is one HTML file and one JS file. This one shows how many hits
each plane had in the newest event — about forty lines, and it exercises
everything a real page needs: the shared event poller, the geometry from the
ODB, build-the-DOM-once, and a freshness indicator.

**`pages/sampic-first.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>My First Page</title>
  <link rel="stylesheet" href="midas.css">
  <script src="controls.js"></script>
  <script src="midas.js"></script>
  <script src="mhttpd.js"></script>
  <link rel="stylesheet" href="sampic.css?v=3">
  <script src="sampic-common.js?v=4"></script>
  <script src="sampic-banks.js?v=3"></script>
  <script src="sampic-first.js?v=1"></script>
</head>
<body class="mcss">
  <div id="mheader"></div><div id="msidenav"></div>
  <div id="mmain"><div id="sdqm-root"></div></div>
</body>
</html>
```

**`pages/js/sampic-first.js`**

```js
(function () {
  "use strict";
  const ROOT = "sdqm-root";
  let geom = null, poller = null, rows = null, chipHolder = null;

  function build() {                       // once, not on every refresh
    const root = document.getElementById(ROOT);
    root.innerHTML = "";
    chipHolder = SDQM.el("div", "sdqm-controls");
    root.appendChild(chipHolder);

    const card = SDQM.el("div", "sdqm-card");
    card.appendChild(SDQM.el("h3", null, "Hits per plane, newest event"));
    const table = document.createElement("table");
    table.className = "sdqm";
    rows = [];
    geom.planeNames.forEach(function (name, p) {
      const tr = document.createElement("tr");
      tr.appendChild(SDQM.el("td", "name", name));
      const n = SDQM.el("td", null, "-");
      tr.appendChild(n);
      table.appendChild(tr);
      rows.push(n);
    });
    card.appendChild(table);
    root.appendChild(card);
  }

  function show(hits) {
    const perPlane = new Array(geom.nPlanes).fill(0);
    hits.forEach(function (h) {
      const p = geom.channelPlane[h.channel];
      if (p >= 0) perPlane[p]++;
    });
    rows.forEach(function (cell, p) { cell.textContent = perPlane[p]; });

    const age = poller.ageSec();             // say whether this is current
    chipHolder.innerHTML = "";
    chipHolder.appendChild(SDQM.statusChip(
      age !== null && age < 5 ? { state: "live", text: "live" }
                              : { state: "stale", text: "stale" }));
  }

  window.addEventListener("load", function () {
    mhttpd_init(mhttpd_getParameterByName("page") || "SampicFirst", 1000);
    SDQM.loadGeometry().then(function (g) {
      if (!g) { SDQM.setBanner(ROOT, "Start the frontend first.", "warn"); return; }
      geom = g;
      build();
      poller = SDQM.eventPoller({
        eventId: 1,
        onEvent: function (event) { show(SAMPIC.hitsOf(event)); }
      });
      poller.start();
      setInterval(function () { if (rows) show([]); }, 2000);  // keep the chip honest
    });
  });
})();
```

Register it by adding two entries to `install/manifest.py`:

```python
Entry("SampicFirst", "sampic-first.html", True, "hits per plane"),
Entry("sampic-first.js", "js/sampic-first.js", False, "my first page"),
```

then

```bash
scripts/register-custom-pages.sh
```

and reload mhttpd. `SampicFirst` is in the side menu.

### What that example is demonstrating

* **`SDQM.eventPoller`** — never write your own `bm_receive_event` loop. The
  `event_id` workaround, the in-flight guard and the idle-versus-error
  distinction live in one place.
* **`SDQM.loadGeometry()`** — never hardcode a plane or channel count. The
  detector is imaginary and changes with a setting.
* **Build the DOM once.** Rebuilding on every refresh throws away text
  selection, focus, and any open dropdown under the user's pointer.
* **Say whether the data is current.** The ODB happily serves a dead frontend's
  last values forever; a page that cannot say "this is old" is worse than no
  page, because the numbers look fine and someone acts on them.

[PAGES.md](PAGES.md) has the full checklist, what belongs in which shared
module, how to verify a page actually rendered, and the MIDAS APIs that fail
silently when used wrongly. Read it before your second page.

## 7. Run the tests

```bash
$FS_PYTHON -m pytest tests/ -q        # no MIDAS needed
$FS_PYTHON tests/js/decode.test.js    # needs node; optional
```

## Where to go next

| you want to | read |
|---|---|
| understand a setting | [ODB.md](ODB.md) |
| operate the experiment, or verify the chain end to end | [RUNNING.md](RUNNING.md) |
| write more pages | [PAGES.md](PAGES.md) |
| add a histogram, or run the analyzer on another machine | [ANALYZER.md](ANALYZER.md) |
| fix a build problem | [INSTALL.md](INSTALL.md) |

## If something is wrong

**No pages in the side menu.** `scripts/register-custom-pages.sh`.

**A page is there but empty.** Is the frontend running, and is a run started?
The physics equipment is `RO_RUNNING`. Check
`/Equipment/FakeSampic/Common/Status`.

**An edited page does not change.** mhttpd caches `/Custom` assets for 24 hours.
Bump the `?v=` in the page's HTML.

**SampicHistos or SampicPersist say the analyzer is not answering.**
`scripts/start-analyzer.sh --daemon`.

**Starting over.** `scripts/stop-midas.sh --clean` drops the ODB and its shared
memory; then start again from step 4.
