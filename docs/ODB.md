# ODB reference

Generated from `fakesampic/settings.py`, which is the schema of record.

**hot** applies on the next readout tick. **cold** rebuilds the event source and
waits for the next begin-of-run unless `Apply Cold Settings` is `immediately`,
because applying it mid-run would silently change what a run file contains
halfway through. Every deferred change is announced in the MIDAS message log.

The readout tick is `Common/Period`, MIDAS's own knob, and is deliberately NOT
duplicated here: two places to set one value is a bug waiting to happen.

Defaults for the pulse shape, baseline, amplitude and the ToT sentinels are
measured from `data/W9PIN_14MeV_0deg_100V_run914`, not invented, so synthetic
hits sit in the same range as real ones.

## `/Equipment/FakeSampic/Settings`

| setting | default | units | | notes |
|---|---|---|---|---|
| `Source` | `binfile` |  | cold | binfile | synthetic | mixer |
| `Rate Hz` | `100.0` | ev/s | hot | ignored when BinFile/Timing is 'file' |
| `Rate Model` | `poisson` |  | hot | fixed | poisson | burst |
| `Burst Period s` | `1.0` | s | hot | spill repetition period |
| `Burst Duty` | `0.1` | 0-1 | hot | fraction of the cycle that is in-spill; in-spill rate is Rate Hz / duty |
| `Time Scale` | `1.0` | sim-ns per real-ns | hot | >1 fast-forwards the simulated clock |
| `Max Events Per Call` | `256` | ev | hot | bounds one readout, which keeps run transitions responsive |
| `Max Readout ms` | `20.0` | ms | hot | wall-clock budget per readout; the second, adaptive cap |
| `Max Backlog` | `10000` | ev | hot | catchup only; an unbounded queue is a memory leak |
| `Backlog Policy` | `drop` |  | hot | drop (give up lost time, count it) | catchup (queue it) |
| `Backpressure` | `drop` |  | hot | drop (BM_NO_WAIT, counts drops) | wait (stock, can hang on a stale client) |
| `Timing Bank` | `True` |  | hot | emit AT00 alongside AD00 |
| `Restamp Timestamps` | `True` |  | cold | OFF = passthrough, the only mode comparable byte-for-byte with bin_to_mid |
| `Restamp Hit Number` | `True` |  | cold | monotonic hit numbering across loops; wraps at 2^31-1 |
| `Reset Clock At BOR` | `True` |  | cold | restart simulated t0 at 0 each run; keeps the float32 'time' field meaningful |
| `FE Board Index` | `-1` |  | hot | -1 = take it from the file header |
| `Seed` | `0` |  | cold | 0 = nondeterministic; nonzero = reproducible |
| `Apply Cold Settings` | `at-bor` |  | hot | at-bor | immediately |

### `BinFile/`

| setting | default | units | | notes |
|---|---|---|---|---|
| `Files` | `[<FS_DEFAULT_BIN>, , , ...]` |  | cold | paths or globs; blank entries are padding and ignored |
| `Timing` | `file` |  | hot | file = use the run's own gaps | rate = use Rate Hz |
| `Gap ns` | `100.0` | ns | cold | hits closer than this belong to one event |
| `Loop` | `True` |  | hot | replay forever |
| `Loop Gap ns` | `1000000.0` | ns | hot | gap inserted at a loop seam, where the file's own gap is undefined |
| `Shuffle Files` | `False` |  | cold | randomise file order each pass |
| `Start Event` | `0` | ev | cold | skip this many events at the start of each pass |
| `Max Events Per Pass` | `0` | ev | cold | 0 = the whole file |

### `Synthetic/`

