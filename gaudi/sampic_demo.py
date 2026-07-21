"""Gaudi options: SAMPIC MIDAS file -> decoded waveform RNTuple.

Chain: PIMidasSelector (reads the .mid produced by converter/bin_to_mid.py)
-> PIMidasConversionSvc -> PIMidasDecoder dispatching banks to PITMidasSampic
-> TES paths /Event/Sampic* -> PIAOutputStream (RNTuple "rec").

Run inside the pioneer-midas container after building main with the testbeam
libraries (./setup.sh -b -t -e):

    SAMPIC_MID=... SAMPIC_REC=... gaudirun.py sampic_demo.py

The output RNTuple "rec" has fields named after the TES paths with non-alnum
chars replaced by '_': _Event_SampicEvent, _Event_SampicEventTiming,
_Event_SampicCollectorTiming, _Event_HasSampicCollectorTiming.

Deliberately standalone: prod/baseline.py pulls in DetResponse/Reco
Configurables which need the -d/-o builds; the MIDAS chain does not.
"""

import os

from Configurables import ApplicationMgr, EvtDataSvc, EvtPersistencySvc
from Gaudi.Configuration import INFO

from shared.PiGaudiSharedSvcConf import (
    PIAOutputStream,
    PIDataModelSvc,
    PIHeaderSvc,
)

try:
    # Conf module generated when reco_testbeam/pi_midas is built (needs MIDASSYS)
    from pi_midas.PIONEER_MIDAS_READERConf import (
        PIMidasConversionSvc,
        PIMidasDecoder,
        PIMidasSelector,
    )
except ImportError:
    # fallback: the merged confdb makes the generic package work too
    from Configurables import (
        PIMidasConversionSvc,
        PIMidasDecoder,
        PIMidasSelector,
    )

input_file = os.environ.get(
    "SAMPIC_MID", "/workdir/sampic-to-midas/output/run914.mid")
output_file = os.environ.get(
    "SAMPIC_REC", "/workdir/sampic-to-midas/output/run914_rec.root")

selector = PIMidasSelector("EventSelector")
selector.file = input_file
selector.require = "none"

conversion = PIMidasConversionSvc("ConversionSvc")

persistency = EvtPersistencySvc()
persistency.CnvServices = ["PIMidasConversionSvc/ConversionSvc"]

decoder = PIMidasDecoder("SampicDecoder")
decoder.decoders = ["PITMidasSampic"]

writer = PIAOutputStream("RNTupleWriter")
writer.destination = output_file

mgr = ApplicationMgr()
mgr.TopAlg = [decoder]
mgr.ExtSvc = [
    PIHeaderSvc(),      # safe with empty file list; kept explicit for clarity
    PIDataModelSvc(),
    EvtDataSvc(),
    selector,
    conversion,
    persistency,
]
mgr.OutStream = [writer]
mgr.EvtSel = selector
mgr.EvtMax = int(os.environ.get("SAMPIC_EVTMAX", "-1"))
mgr.OutputLevel = INFO
