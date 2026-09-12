"""The byte-identity gate, run as a test.

Replaying run914 in passthrough mode must produce exactly the bank stream
`converter.bin_to_mid` writes from the same file. Passthrough is the only mode
where that comparison is even meaningful -- every other mode deliberately
restamps timestamps -- but since all modes share `build_ad_records` and
`build_batch`, proving this one proves the conversion for all of them.
"""

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args):
    return subprocess.run([sys.executable] + args, cwd=REPO,
                          capture_output=True, text=True)


@pytest.mark.slow
def test_replay_passthrough_is_byte_identical_to_bin_to_mid(run914, tmp_path):
    ref = tmp_path / "ref.mid"
    fe = tmp_path / "fe.mid"

    r = _run(["-m", "converter.bin_to_mid", run914, "-o", str(ref),
              "--gap-ns", "100", "--no-bor-eor"])
    assert r.returncode == 0, r.stderr

    r = _run(["-m", "fakesampic.offline", "--source", "binfile",
              "--files", run914, "--no-restamp", "--no-loop",
              "--gap-ns", "100", "--events", "42258", "--no-bor-eor",
              "-o", str(fe)])
    assert r.returncode == 0, r.stderr

    assert ref.stat().st_size == fe.stat().st_size

    r = _run(["tools/compare_mid.py", str(ref), str(fe)])
    assert "BANK STREAMS IDENTICAL" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0


@pytest.mark.slow
def test_validator_accepts_replayed_passthrough_output(run914, tmp_path):
    """The original strict validator, unmodified in its checks, against our output."""
    fe = tmp_path / "fe.mid"
    r = _run(["-m", "fakesampic.offline", "--source", "binfile",
              "--files", run914, "--no-restamp", "--no-loop",
              "--gap-ns", "100", "--events", "42258", "-o", str(fe)])
    assert r.returncode == 0, r.stderr

    r = _run(["tools/validate_midas.py", run914, str(fe), "--gap-ns", "100"])
    assert "ALL CHECKS PASSED" in r.stdout, r.stdout + r.stderr


@pytest.mark.slow
def test_synthetic_output_is_readable_and_self_consistent(tmp_path):
    import numpy as np
    from converter.mid_reader import decode_ad, decode_at, iter_events

    out = tmp_path / "syn.mid"
    r = _run(["-m", "fakesampic.offline", "--source", "synthetic",
              "--model", "track", "--events", "300", "-o", str(out)])
    assert r.returncode == 0, r.stderr

    n = 0
    for ev in iter_events(str(out)):
        if ev.is_special:
            continue
        banks = {b[0]: b[2] for b in ev.banks}
        ad = decode_ad(banks["AD00"])
        at = decode_at(banks["AT00"])
        assert at[1] == len(ad), "AT00 must report the AD00 hit count"
        assert at[0] == round(float(ad["first_cell_timestamp"][0]))
        assert (np.diff(ad["first_cell_timestamp"]) >= 0).all()
        assert (ad["data_size"] == 64).all()
        # Synthetic hits derive both scalars from the samples, so these agree.
        assert np.allclose(ad["peak"], ad["waveform"].max(axis=1), atol=1e-6)
        n += 1
    assert n == 300