| setting | default | units | | notes |
|---|---|---|---|---|
| `Model` | `track` |  | hot | track (correlated clusters) | parametric (independent strips) |
| `N Planes` | `8` |  | cold | LGAD layers to invent |
| `Strips Per Plane` | `10` |  | cold |  |
| `Pitch mm` | `0.5` | mm | cold | strip pitch |
| `Plane Z mm` | `[-70.0, -60.0, -50.0, ...]` | mm | cold | position along the beam |
| `Plane Orientation` | `[X, Y, X, ...]` |  | cold | X or Y: the axis each plane MEASURES |
| `Plane Offset mm` | `[0.0, 0.0, 0.0, ...]` | mm | hot | alignment shift per plane |
| `Plane Efficiency` | `[0.98, 0.98, 0.98, ...]` | 0-1 | hot | per plane per event; a plane sees the particle or it does not |
| `First Channel` | `0` |  | cold | DAQ channel of plane 0 strip 0 |
| `Hit Probability` | `0.15` | 0-1 | hot | parametric only: chance each strip fires |
| `Beam X mm` | `0.0` | mm | hot |  |
| `Beam Y mm` | `0.0` | mm | hot |  |
| `Beam Sigma mm` | `1.5` | mm | hot | transverse beam spot width |
| `Beam Divergence mrad` | `2.0` | mrad | hot | track angular spread |
| `Sensor Fit` | `[0.95143, 0.28024, 0.055166, ...]` | mm | hot | amp1,sigma1,amp2,sigma2,amp3,sigma3 charge-sharing mixture. SIGMAS ARE mm |
| `Charge Threshold` | `0.02` | fraction | hot | strips below this fraction of the cluster are not read out |
| `Amplitude V` | `0.06` | V | hot | MIP pulse height (measured: run914 peaks near 0.064) |
| `Amplitude Spread` | `0.25` | relative | hot | event-to-event amplitude smearing |
| `Baseline V` | `0.7459` | V | hot | measured from run914 |
| `Baseline Noise V` | `0.0015` | V | hot | gaussian noise rms |
| `Rise ns` | `0.6` | ns | hot | must be shorter than Fall ns |
| `Fall ns` | `2.0` | ns | hot |  |
| `Time Jitter ps` | `40.0` | ps | hot | per-hit timing smear |
| `Sampling MSps` | `6400` | MS/s | cold | 6400 -> 0.15625 ns/sample, the run914 setting |
| `Peak Sample` | `20.0` | sample | hot | where the pulse peaks in the 64-sample window |
| `Randomize First Cell` | `True` |  | hot | randomise first_cell_physical_index, as real data does |
| `Raw ToT` | `65535` |  | hot | sentinel every run914 hit carries |
| `ToT ns` | `-1.0` | ns | hot | sentinel every run914 hit carries |

### `Mixer/`

| setting | default | units | | notes |
|---|---|---|---|---|
| `Child Sources` | `[binfile, synthetic, , ...]` |  | cold | kinds to overlay; blank entries ignored; may not contain 'mixer' |
| `Mode` | `overlay` |  | hot | overlay (each child, per Weights) | pileup (one primary + Poisson(mu)) |
| `Weights` | `[1.0, 1.0, 1.0, ...]` | 0-1 | hot | overlay: probability each child contributes |
| `Pileup Mu` | `0.5` |  | hot | mean number of extra sub-events |
| `Jitter ns` | `[0.0, 0.0, 0.0, ...]` | ns | hot | uniform +/- time shift applied per child |
| `Channel Offset` | `[0, 0, 0, ...]` |  | cold | shifts a child's channels; without it two copies of a one-channel file pile up |
| `Sort Hits` | `True` |  | hot | sort merged hits by time; required by the event builder |

## `/Equipment/FakeSampic/Common`

| key | value | why |
|---|---|---|
| `Event ID` | `1` | same as `converter.bin_to_mid`, so the two are comparable |
| `Type` | `EQ_PERIODIC` | `EQ_USER` is stripped by the framework with a logged complaint |
| `Read on` | `RO_RUNNING` | no fabricated physics events outside a run |
| `Period` | `20` ms | the readout TICK, not the rate |
| `Log history` | `0` | **must be**: history would copy every AD00 byte blob into the ODB each second |
| `Event limit` | `0` | **must be**: a non-zero limit crashes the python framework |

Both `Log history` and `Event limit` are corrected at startup with an
explanation in the message log, because both are reasonable things to set and
both break the frontend in ways that do not point at their cause.

## `/Equipment/FakeSampicDQM/`

`RO_ALWAYS` with history on, so rates stay visible between runs.

| bank | type | length | contents |
|---|---|---|---|
| `FSRT` | FLOAT | 128 | per-channel hit rate, Hz. `-1` = no DAQ channel |
| `FSAM` | FLOAT | 128 | per-channel mean amplitude, V. `-999` = no hits this period |
| `FSST` | FLOAT | 12 | generator statistics, named in `Settings/Names FSST` |

Bank lengths are fixed by the compiled-in `identity.N_DAQ_CHANNELS` and never
follow the geometry, because mlogger builds its history schema from a bank's
length when it starts: a bank that changed length when somebody edited a setting
would break history for every run already recorded.

`Settings` also carries the channel map — `Channel Plane`, `Channel Strip`,
`Channel Position mm`, `Plane Names`, `Plane Z mm` and so on — which is how a
custom page draws the detector without knowing anything about this code. It is
derived, so it is rewritten at every frontend start.

