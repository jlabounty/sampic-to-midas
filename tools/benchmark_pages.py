#!/usr/bin/env python3
"""Measure what each custom page costs the machine running MIDAS.

    tools/benchmark_pages.py                # all pages, 20 s each
    tools/benchmark_pages.py --seconds 60 --pages SampicPersist

Each page is a polling HTTP client, so its cost lands on mhttpd (which serves
it) and, for the two backend pages, on the analyzer (which answers brpc). This
replays each page's actual request pattern -- same endpoints, same cadence --
and measures the CPU those processes burn and the bytes crossing the socket.

Why this matters: the answer decides whether a shift screen can sit on another
machine, or whether every open browser tab is competing with the DAQ for CPU.

CPU is read from /proc/<pid>/stat (utime+stime), memory from VmRSS. The numbers
are per-page DELTAS over an idle baseline measured the same way, so the
frontend's own load does not appear in them.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

BASE = os.environ.get("FS_MHTTPD_URL", "http://localhost:8080")
CLOCK = os.sysconf("SC_CLK_TCK")

# Each page's real polling pattern: (interval seconds, [request, ...]).
# Kept next to the pages themselves -- if a page's cadence changes, change it
# here too or the benchmark quietly measures something else.
PAGES = {
    "SampicScope":   (0.5, [("event", None)]),
    "SampicGrid":    (0.5, [("event", None)]),
    "SampicStrips":  (2.0, [("odb", ["/Equipment/FakeSampicDQM/Variables/FSRT",
                                     "/Equipment/FakeSampicDQM/Common/Period"])]),
    "SampicRates":   (2.0, [("odb", ["/Equipment/FakeSampicDQM/Variables/FSST",
                                     "/Equipment/FakeSampic/Common/Status",
                                     "/Equipment/FakeSampic/Statistics/Events per sec.",
                                     "/Equipment/FakeSampic/Statistics/kBytes per sec.",
                                     "/Equipment/FakeSampic/Common/Period",
                                     "/Equipment/FakeSampicDQM/Settings/Names FSST",
                                     "/Equipment/FakeSampicDQM/Common/Period"])]),
    "SampicHistos":  (3.0, [("brpc", ("sampic::list", "")),
                            ("brpc", ("sampic::hist", "shape/persistence")),
                            ("brpc", ("sampic::status", ""))]),
    "SampicPersist": (2.0, [("brpc", ("sampic::persist", "all")),
                            ("brpc", ("sampic::status", ""))]),
}


def find_pids():
    """pid of each MIDAS-side process we care about."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            comm = open(f"/proc/{pid}/comm").read().strip()
        except OSError:
            continue
        # Our python processes first, matched on the module they run. `comm` for
        # these is the interpreter's own name ("python3.14"), which varies with
        # the environment, so match the command line and require it not be a
        # shell -- a script mentioning the module would otherwise count.
        if comm.startswith("python"):
            if "fakesampic.analyzer" in cmd:
                out["analyzer"] = int(pid)
            elif "fakesampic.frontend" in cmd:
                out["frontend"] = int(pid)
            continue
        if comm in ("bash", "sh", "dash", "grep", "ps"):
            continue
        if comm == "mhttpd":
            out["mhttpd"] = int(pid)
        elif comm == "mlogger":
            out["mlogger"] = int(pid)
    return out


def cpu_seconds(pid):
    try:
        parts = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()
        return (int(parts[11]) + int(parts[12])) / CLOCK      # utime + stime
    except (OSError, IndexError):
        return None


