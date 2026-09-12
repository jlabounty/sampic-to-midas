"""Live fake SAMPIC data for MIDAS.

`converter/` turns a SAMPIC .bin file into a .mid file offline. This package
turns one into a *live* stream: a MIDAS frontend that replays real files on an
endless loop, synthesises events for detectors that do not exist yet, or mixes
the two -- so MIDAS custom pages can be developed with no detector attached.

Only `fakesampic.frontend` and `fakesampic.midasbank` import `midas`. Everything
else is plain Python and numpy, so the sources, geometry, pacing and waveform
model stay unit-testable on a machine with no MIDAS installed.
"""

__all__ = ["identity"]
