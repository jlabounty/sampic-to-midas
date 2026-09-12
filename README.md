# sampic-to-midas

Helper function to convert a SAMPIC data file into a MIDAS format for testing the PIONEER data loaders/unpackers.

Exercises the complete `raw MIDAS -> Gaudi -> waveform analysis` chain before
real SAMPIC MIDAS data exists, by re-packaging SAMPIC standalone `.bin` files
into MIDAS files that the existing `pi_midas` unpacker
(`main/reco_testbeam/pi_midas`, tool `PITMidasSampic`) decodes natively.

One MIDAS event = one time-gap cluster of hits (`--gap-ns`, default 100 ns).
Each event carries an `AD00` bank (hits + 64-sample corrected waveforms, byte
layout of `sampic/EventBankUnpacker.hh`) and an `AT00` event-timing bank.

The commands below assume this repo is checked out next to the PIONEER `main`
repo and the test-beam `data/` folder (as in the `fake_sampic` workspace), with
the parent directory mounted at `/workdir` in the container. Only the
`scripts/` and the notebook's default paths depend on that layout — the
converter and validator are standalone (any python >= 3.8 with numpy).


## Two halves

**Offline** (`converter/`, `tools/`) — turn a SAMPIC `.bin` into a `.mid` file,
and prove it is correct. This is the original content of the repository and is
described below.

**Live** (`fakesampic/`, `pages/`, `scripts/`) — a MIDAS frontend that produces
the same events as a continuous stream, so MIDAS custom pages can be developed
with no detector attached. It replays real `.bin` files on an endless loop,
generates events for detectors that do not exist yet (8 layers of LGAD strips by
default), or mixes the two, with every knob in the ODB.

    scripts/setup-conda.sh          # python environment
    scripts/setup-midas.sh          # build MIDAS from source into ../midas
    scripts/start-midas.sh          # the experiment -> http://localhost:8080
    scripts/start-frontend.sh --daemon
    scripts/register-custom-pages.sh

**New here? Start with [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)** — it
goes from a machine with nothing installed to a custom page you wrote yourself,
in about half an hour.

Reference: [INSTALL.md](docs/INSTALL.md) (building MIDAS) ·
[RUNNING.md](docs/RUNNING.md) (operating it) · [ODB.md](docs/ODB.md) (every
setting) · [PAGES.md](docs/PAGES.md) (writing pages) ·
[ANALYZER.md](docs/ANALYZER.md) (the histogram backend, and what it all costs)

The two halves share `converter/sampic_banks.py`, which is the single place any
hit becomes bank bytes. That is what makes the guarantee below testable: events
replayed live through MIDAS and written by mlogger are **byte-identical** to the
same file converted offline — verified over all 42258 events of run914.

---

## Layout

| Path | Purpose |
|---|---|
| `converter/bin_to_mid.py` | `.bin -> .mid` CLI (pure python + numpy, streaming, bounded memory) |
| `converter/*.py` | header/record parser, time-gap event builder, MIDAS writer, `.mid` reader |
| `tools/validate_midas.py` | round-trip validator — **hard gate before running Gaudi** |
| `tools/inspect_mid.py` | event/bank table dump of a `.mid` |
| `gaudi/sampic_demo.py` | Gaudi options file for the MIDAS chain |
| `scripts/` | container build/run helpers |
| `notebooks/waveform_explorer.ipynb` | waveform dumps/plots from the output RNTuple |
| `output/` | produced `.mid` and `*_rec.root` (not tracked) |

## End-to-end sequence

```bash
cd sampic-to-midas

# 1. convert (any python with numpy; here: the pioneer container)
docker run --rm --entrypoint /bin/bash -v "$(pwd)/..":/workdir \
    ghcr.io/pioneer-experiment/pioneer:latest-x86_64 -c '
  cd /workdir/sampic-to-midas &&
  python3 converter/bin_to_mid.py \
      /workdir/data/W9PIN_14MeV_0deg_100V_run914/W9PIN_14MeV_0deg_100V_run914.bin \
      -o output/run914.mid --gap-ns 100 &&
  python3 tools/validate_midas.py \
      /workdir/data/W9PIN_14MeV_0deg_100V_run914/W9PIN_14MeV_0deg_100V_run914.bin \
      output/run914.mid --gap-ns 100'
# expect: 42258 hits, ALL CHECKS PASSED

# 2. build the MIDAS-enabled container (once, ~15 min; recipe from the
#    feature/container-updates-July26 branch of main)
scripts/build_midas_image.sh

# 3. enter it and build main with the testbeam libraries (once)
scripts/run_container.sh
  source /software/setup_container_env.sh
  cd /workdir/main
  CMAKE_FLAGS="-DBUILD_MC=OFF" ./setup.sh -b -t -o -e
  # pi_midas needs MIDASSYS (set in this image); BUILD_MC=OFF skips the
  # Geant4 simulation build, which the MIDAS chain does not need.
  # -o (Reco) must accompany -t: the top-level CMakeLists only looks for
  # Gaudi when BUILD_DER or BUILD_REC is on, and testbeam requires Gaudi.

# 4. run the reconstruction (inside the container)
  bash /workdir/sampic-to-midas/scripts/run_reco.sh
  # SAMPIC_MID/SAMPIC_REC/SAMPIC_EVTMAX env vars override the defaults

# 5. explore the waveforms (inside the container)
  bash /workdir/sampic-to-midas/scripts/run_jupyter.sh
  # open notebooks/waveform_explorer.ipynb
```

