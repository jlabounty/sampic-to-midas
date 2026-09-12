"""What this frontend *is*, in one place.

Two kinds of value live here and the distinction matters:

Deployment-INVARIANT, compiled in
    Equipment names, event IDs, the buffer, and the DAQ channel count. These are
    part of the data format: an analyser or a custom page that reads our events
    hardcodes them, so they cannot be per-site configuration. Changing one is a
    format change and needs the consumers changed with it.

Deployment-specific, from the environment
    The MIDAS client name and the alarm class, which differ between a laptop and
    a real DAQ host sharing an experiment with other frontends.

`PHYSICS_EVENT_ID` is deliberately the same value as `converter.bin_to_mid`'s,
because the replayed stream and the offline conversion of the same file are
compared event for event; see docs/RUNNING.md.
"""

import os

# -- format constants: changing these is a format change ---------------------

REPLAY_EQUIP_NAME = "FakeSampic"
DQM_EQUIP_NAME = "FakeSampicDQM"

PHYSICS_EVENT_ID = 1        # == converter.bin_to_mid.PHYSICS_EVENT_ID
DQM_EVENT_ID = 200

BUFFER_NAME = "SYSTEM"

N_DAQ_CHANNELS = 128
"""SAMPIC DAQ channels this frontend can address.

Compiled in rather than read from the ODB because the DQM bank lengths are
derived from it, and mlogger builds its history schema from a bank's length when
it starts: a bank that changed length when somebody edited a setting would break
history for every previously recorded run. Unmapped channels are reported with a
sentinel instead (see `fakesampic.occupancy`).

128, not the 64 of a single SAMPIC module, because the default fake detector is
8 planes of 10 strips = 80 channels and the whole point is to generate detectors
larger than the one on the bench. Real systems reach this the same way, by
running several modules: `pi_midas` derives `sampic_index = channel // 16` from
the channel number (converter/sampic_banks.py:73), so 128 channels is 8 SAMPIC
chips and needs no format change. Raising it later is cheap in code and
expensive in history, so it is set high once here.
"""

UNMAPPED_RATE = -1.0
"""`FSRT` value for a channel not in the geometry map.

Distinguishable from a real 0 Hz, which is the point -- 0 Hz on a mapped
channel is a detector problem, and it should not look like an empty slot.
"""

NO_READING = -999.0
"""`FSAM` value for a mapped channel with no hits this period.

-999 rather than 0 or NaN: it is the sentinel this data already uses (the
SAMPIC standalone format writes -1 ToT and the WaveDream scalers use -999 for a
missing temperature), and NaN does not survive the ODB float round trip.
"""


def client_name() -> str:
    """MIDAS client name. Deployment-specific: two of these may share a host."""
    return os.environ.get("FS_CLIENT_NAME", "fake-sampic-fe")


def alarm_class() -> str:
    """Alarm class raised by this frontend, or "" to stay silent.

    Defaults to silent: a fake data source has no business raising an alarm in
    an experiment it is a guest in.
    """
    return os.environ.get("FS_ALARM_CLASS", "")
