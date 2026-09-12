# Installing MIDAS and this frontend from scratch

> Following this for the first time? [GETTING-STARTED.md](GETTING-STARTED.md)
> walks the same ground with less detail and ends with a page you wrote.

Everything lands inside the `fake_sampic/` workspace. No system directory is
written, `/etc/exptab` is not touched, and any MIDAS experiment already on the
machine is unaffected — `MIDAS_EXPTAB` is exported for these processes only.

    fake_sampic/
    ├── midas/           MIDAS source + build + install  (MIDASSYS)
    ├── midas-deps/      zlib header shim, only if you lack zlib1g-dev
    ├── online/          the experiment: exptab, ODB, history, run files
    └── sampic-to-midas/ this repository

## 0. What you need

| | |
|---|---|
| OS | Linux. Developed on Ubuntu 26.04 / WSL2, gcc 15, cmake 4.2 |
| conda | miniconda or anaconda (set `FS_CONDA_ROOT` if not `~/miniconda3`) |
| git | with network access to bitbucket.org |
| zlib headers | `sudo apt install zlib1g-dev` — **or** let the build use conda's, see below |

ROOT is **not** required. Neither is CUDA, SQLite or libcurl.

## 1. Clone this repository

```bash
cd ~/github/pioneer/fake_sampic          # or wherever the workspace lives
git clone <this repo> sampic-to-midas    # if it is not already here
cd sampic-to-midas
```

## 2. Create the conda environment

```bash
scripts/setup-conda.sh                   # python 3.14 + numpy + pytest
```

Python **3.14** is the default and is what this was developed and tested on;
the MIDAS bindings are pure-python ctypes and work fine on it. If a future
dependency does not, `scripts/setup-conda.sh --python 3.12` is the fallback and
nothing else changes.

Do this *before* step 3 if you do not have `zlib1g-dev`: the MIDAS build then
borrows zlib from this environment.

## 3. Build MIDAS

```bash
scripts/setup-midas.sh                   # clones + builds tag midas-2026-07-a
```

Takes a few minutes. It is idempotent — rerun it after a `git pull` in
`midas/` and it rebuilds incrementally.

Then install the python bindings into the environment (setup-conda.sh does this
automatically if MIDAS was already built; otherwise rerun it):

```bash
scripts/setup-conda.sh
```

### Three things about this build worth knowing

They are handled automatically, and are written down because each one fails in
a way that does not point at its cause.

**zlib is mandatory and cmake does not finish the job.** (With `zlib1g-dev`
installed none of this applies and the build is unremarkable; it is written down
for machines without root.)
MIDAS does `find_package(ZLIB REQUIRED)` (`CMakeLists.txt:236`) and
`midasio.cxx` includes `<zlib.h>`, but the include directory is never added to
the compile flags — so cmake happily reports *"Found ZLIB"* and the build then
fails with `fatal error: zlib.h: No such file or directory`. With
`zlib1g-dev` installed this never shows up. Without it, `setup-midas.sh`
points at conda's zlib and passes `-isystem` explicitly.

It uses a directory containing **only** symlinks to `zlib.h` and `zconf.h`,
not conda's whole `include/`. Putting all of conda's headers on the include
path would shadow the system OpenSSL headers while still linking the system
`libssl` — an ABI mismatch that does not announce itself.

The binaries get an **RPATH** to conda's `lib` rather than relying on
`LD_LIBRARY_PATH`. Exporting that globally puts conda's `libtinfo`, `libssl`
and friends ahead of the system ones for every program the shell runs,
including `/bin/bash`, which starts printing version warnings.

**`NO_NVIDIA=ON` is required on a machine with CUDA headers but no driver
library.** MIDAS builds `msysmon-nvidia` whenever `/usr/local/cuda/include`
exists *and* `nvidia-smi -L` succeeds (`CMakeLists.txt:396-402`), then links
`-lnvidia-ml` from `/usr/local/cuda/lib64`. On WSL2 both conditions hold and the
library is absent, so the entire build fails on a monitoring tool nothing here
uses.

**`libmidas-c-compat.so` is the file that matters.** The python bindings
`dlopen` that specific library (`midas/client.py:113`), not `libmidas.a`. A
build that produced `bin/odbedit` but not this file looks complete and cannot
run a python frontend. `setup-midas.sh` checks for it explicitly.

## 4. Start the experiment

```bash
scripts/start-midas.sh
```

Creates `online/` with its own `exptab` and a 64 MB ODB, then starts `mhttpd`
and `mlogger`. Open <http://localhost:8080>.

Run-file writing is **off** by default — see [RUNNING.md](RUNNING.md).

> The mhttpd port is an ODB key (`/WebServer/localhost port`), not a command
> line flag, in this MIDAS series. `mhttpd -p` selects the obsolete web server
> and exits with a message instead of starting. `start-midas.sh` sets the key
> before launching, so `FS_MHTTPD_PORT` works on the first run.

## 5. Start the frontend

```bash
scripts/start-frontend.sh --daemon       # or without --daemon to watch it
```

Two equipments appear under `/Equipment`: `FakeSampic` (the event stream) and
`FakeSampicDQM` (rates and the channel map). Start a run from mhttpd, or:

```bash
source scripts/fake-sampic-env.sh
odbedit -e fakesampic -c "start now"
```

## 6. Install the custom pages

```bash
scripts/register-custom-pages.sh
```

Three pages appear in the mhttpd side menu. See [PAGES.md](PAGES.md).

## 7. Check it works

```bash
source scripts/fake-sampic-env.sh
$FS_PYTHON -m pytest tests/ -q                        # no MIDAS needed
$FS_PYTHON tests/js/decode.test.js                    # needs node; optional
```

The full end-to-end verification is in [RUNNING.md](RUNNING.md#verifying-the-chain).

## Environment variables

All optional; `scripts/fake-sampic-env.sh` is the single place they are read.

| variable | default |
|---|---|
| `FS_MIDASSYS` | `<workspace>/midas` |
| `FS_EXPT_NAME` | `fakesampic` |
| `FS_EXPT_DIR` | `<workspace>/online` |
| `FS_DATA_DIR` | `<workspace>/online/data` |
| `FS_MHTTPD_PORT` | `8080` |
| `FS_ODB_SIZE` | `64MB` |
| `FS_CONDA_ENV` / `FS_CONDA_ROOT` | `fake-sampic` / `~/miniconda3` |
| `FS_DEFAULT_BIN` | the run914 file in `../data` |
| `FS_LOGGING` | `off` |
| `FS_MIDAS_TAG` | `midas-2026-07-a` |

## Starting over

```bash
scripts/stop-midas.sh --clean     # drops the ODB and its shared memory
```

`--clean` also removes the POSIX shared-memory segments. MIDAS names them after
the experiment directory and they outlive `rm -rf online/`; left behind, the
next `odbinit` meets a stale segment of the wrong size and fails confusingly.
