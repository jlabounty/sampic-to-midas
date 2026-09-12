# The custom pages

> Never written one? [GETTING-STARTED.md](GETTING-STARTED.md) has a worked
> example from scratch. This is the reference behind it.

```bash
scripts/register-custom-pages.sh            # install
scripts/register-custom-pages.sh --list     # show what would be written
scripts/register-custom-pages.sh --remove   # take them out again
```

Three pages, none of which needs a backend — they read the ODB and the event
buffer directly, so they work whenever the frontend is running.

| page | reads | shows |
|---|---|---|
| **SampicScope** | `bm_receive_event` on SYSTEM | waveforms from the newest event, decoded in the browser, with a per-hit table |
| **SampicStrips** | `FakeSampicDQM` Variables + Settings | the detector as a picture: one row per plane, one cell per strip, coloured by rate or amplitude |
| **SampicRates** | `FSST` + equipment Statistics | generator rate, backlog, drops, readout duty, loops |
| **SampicGrid** | `bm_receive_event` + geometry | one event laid out as the detector: a waveform per plane/strip cell |
| **SampicHistos** | analyzer, over `brpc` | accumulated histograms, 1-D and 2-D — **needs the backend** |
| **SampicPersist** | analyzer, over `brpc` | the last N waveforms per channel, against the canonical pulse shape — **needs the backend** |

The last two need `scripts/start-analyzer.sh`; see [ANALYZER.md](ANALYZER.md).
Everything else works with just the frontend.

## SampicGrid and the y-range toggle

One row per plane, one column per strip. A cell carries that channel's waveform
if the event had a hit on it and is left blank if not, so the hit pattern is
visible as a shape rather than as a list of channel numbers — a track through
alternating X and Y planes shows up immediately as two vertical stripes.

The toggle is the substance of the page, and the two modes answer different
questions:

| `y range` | every cell uses | good for | misleading about |
|---|---|---|---|
| `shared across the event` | one range from the whole event | comparing amplitudes; seeing which strips actually collected charge | small pulses look almost flat |
| `per cell (autoscale)` | its own min/max | shape and timing on quiet channels | heights across cells mean nothing |

The amplitude is printed in each cell in mV precisely because per-cell mode
makes the trace height incomparable — in that mode the number is the only thing
you can compare between cells. The mode is also in the URL (`&y=cell`), so a
particular view can be bookmarked or left up on a shift screen.

Cells are plain 2D canvases, not `MPlotGraph` figures: an 8×10 detector is 80
cells, and 80 full figures with axes, legends and mouse handlers is far more
machinery than a 64-sample sparkline needs.

They are a starting point for a real DQM suite, and are deliberately written the
way the `wavedream-midas-dqm` pages are, so they can grow the same way.

## No geometry is hardcoded

Everything the pages know about the detector comes from
`/Equipment/FakeSampicDQM/Settings`, which the frontend rewrites at every start:
`Channel Plane`, `Channel Strip`, `Channel Position mm`, `Plane Names`,
`Plane Z mm`, `Plane N Strips`.

Change `Synthetic/N Planes` to 12, restart the run, and SampicStrips draws
twelve planes. That is the point of the exercise — the pages are being developed
against a detector that does not exist, so they must not assume its shape.

## Being a guest

Each page and asset gets its own `/Custom/<key>` entry holding an absolute path
into this checkout. `/Custom/Path` is never read or written, so these can be
installed into an experiment that already has custom pages without disturbing
them.

Keys are written **one at a time by full path**. Never `odb_set("/Custom", {...})`:
`odb_set` defaults to `remove_unspecified_keys=True`, so handing it a dict for
the whole subtree would delete every custom page belonging to anyone else. The
installer also refuses to overwrite a key pointing outside this checkout unless
you pass `--replace`, and `--remove` only deletes keys that point into it.

Use `--prefix` to install a second copy alongside an existing one.

## Writing a new page

Copy the closest existing page rather than starting blank: `sampic-grid.js` for
an event-driven page, `sampic-strips.js` for an ODB-driven one, or
`sampic-histos.js` for one backed by the analyzer. All of them follow the rules
below.

