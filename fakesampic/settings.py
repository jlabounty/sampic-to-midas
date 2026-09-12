"""The ODB settings schema, and which changes take effect immediately.

Every knob this frontend has lives under /Equipment/FakeSampic/Settings, with
one deliberate exception: the readout tick is `Common/Period`, MIDAS's own knob,
and is NOT duplicated here. Two places to set one value is a bug waiting to
happen, and the framework already honours Period.

HOT vs COLD
    A hot setting is read on the next readout tick. A cold setting rebuilds the
    event source, which for a replay means reopening files and losing the
    position in them -- so by default cold changes are deferred to the next
    begin-of-run (`Apply Cold Settings = at-bor`). Applying them mid-run is
    available and is occasionally what you want while developing a page, but it
    silently changes what a run file contains halfway through, so every change
    is announced in the MIDAS message log.

Defaults for the pulse shape, baseline, amplitude and the ToT sentinels are
MEASURED from data/W9PIN_14MeV_0deg_100V_run914 rather than invented, so
synthetic hits sit in the same range as real ones and a DQM page scaled for one
works for the other.
"""

import copy
from typing import Any, Dict, List

from .chargesharing import DEFAULT_SENSOR_FIT
from .geometry import DEFAULT_N_PLANES, DEFAULT_PITCH_MM, DEFAULT_STRIPS_PER_PLANE
from .waveform import (DEFAULT_AMPLITUDE_V, DEFAULT_BASELINE_V, DEFAULT_FALL_NS,
                       DEFAULT_NOISE_V, DEFAULT_PEAK_SAMPLE, DEFAULT_RISE_NS,
                       DEFAULT_SAMPLING_MSPS)

SOURCE_KINDS = ("binfile", "synthetic", "mixer")
RATE_MODELS = ("fixed", "poisson", "burst")
BACKLOG_POLICIES = ("drop", "catchup")
BACKPRESSURE = ("drop", "wait")
APPLY_MODES = ("at-bor", "immediately")
SYNTH_MODELS = ("track", "parametric")
MIX_MODES = ("overlay", "pileup")
BINFILE_TIMING = ("file", "rate")


def defaults(default_bin: str = "") -> Dict[str, Any]:
    """The Settings tree as it is seeded when absent."""
    return {
        "Source": "binfile",

        "Rate Hz": 100.0,
        "Rate Model": "poisson",
        "Burst Period s": 1.0,
        "Burst Duty": 0.1,
        "Time Scale": 1.0,

        "Max Events Per Call": 256,
        "Max Readout ms": 20.0,
        "Max Backlog": 10000,
        "Backlog Policy": "drop",
        "Backpressure": "drop",

        "Timing Bank": True,
        "Restamp Timestamps": True,
        "Restamp Hit Number": True,
        "Reset Clock At BOR": True,
        "FE Board Index": -1,
        "Seed": 0,
        "Apply Cold Settings": "at-bor",

        "BinFile": {
            "Files": [default_bin or "", "", "", ""],
            "Timing": "file",
            "Gap ns": 100.0,
            "Loop": True,
            "Loop Gap ns": 1.0e6,
            "Shuffle Files": False,
            "Start Event": 0,
            "Max Events Per Pass": 0,
        },

        "Synthetic": {
            "Model": "track",
            "N Planes": DEFAULT_N_PLANES,
            "Strips Per Plane": DEFAULT_STRIPS_PER_PLANE,
            "Pitch mm": DEFAULT_PITCH_MM,
            "Plane Z mm": [-70.0 + 10.0 * i for i in range(DEFAULT_N_PLANES)],
            "Plane Orientation": ["XY"[i % 2] for i in range(DEFAULT_N_PLANES)],
            "Plane Offset mm": [0.0] * DEFAULT_N_PLANES,
            "Plane Efficiency": [0.98] * DEFAULT_N_PLANES,
            "First Channel": 0,
            "Hit Probability": 0.15,
            "Beam X mm": 0.0,
            "Beam Y mm": 0.0,
            "Beam Sigma mm": 1.5,
            "Beam Divergence mrad": 2.0,
            "Sensor Fit": list(DEFAULT_SENSOR_FIT),
            "Charge Threshold": 0.02,
            "Amplitude V": DEFAULT_AMPLITUDE_V,
            "Amplitude Spread": 0.25,
            "Baseline V": DEFAULT_BASELINE_V,
            "Baseline Noise V": DEFAULT_NOISE_V,
            "Rise ns": DEFAULT_RISE_NS,
            "Fall ns": DEFAULT_FALL_NS,
            "Time Jitter ps": 40.0,
            "Sampling MSps": DEFAULT_SAMPLING_MSPS,
            "Peak Sample": DEFAULT_PEAK_SAMPLE,
            "Randomize First Cell": True,
            "Raw ToT": 65535,
            "ToT ns": -1.0,
        },

        "Mixer": {
            "Child Sources": ["binfile", "synthetic", "", ""],
            "Mode": "overlay",
            "Weights": [1.0, 1.0, 1.0, 1.0],
            "Pileup Mu": 0.5,
            "Jitter ns": [0.0, 0.0, 0.0, 0.0],
            "Channel Offset": [0, 0, 0, 0],
            "Sort Hits": True,
        },
    }


