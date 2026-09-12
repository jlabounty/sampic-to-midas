# Running the fake SAMPIC experiment

> First time? [GETTING-STARTED.md](GETTING-STARTED.md) sets everything up.

Install first: [INSTALL.md](INSTALL.md).

```bash
cd sampic-to-midas
source scripts/fake-sampic-env.sh     # MIDASSYS, MIDAS_EXPTAB, $FS_PYTHON, ...

scripts/start-midas.sh                # mhttpd + mlogger  -> http://localhost:8080
scripts/start-frontend.sh --daemon    # the event source
scripts/register-custom-pages.sh      # the DQM pages (once)

odbedit -e fakesampic -c "start now"  # or press Start in mhttpd
```

Stop with `scripts/stop-midas.sh` (add `--clean` to drop the ODB too).

The frontend produces physics events only while a run is active (`RO_RUNNING`),
which is deliberate — it is what a real frontend does, and it keeps the Start
button meaningful. The DQM equipment is `RO_ALWAYS`, so per-channel rates and
the strip map stay live between runs.

## Run files are off by default

```bash
scripts/logging.sh                    # status
scripts/logging.sh on  --restart-run  # record
scripts/logging.sh off --restart-run  # stop recording
```

This experiment exists to feed a live stream to DQM pages, and the data is
fabricated — recording it has no value while costing over a MB/s. Events still
reach the SYSTEM buffer with writing off, so every page, analyser and
`bm_receive_event` consumer works exactly the same.

**A change only takes effect at the next begin-of-run.** mlogger decides what to
open when the run starts, so flipping the switch mid-run has no effect at all
until the run is cycled — no file appears however long you wait, and one appears
immediately after a stop/start. `--restart-run` does the cycle for you, and
`scripts/logging.sh` warns when a run is active.

## The knobs

All under `/Equipment/FakeSampic/Settings`; the full reference is
[ODB.md](ODB.md). The ones you will actually reach for:

| setting | does |
|---|---|
| `Source` | `binfile` (replay), `synthetic` (generate), `mixer` (overlay) |
| `Rate Hz` | events per second. Applies within one readout tick, live |
| `Rate Model` | `fixed`, `poisson`, `burst` |
| `BinFile/Timing` | `file` uses the replayed run's own gaps; `rate` uses `Rate Hz` |
| `BinFile/Files` | what to replay; globs allowed |
| `BinFile/Loop` | replay forever |
| `Synthetic/Model` | `track` (correlated clusters) or `parametric` (independent) |
| `Synthetic/N Planes` | how many LGAD layers to invent |

Hot settings apply on the next readout tick. Cold ones rebuild the event source
and wait for the next begin-of-run, because applying them mid-run would silently
change what a run file contains halfway through. Every deferred change is
announced in the MIDAS message log, and the equipment status goes yellow with
`[pending: ...]`. Set `Apply Cold Settings` to `immediately` to override.

`Common/Period` is the readout **tick**, not the rate — MIDAS's own knob, not
duplicated in Settings. One tick emits as many events as the schedule says are
due, which is how a ~50 Hz tick reaches kHz rates.

### Examples

```bash
S=/Equipment/FakeSampic/Settings
odbedit -e fakesampic -c "set \"$S/Rate Hz\" 2000"            # live, no restart

# Replay run914 at its own ~700 Hz, forever
odbedit -e fakesampic -c "set \"$S/Source\" binfile"
odbedit -e fakesampic -c "set \"$S/BinFile/Timing\" file"

# Invent a 12-plane detector instead of 8
odbedit -e fakesampic -c "set \"$S/Source\" synthetic"
odbedit -e fakesampic -c "set \"$S/Synthetic/N Planes\" 12"
odbedit -e fakesampic -c "stop now" && odbedit -e fakesampic -c "start now"

# Real hits with synthetic ones piled on top
odbedit -e fakesampic -c "set \"$S/Source\" mixer"
odbedit -e fakesampic -c "set \"$S/Mixer/Mode\" overlay"
```

## Verifying the chain

