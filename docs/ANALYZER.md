# The histogram backend

```bash
scripts/start-analyzer.sh --daemon      # or without --daemon to watch it
```

Everything else in this project is backend-free: the pages read the ODB and the
event buffer directly. Histograms cannot work that way — they accumulate across
events, and a browser only samples the stream a couple of times a second. The
analyzer is a MIDAS client that watches every event it is given and serves the
results to the `SampicHistos` and `SampicPersist` pages over `brpc`.

## Two design rules it does not break

**No equipment, no transition callbacks.** Registering either would put this
client in the run-transition path, where a wedged analyzer delays a run start
until the watchdog reaps it. Monitoring must never be able to stop data taking.
Run state is *polled* from `/Runinfo` instead, which cannot block anybody.

**`GET_NONBLOCKING`.** A sampling consumer. With `GET_ALL` it would apply
back-pressure to the SYSTEM buffer and therefore to the frontend — again,
monitoring interfering with data taking. Dropping events is the right behaviour
for a histogram: it changes how fast a distribution fills, not what it looks
like. The `Max Events Per Second` setting bounds the cost further.

## What it accumulates

| | |
|---|---|
| `amp/all`, `amp/baseline` | amplitude and baseline spectra over every channel |
| `amp/plane_<name>` | per plane |
| `amp/strip/<plane>_s<NN>` | **per strip** — see below |
| `baseline/strip/<plane>_s<NN>` | per strip |
| `mult/hits_per_event`, `mult/planes_per_event` | multiplicity |
| `time/spread_ns` | max−min hit time within an event |
| `occ/channel`, `occ/plane_vs_strip` | occupancy, 1-D and 2-D |
| `shape/persistence` | the classic 2-D density: every sample of every hit |
| `shape/template_rms`, `shape/rms_vs_amp` | departure from the canonical pulse shape |
| `shape/strip/<plane>_s<NN>` | that departure, per strip |

Plus a ring buffer of the **last N raw waveforms per channel**, which is what
`SampicPersist` draws.

### Why per strip

A plane-level spectrum is the sum of ten strips that see quite different things
— the centre strip of a cluster and its neighbours differ by an order of
magnitude — so the aggregate is a mixture whose shape describes no single
channel, and one sick strip is invisible in it. The aggregates are kept as an
overview; the per-strip set is what you look at when something is wrong.

It costs about 2.6× the analyser's CPU per event (measured below) and ~1.2 MB of
histogram memory for an 80-strip detector. `Per Strip Histograms` turns it off.

### Why a ring buffer *and* a 2-D density

They answer different questions. The density (`shape/persistence`) is right for
millions of events; a single misshapen pulse is averaged away in it. The ring
buffer keeps the actual last N traces, so one bad pulse in fifty is visible.
Density is kept aggregate rather than per channel because a per-channel density
would be ~9 MB of histogram and 4.5 MB per page refresh, where the ring buffer
is 330 kB in total.

## The canonical pulse shape

A template is a *normalised* shape — baseline subtracted, unit peak — so
comparing against it measures shape and nothing else. Amplitude and timing can
both look perfectly normal while a pulse is a reflection, a saturated preamp, or
a channel picking up its neighbour.

```bash
tools/make_template.py --synthetic 3000 -o config/pulse_template.json
tools/make_template.py data/.../run914.bin --min-amplitude 0.03
tools/make_template.py online/data/run00007.mid --channels 3,4,5
```

The analyzer loads `config/pulse_template.json` unless
`/Analyzer/SampicDQM/Template File` points elsewhere, histograms each hit's rms
deviation, and `SampicPersist` overlays the shape on every cell scaled to that
cell's own amplitude.

Building one from real run914 data shows why `--min-amplitude` matters: with the
default 5 mV cut the averaged shape has a 0.49 spread *at the peak*, because
run914's self-trigger sits near the noise and a near-threshold pulse's `argmax`
lands on noise rather than on the pulse. At `--min-amplitude 0.03` the spread
falls to 0.16.

## Settings — `/Analyzer/SampicDQM`

| key | default | |
|---|---|---|
| `Enabled` | `y` | stop accumulating without stopping the client |
| `Max Events Per Second` | `200` | the cost bound; see below |
| `Persistence Depth` | `20` | waveforms kept per channel |
| `Per Strip Histograms` | `y` | |
| `Template File` | `""` | empty = `config/pulse_template.json` if present |
| `Template Window` | `12` | ± samples about the peak used for the comparison |
| `Amplitude Max V` | `0.20` | upper edge of the amplitude histograms |
| `Reset At BOR` | `y` | so a plot describes one run |

