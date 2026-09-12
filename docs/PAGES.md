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