### The checklist

1. **Register it in `install/manifest.py`, nowhere else.** One `Entry` for the
   HTML (menu, so a dot-free key) and one per new asset (not in the menu, so it
   keeps its `.js` name). `--check` validates the naming rules for you.

2. **Build the DOM once; update only what changed.** Keep handles to the
   elements you will rewrite. A page that does `root.innerHTML = ""` on every
   refresh destroys text selection, focus and any open `<select>` under the
   user's pointer — every two seconds.

3. **Say whether the data is current.** Use `SDQM.freshness()` with
   `odbGetFull()`'s `lastWritten`, or `poller.ageSec()` for event pages, and put
   a `SDQM.statusChip()` somewhere visible. The ODB keeps serving a dead
   frontend's last value forever; a page that cannot say "this is old" is worse
   than no page, because the numbers look fine and someone acts on them.

4. **Age the DATA, not your page.** Measure from the event's own `time_stamp` or
   the ODB's `last_written`, never from when your page received it — a page
   opened onto a dead experiment receives a stale event immediately and would
   otherwise call it live. Repeated receipt of the *same* event is not liveness
   either; `SDQM.eventPoller` handles both.

5. **Read events through `SDQM.eventPoller`.** Do not write another
   `bm_receive_event` loop: the `event_id` workaround, the `inFlight` guard and
   the idle-vs-error distinction all live in one place so they cannot drift
   apart between pages.

6. **Take the geometry from the ODB.** `SDQM.loadGeometry()`. Never hardcode a
   plane or channel count — the detector this data describes is imaginary and
   changes with a setting.

7. **Normalise ODB arrays.** A one-element array comes back as a bare scalar;
   `SDQM.asArray()` exists for that, and it bites the common case, not an
   exotic one.

8. **Keep the three channel states distinct**: no DAQ channel, mapped but
   silent, and busy. Collapsing the first two is how a page hides a dead
   detector.

9. **Bump `?v=` on every asset you edited.** mhttpd stamps a 24 h `Expires`
   header on dotted `/Custom` keys, so an un-bumped edit simply does not appear.

10. **Render it before believing it.** HTTP 200 means the file was served, not
    that the page drew anything.

11. **If the page needs the analyzer, say so when it is missing.** A backend
    page must degrade to a clear "start the analyzer" message, not to an empty
    plot — and `SBRPC.call` already turns a non-binary reply into that error.

12. **Draw big 2-D data as an image** (`SH2D.draw`), not as one rectangle per
    bin. See below.

### What lives where

| | |
|---|---|
| `sampic-common.js` | ODB access (`odbGet`, `odbGetFull`), geometry, freshness, the event poller, formatting, colour ramp |
| `sampic-banks.js` | AD00/AT00 decoding: `hitsOf(event)`, `decodeAD`, `decodeAT`, `bankByName`, `bankBytes` |
| `sampic-brpc.js` | the binary protocol to the analyzer; mirrors `fakesampic/framing.py` |
| `sampic-h2d.js` | 2-D histograms as ImageData — see below |
| `sampic.css` | only what `midas.css` does not cover |
| your page | layout and drawing, and as little else as possible |

If you find yourself copying more than a few lines out of another page, it
belongs in `sampic-common.js` instead — a helper duplicated across two pages is
a helper that will diverge between them.

## Verifying a page actually renders

Serving HTTP 200 says nothing about whether a page drew anything. Headless
Chrome does, and WSL can use the Windows install:

```bash
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
"$CHROME" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=9000 \
    --dump-dom "http://localhost:8080/?cmd=custom&page=SampicScope"
# or --screenshot='C:\Users\<you>\AppData\Local\Temp\shot.png' --window-size=1400,1000
```

`--dump-dom` after a virtual-time budget shows the DOM the page built: a
`<canvas>` inside `sdqm-scope-plot` and a populated hit table mean the whole
chain worked. This is worth doing after any change to the plotting, because the
two APIs below are easy to get wrong in ways that fail silently.

