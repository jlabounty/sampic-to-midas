"""LGAD plane / strip / DAQ-channel geometry.

Ported from `reference/potatar-analysis/LGADDetectorMapping.hh`: `PlaneGeometry`
(:32), `computeStripPosition` (:214) and the strip->board->sampic lookup that
`LGADChannelMap` builds (:174).

The C++ describes detectors that were actually built (`DecemberRun`, four planes
with hand-entered cabling maps). This describes whatever the ODB asks for -- the
point of the exercise is data for a detector that does not exist yet, so the
plane count, strip count, pitch and orientation are all parameters, and the
cabling map defaults to the obvious one instead of being enumerated by hand.

Both directions of the map are materialised as arrays: the generator needs
plane/strip -> channel, and the DQM equipment publishes channel -> plane/strip so
a browser page can draw the detector without knowing any of this.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from . import identity

# LGADDetectorMapping.hh:16-20, converted to mm.
DEFAULT_PITCH_MM = 0.5
DEFAULT_STRIPS_PER_PLANE = 10      # LGADChannelMap::N_STRIPS (:79)
DEFAULT_N_PLANES = 8
UNMAPPED = -1


@dataclass(frozen=True)
class Plane:
    """One sensor plane. `orientation` is the axis the strips MEASURE."""

    name: str
    index: int
    orientation: str          # "X" or "Y"
    z_mm: float
    pitch_mm: float
    n_strips: int
    offset_mm: float = 0.0
    efficiency: float = 1.0

    def strip_position_mm(self, strip) -> np.ndarray:
        """Centre of `strip`, in the measured coordinate.

        `computeStripPosition` (LGADDetectorMapping.hh:214):
            centre = (N-1)/2 - offset;  position = (strip - centre) * pitch
        so a positive offset shifts the whole plane towards +u. Note
        `buildEtaTable` omits the offset; see `fakesampic.chargesharing`.
        """
        strip = np.asarray(strip, dtype=np.float64)
        centre = (self.n_strips - 1) / 2.0 - self.offset_mm
        return (strip - centre) * self.pitch_mm

    def strip_positions_mm(self) -> np.ndarray:
        return self.strip_position_mm(np.arange(self.n_strips))


class Geometry:
    """A stack of planes plus the DAQ channel map, in both directions."""

    def __init__(self, planes: Sequence[Plane],
                 channel_map: Optional[np.ndarray] = None,
                 first_channel: int = 0,
                 n_daq_channels: int = identity.N_DAQ_CHANNELS):
        if not planes:
            raise ValueError("geometry needs at least one plane")
        self.planes: List[Plane] = list(planes)
        self.n_daq_channels = int(n_daq_channels)

        n_planes = len(self.planes)
        widths = {p.n_strips for p in self.planes}
        if len(widths) != 1:
            # Not a fundamental limit, but the (plane, strip) map is rectangular
            # and every consumer assumes it; refuse rather than silently ragged.
            raise ValueError(f"all planes must have the same strip count, got {sorted(widths)}")
        n_strips = self.planes[0].n_strips

        if channel_map is None:
            flat = first_channel + np.arange(n_planes * n_strips, dtype=np.int64)
            flat[flat >= self.n_daq_channels] = UNMAPPED
        else:
            flat = np.asarray(channel_map, dtype=np.int64).ravel()
            if flat.size != n_planes * n_strips:
                raise ValueError(
                    f"channel map has {flat.size} entries, expected "
                    f"{n_planes} planes x {n_strips} strips = {n_planes * n_strips}")
            bad = flat[(flat != UNMAPPED) & ((flat < 0) | (flat >= self.n_daq_channels))]
            if bad.size:
                raise ValueError(
                    f"channel map entries outside 0..{self.n_daq_channels - 1}: "
                    f"{sorted(set(bad.tolist()))[:8]}")
            mapped = flat[flat != UNMAPPED]
            dup, counts = np.unique(mapped, return_counts=True)
            if (counts > 1).any():
                # Two strips on one DAQ channel is a cabling error in real life and
                # would make occupancy per channel meaningless here.
                raise ValueError(
                    f"channel map assigns these channels twice: {dup[counts > 1].tolist()[:8]}")

        self.channel_of = flat.reshape(n_planes, n_strips)

        # Reverse map, sized by the compiled-in channel count so the DQM banks are
        # always the same length. See identity.N_DAQ_CHANNELS.
        n = self.n_daq_channels
        self.channel_plane = np.full(n, UNMAPPED, dtype=np.int64)
        self.channel_strip = np.full(n, UNMAPPED, dtype=np.int64)
        self.channel_position_mm = np.zeros(n, dtype=np.float64)
        for p in self.planes:
            for s in range(n_strips):
                ch = int(self.channel_of[p.index, s])
                if ch == UNMAPPED:
                    continue
                self.channel_plane[ch] = p.index
                self.channel_strip[ch] = s
                self.channel_position_mm[ch] = float(p.strip_position_mm(s))

    # -- convenience ---------------------------------------------------------

    @property
    def n_planes(self) -> int:
        return len(self.planes)

    @property
    def n_strips(self) -> int:
        return self.planes[0].n_strips

    @property
    def mapped_channels(self) -> np.ndarray:
        return np.flatnonzero(self.channel_plane != UNMAPPED)

    def plane_z_mm(self) -> np.ndarray:
        return np.array([p.z_mm for p in self.planes], dtype=np.float64)

    def plane_names(self) -> List[str]:
        return [p.name for p in self.planes]

    def channel_names(self) -> List[str]:
        """Per-channel labels for the DQM `Names <bank>` arrays, e.g. "P3_s07"."""
        out = []
        for ch in range(self.n_daq_channels):
            pi = int(self.channel_plane[ch])
            if pi == UNMAPPED:
                out.append(f"ch{ch:02d}")
            else:
                out.append(f"{self.planes[pi].name}_s{int(self.channel_strip[ch]):02d}")
        return out

    def describe(self) -> str:
        n_mapped = int(self.mapped_channels.size)
        return (f"{self.n_planes} planes x {self.n_strips} strips, "
                f"{n_mapped}/{self.n_daq_channels} channels mapped, "
                f"pitch {self.planes[0].pitch_mm} mm")


def build_geometry(n_planes: int = DEFAULT_N_PLANES,
                   n_strips: int = DEFAULT_STRIPS_PER_PLANE,
                   pitch_mm: float = DEFAULT_PITCH_MM,
                   z_mm: Optional[Sequence[float]] = None,
                   orientation: Optional[Sequence[str]] = None,
                   offsets_mm: Optional[Sequence[float]] = None,
                   efficiency: Optional[Sequence[float]] = None,
                   names: Optional[Sequence[str]] = None,
                   channel_map: Optional[Sequence[int]] = None,
                   first_channel: int = 0,
                   n_daq_channels: int = identity.N_DAQ_CHANNELS) -> Geometry:
    """Build a Geometry from plain ODB-shaped values.

    Defaults give the 8-layer LGAD telescope this project exists to fake:
    alternating X/Y planes so a track is measured in both coordinates, evenly
    spaced over 70 mm ending at the origin (the C++ puts the scintillator face
    at z = 0 with the planes upstream at negative z).
    """
    if n_planes < 1:
        raise ValueError(f"n_planes must be >= 1, got {n_planes}")
    if n_strips < 1:
        raise ValueError(f"n_strips must be >= 1, got {n_strips}")
    if pitch_mm <= 0:
        raise ValueError(f"pitch must be > 0 mm, got {pitch_mm}")

    def _fill(seq, default, name, cast):
        if seq is None:
            return [default(i) for i in range(n_planes)]
        seq = list(seq)
        if len(seq) == 1 and n_planes > 1:
            # MIDAS returns a 1-element ODB array as a bare scalar; a config that
            # means "same for every plane" is also legitimate. Broadcast both.
            seq = seq * n_planes
        if len(seq) != n_planes:
            raise ValueError(f"{name} has {len(seq)} entries, expected {n_planes}")
        return [cast(v) for v in seq]

    z = _fill(z_mm, lambda i: -70.0 + 70.0 * i / max(n_planes - 1, 1), "Plane Z mm", float)
    orient = _fill(orientation, lambda i: "XY"[i % 2], "Plane Orientation", str)
    offs = _fill(offsets_mm, lambda i: 0.0, "Plane Offset mm", float)
    eff = _fill(efficiency, lambda i: 1.0, "Plane Efficiency", float)
    nms = _fill(names, lambda i: f"P{i}", "Plane Names", str)

    for i, o in enumerate(orient):
        if o.upper() not in ("X", "Y"):
            raise ValueError(f"plane {i} orientation must be X or Y, got {o!r}")
    for i, e in enumerate(eff):
        if not 0.0 <= e <= 1.0:
            raise ValueError(f"plane {i} efficiency must be in [0,1], got {e}")

    planes = [Plane(name=nms[i], index=i, orientation=orient[i].upper(),
                    z_mm=z[i], pitch_mm=pitch_mm, n_strips=n_strips,
                    offset_mm=offs[i], efficiency=eff[i])
              for i in range(n_planes)]

    cmap = None
    if channel_map is not None:
        cmap = np.asarray(list(channel_map), dtype=np.int64)
        if cmap.size and (cmap == UNMAPPED).all():
            cmap = None          # "all -1" is how the ODB says "use the default"
    return Geometry(planes, cmap, first_channel=first_channel,
                    n_daq_channels=n_daq_channels)
