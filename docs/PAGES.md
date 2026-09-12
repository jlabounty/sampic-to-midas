# The custom pages

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

## Two mhttpd rules that shaped the pages

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