## ROOT input (already-unpacked data)

`converter/root_to_mid.py` is the ROOT-input sibling of `bin_to_mid.py`: it
reads a `dat_to_root`-style `wfms` TTree (uproot + numpy required, e.g. the
pioneer container) and produces the same MIDAS format through the same event
builder and bank writers. `WaveformData` (volts, double) is converted back to
the raw int16 counts — the exact inverse of the counts/1e4 unpacking — so all
downstream code and validators are shared. Multi-channel trees are sorted by
`StartTime` before clustering. Use `--entry-stop`/`--max-events` to slice
large files; `tools/validate_midas_root.py` is the matching round-trip gate.

Example (TRIUMF surface-muon run 108, 2.3M hits, 32 channels; first 1000
events): 20k entries -> sort -> gap-100 ns clustering gives 2316 hits in 1000
genuine coincidence events (mean 2.3, max 7 hits/event), bitwise-validated
and decoded through the Gaudi chain:

```bash
python3 converter/root_to_mid.py RUN108.root -o output/run108_first1000.mid \
    --gap-ns 100 --entry-stop 20000 --max-events 1000
python3 tools/validate_midas_root.py RUN108.root output/run108_first1000.mid \
    --gap-ns 100 --entry-stop 20000
```

## Regenerating the cross-check reference

The `.root` shipped with run914 in the test-beam `data/` folder is **empty**
(tree and histograms): `dat_to_root`'s channel filter defaults to 0 while all
run914 hits are on channel 2. The notebook therefore compares against a
reference regenerated from the same `.bin` (into `output/reference/`), built
in the container from the `unpacker/preprocess` sources:

```bash
# inside the container; the CMakeLists needs a two-line keyword-signature
# patch for ROOT 6.38, so build from a patched copy:
cd /software/root/install && source bin/thisroot.sh && cd /
cp -r /workdir/unpacker/preprocess /tmp/pp_src && rm -rf /tmp/pp_src/{build,install}
sed -i 's|target_link_libraries(TBRootDict |target_link_libraries(TBRootDict PUBLIC |;
        s|target_link_libraries(dat_to_root |target_link_libraries(dat_to_root PUBLIC |' \
    /tmp/pp_src/CMakeLists.txt
cmake -S /tmp/pp_src -B /tmp/pp_build -DCMAKE_BUILD_TYPE=Release && cmake --build /tmp/pp_build -j
mkdir -p /workdir/sampic-to-midas/output/reference && cd /workdir/sampic-to-midas/output/reference
cp /workdir/data/W9PIN_14MeV_0deg_100V_run914/W9PIN_14MeV_0deg_100V_run914{.bin,_summary.csv} .
LD_LIBRARY_PATH=/tmp/pp_build /tmp/pp_build/dat_to_root W9PIN_14MeV_0deg_100V_run914.bin 2
```

Verified agreement (notebook, run914): baselines and waveforms match to
float32 rounding (~1e-7 V); calculated-amplitude magnitudes likewise; 21 of
42258 hits flip amplitude sign because their +/- noise peaks tie within
float32 (the AD bank carries float32, `dat_to_root` computes in double).

## Notes

- The output RNTuple is named `rec`; SAMPIC data sits in fields
  `_Event_SampicEvent` (hits with `corrected_waveform`), `_Event_SampicEventTiming`,
  `_Event_SampicCollectorTiming`, `_Event_HasSampicCollectorTiming`. uproot
  reads it (use `filter_name` globs, e.g. `"*SampicEvent.hits*"`); PyROOT
  `RNTupleReader` is the fallback.
- The chain writes two hit-less bookkeeping entries (first and last, from
  event-loop priming and the EOF probe): 42258 physics events -> 42260 entries.
- run914 has a single enabled channel (mask 0x4 -> channel 2) at ~700 Hz, so
  every hit is its own event at 100 ns gap — multi-hit events appear once runs
  with several channels are converted (validated up to 126 hits/event with
  `--gap-ns 2000000`).
- BOR/EOR marker events carry a small dummy payload on purpose: with an empty
  payload, `PIMidasSelector::next()` evaluates `&data[16]` on a 16-byte vector
  and trips the debug-STL bounds assertion in the container build (a latent
  `pi_midas` edge — real MIDAS files always have an ODB dump there; worth a
  one-line `data.data() + EHDR_size` fix upstream).
- The converter maps every `.bin` field onto the AD bank; fields the standalone
  format does not carry (`cell_info`, `time_index`, `time_amplitude`,
  `residual_pedestal_corrected`) are zero — see `converter/sampic_banks.py`.