**Keep `--virtual-time-budget` small when testing anything clock-based.**
Virtual time advances the page's `Date.now()` faster than the real clock, so a
9000 ms budget made a freshness chip report "stale, 9 s old" against data that
was 1.2 s old. Use ~2500 ms for those checks, or the measurement distorts the
thing being measured.

To test the stale path, kill the frontend and reload:

```bash
pkill -u $(id -u) -f '[f]akesampic\.frontend'
# every page should show an amber "stale" chip and dim its numbers,
# because the ODB happily keeps serving the last values written
```

## Draw a density map as an image, not as rectangles

`MPlotGraph`'s colormap fills one rectangle per bin and builds a CSS colour
string for each, so a 64×220 persistence map costs 14,080 string allocations and
14,080 `fillStyle` parses **per redraw** — and it redraws on every refresh and
every mouse move. Measured here: **7.18 ms per paint**, enough to make a page
visibly lag. `sampic-h2d.js` writes the density straight into an
`ImageData`, blits it once with smoothing off, and takes **0.49 ms — 14.8×
faster**. `MPlotGraph` is still the right tool for 1-D, where 200 points cost
nothing.

JSROOT would also do this well, and would be the right call if ROOT-style
fitting, projections and stats boxes were wanted. For drawing a density map it
is a megabyte-plus dependency that MIDAS does not ship, on machines that are
often offline.

Two mplot details worth knowing if you extend this: `redraw()` only schedules
`draw()` via `requestAnimationFrame` (so timing `redraw()` in a loop measures
nothing), and a colormap takes `xMin/xMax/yMin/yMax` on the **plot** parameters
— given only the figure's axis range it silently plots in bin indices.

## Two APIs that fail silently when used wrongly

**`MPlotGraph` takes an ELEMENT and a params object**, not an id string, and its
methods are `addPlot({label, xData, yData, line, marker})` and
`setData(index, x, y, z)`. There is no `addPlot(x, y, label)` and no `draw()`.
Calling the wrong thing throws inside a promise handler and the page simply
stays empty. See `midas/resources/mplot.html` for a worked example and
`defaultGraphParam` / `defaultPlotParam` in `mplot.js` for every parameter name.

**`bkToObj()` returns `event.bank`** — an array of
`{name, type, size, data, hexdata, array}` — **not `event.banks`**, and not keyed
by name. `hexdata` is a `Uint8Array` over the payload whatever the TID, which is
what `SAMPIC.bankBytes` prefers. Reading the wrong property finds no AD00 and
draws nothing, with no error anywhere. `tests/js/decode.test.js` pins both the
property name and the decode-through-`bankByName` path.

## Two mhttpd rules to respect

**A menu key must not contain a dot.** A dot-less key is served by
`show_custom_page()` with no cache headers; a dotted one goes through
`send_fp()`, which stamps `Expires: +24h`. So the pages are `SampicScope`, and
the assets keep their `.js`/`.css` names — and every `<script>` and `<link>` in
the HTML carries a `?v=` query string. **Bump it when you edit an asset**, or
your change will not appear for a day.

**`bm_receive_event` with a specific `event_id` returns 209.** Asking for
`event_id: 1` reports "nothing available" even when matching events are in the
buffer (verified against midas-2026-07-a), so SampicScope asks for `-1` and
filters on `event.event_id` after decoding. The buffer also carries the DQM
equipment's events, which is what that check is really for.

## The bank decoder

`pages/js/sampic-banks.js` decodes AD00 and AT00 in the browser. The byte
offsets mirror `EventBankUnpacker.hh` and `converter/sampic_banks.py`; they are
checked against the authoritative numpy dtype, on a real captured event, by:

```bash
$FS_PYTHON tests/js/decode.test.js      # needs node
```

The fixture in `tests/js/event-fixture.json` is a genuine event taken from a
live run's SYSTEM buffer, with every expected value computed by the numpy dtype
rather than by a second hand-written copy of the same offsets.

One thing to remember when extending it: the waveform in AD00 is **volts**.
`build_ad_records` already divided the int16 samples by 1e4. Dividing again is
the obvious bug.