Changing `Persistence Depth`, `Template File`, `Amplitude Max V`,
`Template Window` or `Per Strip Histograms` rebuilds the accumulators; the
others apply immediately. Settings are seeded with `update_structure_only`, so a
key added in a later version appears in an ODB that predates it.

## Commands

| | |
|---|---|
| `sampic::list` | every histogram name |
| `sampic::hist <name>` | one histogram, 1-D or 2-D |
| `sampic::persist <ch,ch,…\|all>` | last N waveforms per channel |
| `sampic::template` | the canonical shape |
| `sampic::status` | JSON: events, rate, throttling, template |
| `sampic::clear` | zero everything |

Replies are binary — `fakesampic/framing.py` and `pages/js/sampic-brpc.js` are
one format in two languages, checked against each other by
`tests/js/framing.test.js`. A 128-channel persistence reply is ~330 kB of int16;
as JSON it would be several MB of decimal text several times a second.

## What it all costs

Measured on this machine (WSL2, 16 cores), generator at 1 kHz, analyzer limited
to 200 ev/s, 8 planes × 10 strips, ~24 hits/event. Reproduce with
`tools/benchmark_pages.py`.

### Processes

| | CPU (one core = 100%) | private RSS |
|---|---|---|
| frontend | 12% | 41 MB |
| analyzer | 23% (per-strip on), 11% (off) | 121 MB |
| mhttpd | 0.6% idle | 39 MB |
| mlogger | 0.05% | 1 MB |

Plus **189 MB shared once for the machine** — the 128 MB SYSTEM buffer and the
61 MB ODB, mapped into every client. It appears in each process's RSS and is
easy to count four times by mistake.

Analyzer cost per event, measured offline:

| configuration | ms/event | saturates one core at |
|---|---|---|
| per-strip + template | 0.97 | ~1000 ev/s |
| per-strip, no template | 0.72 | ~1400 ev/s |
| aggregate + template | 0.38 | ~2700 ev/s |
| aggregate only | 0.25 | ~4000 ev/s |

So the analyzer is the expensive component — roughly 8× the frontend per event —
and `Max Events Per Second` is what keeps it bounded. That is why it defaults to
200 rather than to unlimited.

### Pages

Per open browser tab, every page costs **less than 0.2% of one core** on the DAQ
machine — at or below the measurement noise. What differs is bandwidth:

| page | requests/s | kB/s | kB per refresh |
|---|---|---|---|
| SampicPersist | 1.0 | 101 | 101 |
| SampicHistos | 1.0 | 21 | 20 |
| SampicScope / SampicGrid | 2.0 | 16 | 8 |
| SampicRates / SampicStrips | 0.5 | <1 | <2 |

Ten tabs of the heaviest page is ~1 MB/s. Bandwidth, not CPU, is what scales
with viewers.

## Can this run on another machine?

**Yes, and it is verified, not assumed.**

**The pages already do.** They are static files served by mhttpd and execute
entirely in the viewer's browser. Any machine that can reach mhttpd's port
displays them at no CPU cost to the DAQ machine beyond the HTTP traffic above.

**The analyzer can too.** MIDAS clients reach a remote experiment through
`mserver`:

```bash
# on the DAQ machine, once:
mserver -D                     # listens on 1175

# on the other machine:
export MIDASSYS=...            # same MIDAS build
python -m fakesampic.analyzer -h daq-host -e fakesampic
```

Tested here over the mserver TCP path: the analyzer received **200 ev/s
(1.65 MB/s)** and — the part that actually decides it — **`brpc` from mhttpd
reached the remotely connected client**, so `SampicHistos` and `SampicPersist`
work against an analyzer that is not on the DAQ machine.

The event data crosses the network: ~8 kB/event, so 1.6 MB/s at 200 ev/s and
8 MB/s at 1 kHz. Comfortable on gigabit, and `GET_NONBLOCKING` means a slow link
drops events rather than back-pressuring the frontend.

**mhttpd and mlogger stay put.** Both map the ODB and the event buffers as
shared memory.

### So where should it run?

For this workload, all of it fits easily on one modest machine: ~35% of one core
and ~200 MB private plus the 190 MB shared. Moving the analyzer off is worth
doing when you are analysing above ~1 kHz, when the analysis grows heavier than
this one, or when the DAQ machine is CPU-constrained and you would rather spend
its cores on readout. Below that, a remote analyzer buys nothing and adds a
network dependency and an mserver to keep running.