HOT_KEYS = frozenset({
    "Rate Hz", "Rate Model", "Burst Period s", "Burst Duty", "Time Scale",
    "Max Events Per Call", "Max Readout ms", "Max Backlog", "Backlog Policy",
    "Backpressure", "Timing Bank", "FE Board Index",
    "Apply Cold Settings",
    # BinFile/Timing chooses between the file's own gaps and the configured
    # rate. That only rebuilds the timing model, not the source, so it is hot --
    # and it has to be, because Rate Model is hot and rebuilding the timing model
    # reads Timing anyway. Classifying it cold would have made it apply as a
    # side effect of an unrelated hot change, which is worse than either.
    "BinFile/Timing", "BinFile/Loop", "BinFile/Loop Gap ns",
    "Synthetic/Model", "Synthetic/Plane Offset mm", "Synthetic/Plane Efficiency",
    "Synthetic/Hit Probability", "Synthetic/Beam X mm", "Synthetic/Beam Y mm",
    "Synthetic/Beam Sigma mm", "Synthetic/Beam Divergence mrad",
    "Synthetic/Sensor Fit", "Synthetic/Charge Threshold",
    "Synthetic/Amplitude V", "Synthetic/Amplitude Spread",
    "Synthetic/Baseline V", "Synthetic/Baseline Noise V",
    "Synthetic/Rise ns", "Synthetic/Fall ns", "Synthetic/Time Jitter ps",
    "Synthetic/Peak Sample", "Synthetic/Randomize First Cell",
    "Synthetic/Raw ToT", "Synthetic/ToT ns",
    "Mixer/Mode", "Mixer/Weights", "Mixer/Pileup Mu", "Mixer/Jitter ns",
    "Mixer/Sort Hits",
})
"""Settings applied on the next readout tick.

Everything else rebuilds the source. Note `Restamp Timestamps` is COLD on
purpose: it changes what the emitted data means, and flipping it mid-run would
produce a file whose first half cannot be compared with its second.
"""


def is_hot(path: str) -> bool:
    """True when `path` (relative to Settings) takes effect without a rebuild."""
    return normalise_path(path) in HOT_KEYS


def normalise_path(path: str) -> str:
    """Reduce an ODB callback path to a 'Sub/Key' form.

    `detailed_settings_changed_func` is handed a full ODB path such as
    /Equipment/FakeSampic/Settings/Synthetic/Beam Sigma mm; the schema is keyed
    on the part below Settings.
    """
    marker = "/Settings/"
    if marker in path:
        path = path.split(marker, 1)[1]
    return path.strip("/")


def as_list(value, n: int = None, fill=None) -> List:
    """ODB arrays of length 1 come back as a bare scalar; normalise them.

    This bites the common case, not an exotic one: `BinFile/Files` with a single
    file configured is exactly what most people will have, and treating the
    string as a list of characters fails in a thoroughly confusing way.
    """
    if value is None:
        out = []
    elif isinstance(value, (list, tuple)):
        out = list(value)
    else:
        out = [value]
    if n is not None:
        if len(out) < n:
            out = out + [fill] * (n - len(out))
        out = out[:n]
    return out


