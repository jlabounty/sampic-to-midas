"""Shared fixtures.

Almost everything here runs with no MIDAS installed -- that is the point of
keeping `midas` imports confined to two modules. The few tests that do need it
are skipped rather than failed when it is absent, so the suite is useful on a
laptop and complete on a DAQ machine.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

RUN914 = os.path.join(
    os.path.dirname(REPO), "data", "W9PIN_14MeV_0deg_100V_run914",
    "W9PIN_14MeV_0deg_100V_run914.bin")


@pytest.fixture(scope="session")
def run914():
    """The one real SAMPIC file in this workspace."""
    if not os.path.isfile(RUN914):
        pytest.skip(f"real SAMPIC data not present at {RUN914}")
    return RUN914


@pytest.fixture(scope="session")
def midas_available():
    try:
        import midas  # noqa: F401
        import midas.event  # noqa: F401
        return True
    except Exception:
        return False


@pytest.fixture
def rng():
    import numpy as np
    return np.random.default_rng(20260911)
