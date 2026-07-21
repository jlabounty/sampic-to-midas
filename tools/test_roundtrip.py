"""Pytest wrapper for the .bin -> .mid round trip on the example run.

Run from sampic-to-midas/:  .venv/bin/python -m pytest tools/test_roundtrip.py
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.dirname(HERE)
sys.path.insert(0, DEMO)

from converter import bin_to_mid  # noqa: E402
import validate_midas  # noqa: E402

EXAMPLE_BIN = os.path.join(
    DEMO, "..", "data", "W9PIN_14MeV_0deg_100V_run914",
    "W9PIN_14MeV_0deg_100V_run914.bin")


@pytest.mark.skipif(not os.path.exists(EXAMPLE_BIN),
                    reason="example run914 .bin not present")
def test_roundtrip(tmp_path):
    mid = str(tmp_path / "run914.mid")
    assert bin_to_mid.main([EXAMPLE_BIN, "-o", mid, "--gap-ns", "100"]) == 0
    validate_midas.FAILURES.clear()
    assert validate_midas.main([EXAMPLE_BIN, mid, "--gap-ns", "100"]) == 0
