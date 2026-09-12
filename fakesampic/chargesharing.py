"""Charge sharing between neighbouring LGAD strips.

Ported from `reference/potatar-analysis/LGADDetectorMapping.hh` (evalGaussianMixture
at :294, buildEtaTable at :304). A particle crossing at position `u` induces signal
on several strips; the fraction landing on a strip whose centre is at `c` is a
three-Gaussian mixture evaluated at `u - c`, normalised across the plane.

This is what makes generated data usable for developing a DQM page: centre-of-gravity
position reconstruction and plane-to-plane track correlations only produce sensible
distributions if the strips around a hit are lit in the right proportions. A model
that fired one strip per plane would give flat correlation plots and a position
resolution of exactly the strip pitch.

UNITS ARE MILLIMETRES.
    `evalGaussianMixture`'s comment in the C++ says "position x (μm)", but it is
    wrong and the call sites prove it: `DecemberRun` multiplies every fitted sigma by
    `um_to_mm` (:467-470), and `buildEtaTable` is called with `pitch = 0.5` over
    `x` in [-2.5, 2.5] (:475) -- millimetres throughout. Porting the comment rather
    than the code would shrink every sigma by 1000 and light exactly one strip.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np

# LGADDetectorMapping.hh:467, the W11/W4 AC-LGAD fit. Sigmas are in mm, having
# already been scaled by um_to_mm there.
DEFAULT_SENSOR_FIT: Tuple[float, ...] = (
    0.95143, 0.28024, 0.055166, 0.4375, 0.06462, 0.4178)


@dataclass(frozen=True)
class SensorFit:
    """Three-Gaussian charge-sharing profile. Amplitudes arbitrary, sigmas in mm."""

    amp1: float
    sigma1: float
    amp2: float
    sigma2: float
    amp3: float
    sigma3: float

    @classmethod
    def from_sequence(cls, values) -> "SensorFit":
        v = list(values)
        if len(v) != 6:
            raise ValueError(
                f"sensor fit needs 6 values (amp1,sigma1,amp2,sigma2,amp3,sigma3), got {len(v)}")
        if any(s <= 0 for s in (v[1], v[3], v[5])):
            raise ValueError(f"sensor fit sigmas must be > 0, got {v[1]}, {v[3]}, {v[5]}")
        return cls(*(float(x) for x in v))

    @classmethod
    def default(cls) -> "SensorFit":
        return cls.from_sequence(DEFAULT_SENSOR_FIT)


def eval_gaussian_mixture(x, fit: SensorFit) -> np.ndarray:
    """Charge-sharing profile at offset `x` mm from a strip centre.

    Vectorised over any shape of `x`; the C++ original (:294) is scalar and is
    called in a loop over strips.
    """
    x = np.asarray(x, dtype=np.float64)
    x2 = x * x
    return (fit.amp1 * np.exp(-0.5 * x2 / (fit.sigma1 * fit.sigma1))
            + fit.amp2 * np.exp(-0.5 * x2 / (fit.sigma2 * fit.sigma2))
            + fit.amp3 * np.exp(-0.5 * x2 / (fit.sigma3 * fit.sigma3)))


def strip_fractions(u, strip_centres: np.ndarray, fit: SensorFit) -> np.ndarray:
    """Normalised charge fraction on each strip, for each track position.

    `u` is (ntracks,) mm, `strip_centres` is (nstrips,) mm; the result is
    (ntracks, nstrips) and each row sums to 1. Fully vectorised: the synthetic
    generator calls this once per readout batch, not once per event.
    """
    u = np.atleast_1d(np.asarray(u, dtype=np.float64))
    centres = np.asarray(strip_centres, dtype=np.float64)
    w = eval_gaussian_mixture(u[:, None] - centres[None, :], fit)
    total = w.sum(axis=1, keepdims=True)
    # A track far enough outside the plane that every strip underflows to zero
    # would divide by zero. Leave that row at zero: no charge collected is the
    # physically right answer, and it is dropped by the amplitude threshold.
    np.divide(w, total, out=w, where=total > 0)
    return w


def build_eta_table(fit: SensorFit, n_strips: int, pitch_mm: float,
                    x_min: float = -2.5, x_max: float = 2.5,
                    dx: float = 0.005) -> Tuple[np.ndarray, np.ndarray]:
    """Centre-of-gravity bias vs true position: (x, cog - x), both mm.

    The port of `buildEtaTable` (:304). Used to check the generator against the
    C++ reference, and available to an analysis that wants to undo the bias.

    Note `buildEtaTable` computes strip centres WITHOUT the plane's position
    offset (:316), while `computeStripPosition` (:216) includes it. This follows
    the former, since the eta correction is a property of the sensor rather than
    of where the plane was bolted; `fakesampic.geometry` follows the latter for
    actual strip positions.
    """
    x = np.arange(x_min, x_max + 0.5 * dx, dx, dtype=np.float64)
    centres = (np.arange(n_strips, dtype=np.float64) - (n_strips - 1) / 2.0) * pitch_mm
    frac = strip_fractions(x, centres, fit)
    cog = frac @ centres
    return x, cog - x