The strongest check this project has: an event replayed through MIDAS must be
byte-identical to the same event converted offline.

```bash
source scripts/fake-sampic-env.sh
S=/Equipment/FakeSampic/Settings

scripts/logging.sh on
odbedit -e fakesampic -c "set \"$S/Source\" binfile"
odbedit -e fakesampic -c "set \"$S/Restamp Timestamps\" n"   # passthrough
odbedit -e fakesampic -c "set \"$S/BinFile/Loop\" n"
odbedit -e fakesampic -c "set \"$S/BinFile/Timing\" rate"
odbedit -e fakesampic -c "set \"$S/Rate Hz\" 30000"
odbedit -e fakesampic -c "set \"$S/Max Events Per Call\" 4000"
odbedit -e fakesampic -c "set \"$S/Max Readout ms\" 200"

odbedit -e fakesampic -c "start now"
# wait for Statistics/Events sent to reach 42258, then:
odbedit -e fakesampic -c "stop now"

RUN=$(ls -t $FS_DATA_DIR/*.mid | head -1)
$FS_PYTHON -m converter.bin_to_mid "$FS_DEFAULT_BIN" -o /tmp/ref.mid --gap-ns 100
$FS_PYTHON tools/compare_mid.py /tmp/ref.mid "$RUN" --event-id 1
$FS_PYTHON tools/validate_midas.py "$FS_DEFAULT_BIN" "$RUN" --gap-ns 100 --event-id 1
```

Expect `BANK STREAMS IDENTICAL` and `ALL CHECKS PASSED` — 42258 events, 84516
banks, bitwise field equality on every hit.

`--event-id 1` matters: a real run file also carries the DQM equipment's events
(ID 200), which have no AD00 bank. `Restamp Timestamps = n` matters because the
validator compares timestamps against the source file bit for bit, which can
only hold when they are passed through rather than moved onto the live clock.
Remember `scripts/logging.sh off --restart-run` afterwards.

There is also an offline version needing no MIDAS at all:

```bash
$FS_PYTHON -m fakesampic.offline --source binfile --files "$FS_DEFAULT_BIN" \
    --no-restamp --no-loop --gap-ns 100 --events 42258 -o /tmp/fe.mid
$FS_PYTHON tools/compare_mid.py /tmp/ref.mid /tmp/fe.mid
```

## Tests

```bash
$FS_PYTHON -m pytest tests/ -q          # 109 tests, no MIDAS needed
$FS_PYTHON tests/js/decode.test.js      # the browser decoder; needs node
```

## Troubleshooting

**The frontend will not start: "already running".**
`pgrep -f` matches its own shell. `pgrep -u $(id -u) -f '[f]akesampic\.frontend'`
gives the real answer.

**No history for the DQM plots.**
mlogger reads the equipment list when *it* starts and does not notice one that
appeared later. Restart mlogger after the frontend's first ever run. The
frontend says so in the message log at the one moment it is actionable.

**An edited page does not change in the browser.**
mhttpd stamps `Expires: +24h` on any `/Custom` file whose key contains a dot —
which is every JS and CSS asset. Bump the `?v=` in the page's HTML.

**The frontend hangs and Ctrl-C does nothing.**
A dead consumer still attached to SYSTEM pins the buffer's read pointer, and the
stock `send_event` blocks uninterruptibly. This frontend defaults to
`Backpressure = drop` precisely so that cannot happen, and warns at startup if
it sees a stale client. If you set `Backpressure = wait`, this is the risk you
took on. Compare `/System/Buffers/SYSTEM/Clients` with `/System/Clients`;
`odbedit -c cleanup` does *not* remove stale entries, but stopping every MIDAS
client and restarting does.

**Events sent stays 0.**
Is a run active? The physics equipment is `RO_RUNNING`. Check
`/Equipment/FakeSampic/Common/Status` — an exhausted non-looping file reports
`exhausted` and goes yellow rather than erroring.

**`odbinit` complains the shared memory is the wrong size.**
A stale POSIX segment outlived a deleted `online/`. `scripts/stop-midas.sh
--clean` removes both.