def rss_mb(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return None


def post(payload):
    req = urllib.request.Request(BASE + "?mjsonrpc",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def do_request(kind, spec):
    if kind == "event":
        return post({"jsonrpc": "2.0", "id": 1, "method": "bm_receive_event",
                     "params": {"buffer_name": "SYSTEM", "event_id": -1,
                                "trigger_mask": -1, "get_recent": True}})
    if kind == "odb":
        return post({"jsonrpc": "2.0", "id": 1, "method": "db_get_values",
                     "params": {"paths": spec}})
    if kind == "brpc":
        cmd, args = spec
        return post({"jsonrpc": "2.0", "id": 1, "method": "brpc",
                     "params": {"client_name": "sampic-analyzer", "cmd": cmd,
                                "args": args, "max_reply_length": 8000000}})
    raise ValueError(kind)


class PageClient(threading.Thread):
    """One simulated browser tab."""

    def __init__(self, page, interval, requests):
        super().__init__(daemon=True)
        self.page, self.interval, self.requests = page, interval, requests
        self.stop_flag = threading.Event()
        self.bytes = 0
        self.calls = 0
        self.errors = 0

    def run(self):
        while not self.stop_flag.is_set():
            t0 = time.monotonic()
            for kind, spec in self.requests:
                try:
                    self.bytes += len(do_request(kind, spec))
                    self.calls += 1
                except Exception:
                    self.errors += 1
            wait = self.interval - (time.monotonic() - t0)
            if wait > 0:
                self.stop_flag.wait(wait)


def measure(page, interval, requests, seconds, pids, tabs):
    base_cpu = {k: cpu_seconds(v) for k, v in pids.items()}
    t0 = time.monotonic()
    clients = [PageClient(page, interval, requests) for _ in range(tabs)]
    for c in clients:
        c.start()
    time.sleep(seconds)
    for c in clients:
        c.stop_flag.set()
    for c in clients:
        c.join(timeout=10)
    elapsed = time.monotonic() - t0

    cpu = {}
    for name, pid in pids.items():
        now = cpu_seconds(pid)
        if now is not None and base_cpu.get(name) is not None:
            cpu[name] = 100.0 * (now - base_cpu[name]) / elapsed
    total_bytes = sum(c.bytes for c in clients)
    calls = sum(c.calls for c in clients)
    errors = sum(c.errors for c in clients)
    return {
        "page": page, "tabs": tabs, "seconds": round(elapsed, 1),
        "calls_per_s": round(calls / elapsed, 2),
        "kB_per_s": round(total_bytes / elapsed / 1024, 1),
        "kB_per_refresh": round(total_bytes / max(calls, 1) / 1024, 1),
        "cpu": {k: round(v, 2) for k, v in sorted(cpu.items())},
        "errors": errors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--tabs", type=int, default=1,
                    help="simulate this many browser tabs of each page")
    ap.add_argument("--pages", nargs="+", default=sorted(PAGES))
    ap.add_argument("--json", default="", help="also write the results here")
    args = ap.parse_args(argv)

    pids = find_pids()
    if "mhttpd" not in pids:
        print("mhttpd is not running; start the experiment first", file=sys.stderr)
        return 1
    print("processes:", ", ".join(f"{k}={v}" for k, v in sorted(pids.items())))
    print(f"memory (VmRSS): " +
          ", ".join(f"{k} {rss_mb(v):.0f} MB" for k, v in sorted(pids.items())
                    if rss_mb(v)))

    print(f"\nidle baseline ({args.seconds:.0f} s, no pages open)")
    idle = measure("(idle)", 1e9, [], args.seconds, pids, 0)
    print("  cpu%: " + ", ".join(f"{k} {v}" for k, v in idle["cpu"].items()))

    results = [idle]
    print(f"\nper page, {args.tabs} tab(s) each, {args.seconds:.0f} s")
    print(f"  {'page':15s} {'calls/s':>8s} {'kB/s':>8s} {'kB/req':>7s}   cpu% by process")
    for page in args.pages:
        if page not in PAGES:
            print(f"  unknown page {page}", file=sys.stderr)
            continue
        interval, requests = PAGES[page]
        r = measure(page, interval, requests, args.seconds, pids, args.tabs)
        # Subtract the idle load so the number is what the PAGE costs.
        for k in list(r["cpu"]):
            r["cpu"][k] = round(r["cpu"][k] - idle["cpu"].get(k, 0.0), 2)
        results.append(r)
        cpu = ", ".join(f"{k} {v:+.2f}" for k, v in r["cpu"].items() if abs(v) >= 0.01)
        print(f"  {page:15s} {r['calls_per_s']:8.2f} {r['kB_per_s']:8.1f} "
              f"{r['kB_per_refresh']:7.1f}   {cpu or 'below noise'}"
              + (f"   [{r['errors']} errors]" if r["errors"] else ""))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"pids": pids, "results": results}, f, indent=1)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