def non_empty(values) -> List[str]:
    """Strings from an ODB string array, dropping the blank padding entries.

    ODB string arrays are fixed length, so a four-slot Files array with one file
    in it carries three empty strings that are padding rather than data.
    """
    return [str(v).strip() for v in as_list(values) if str(v).strip()]


def validate(settings: Dict[str, Any]) -> List[str]:
    """Human-readable complaints about a Settings tree; empty when it is usable.

    Returns problems rather than raising, so the frontend can report all of them
    at once and keep running on the previous configuration instead of dying on
    the first typo somebody makes in mhttpd.
    """
    problems: List[str] = []

    def choice(value, allowed, name):
        if str(value) not in allowed:
            problems.append(f"{name} is {value!r}, expected one of {', '.join(allowed)}")

    choice(settings.get("Source"), SOURCE_KINDS, "Source")
    choice(settings.get("Rate Model"), RATE_MODELS, "Rate Model")
    choice(settings.get("Backlog Policy"), BACKLOG_POLICIES, "Backlog Policy")
    choice(settings.get("Backpressure"), BACKPRESSURE, "Backpressure")
    choice(settings.get("Apply Cold Settings"), APPLY_MODES, "Apply Cold Settings")

    if float(settings.get("Rate Hz", 0)) <= 0:
        problems.append(f"Rate Hz must be > 0, got {settings.get('Rate Hz')}")
    if not 0 < float(settings.get("Burst Duty", 0.1)) <= 1:
        problems.append(f"Burst Duty must be in (0,1], got {settings.get('Burst Duty')}")
    if float(settings.get("Time Scale", 1.0)) <= 0:
        problems.append(f"Time Scale must be > 0, got {settings.get('Time Scale')}")
    if int(settings.get("Max Events Per Call", 1)) < 1:
        problems.append("Max Events Per Call must be >= 1")
    if float(settings.get("Max Readout ms", 1)) <= 0:
        problems.append("Max Readout ms must be > 0")

    binf = settings.get("BinFile", {})
    choice(binf.get("Timing"), BINFILE_TIMING, "BinFile/Timing")
    if settings.get("Source") == "binfile" and not non_empty(binf.get("Files")):
        problems.append("Source is 'binfile' but BinFile/Files is empty")

    syn = settings.get("Synthetic", {})
    choice(syn.get("Model"), SYNTH_MODELS, "Synthetic/Model")
    if int(syn.get("N Planes", 1)) < 1:
        problems.append("Synthetic/N Planes must be >= 1")
    if int(syn.get("Strips Per Plane", 1)) < 1:
        problems.append("Synthetic/Strips Per Plane must be >= 1")
    if float(syn.get("Pitch mm", 1)) <= 0:
        problems.append("Synthetic/Pitch mm must be > 0")
    if len(as_list(syn.get("Sensor Fit"))) != 6:
        problems.append("Synthetic/Sensor Fit needs exactly 6 values")

    mix = settings.get("Mixer", {})
    choice(mix.get("Mode"), MIX_MODES, "Mixer/Mode")
    if settings.get("Source") == "mixer":
        kids = non_empty(mix.get("Child Sources"))
        if not kids:
            problems.append("Source is 'mixer' but Mixer/Child Sources is empty")
        for k in kids:
            if k == "mixer":
                problems.append("Mixer/Child Sources cannot contain 'mixer'")
            elif k not in SOURCE_KINDS:
                problems.append(f"Mixer/Child Sources has unknown kind {k!r}")
    return problems


def merged(stored: Dict[str, Any], default_bin: str = "") -> Dict[str, Any]:
    """Defaults with `stored` laid over them, so a missing key never KeyErrors."""
    out = defaults(default_bin)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            sub = copy.deepcopy(out[key])
            sub.update(value)
            out[key] = sub
        else:
            out[key] = value
    return out
