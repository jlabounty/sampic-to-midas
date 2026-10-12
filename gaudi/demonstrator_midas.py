"""Gaudi options: SAMPIC MIDAS file -> decoded + analysed, in ONE job.

  PIMidasSelector (reads the .mid)
  -> PIMidasConversionSvc -> PIMidasDecoder dispatching to PITMidasSampic
  -> TES /Event/Sampic*
  -> PIDEMPrelimAnalysis  -> TES /Event/DemonstratorEvent
  -> PIAOutputStream (RNTuple "rec")

This is THE pipeline: decoding and analysis in a single pass, producing one
_rec.root. Three things follow from doing it in one job rather than two:

  * No PIAInputStream, so no field is ever resolved by type *name* at runtime,
    so the SAMPIC data model needs no allocator registrations at all. (An
    earlier two-pass route did, in a PIDEMSampicFieldTypes.cpp that no longer
    exists -- see the note at the end of this docstring.)
  * The ODB reaches PIHeaderSvc directly, because PIMidasSelector::ReadBOR()
    puts it there -- so PIDEMPrelimAnalysis.ODBFile can stay empty and the
    first tier of its lookup ladder is used.
  * One output file carries both the decoded SAMPIC hits and the demonstrator
    collection, and only one pass over the data is made.

The .mid should have been written with a real ODB payload:

    tools/run_settings_to_odb.py run_settings.txt -o odb.json
    converter/bin_to_mid.py run.bin -o run.mid --odb-json odb.json

Run inside the pioneer-midas container after building main with the testbeam
libraries (./setup.sh -b -t -o -e):

    SAMPIC_MID=... SAMPIC_REC=... gaudirun.py demonstrator_midas.py

Note on reading an existing _rec.root back in: PIAInputStream resolves every
field by type name through PIDataModel::Allocator(), and no SAMPIC product is
declared there, so that route does not work out of the box. The fix belongs
upstream in pi_midas or shared/unpacking, where it would serve every SAMPIC
job; it is deliberately not carried here.
"""

import os

from Configurables import (
    ApplicationMgr,
    EvtDataSvc,
    EvtPersistencySvc,
    Gaudi__Sequencer,
    PIDEMPrelimAnalysis,
    PIDEMTrackletHits,
)
from Gaudi.Configuration import INFO

from shared.PiGaudiSharedSvcConf import (
    PIAOutputStream,
    PIConditionsSvc,
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
    "SAMPIC_MID", "/workdir/sampic-to-midas/output/ATAR1_300V.mid")
output_file = os.environ.get(
    "SAMPIC_REC", "/workdir/sampic-to-midas/output/ATAR1_300V_rec.root")

selector = PIMidasSelector("EventSelector")
selector.file = input_file
selector.require = "none"

conversion = PIMidasConversionSvc("ConversionSvc")

persistency = EvtPersistencySvc()
persistency.CnvServices = ["PIMidasConversionSvc/ConversionSvc"]

decoder = PIMidasDecoder("SampicDecoder")
decoder.decoders = ["PITMidasSampic"]

prelim = PIDEMPrelimAnalysis("DemonstratorPrelim")
# ODBFile deliberately unset: the BOR ODB is already in PIHeaderSvc here.
# The nominal values below are only reached if the .mid carries no ODB; they
# are taken from run_settings.txt so the job stays correct either way.
prelim.NominalSamplingGSPS = float(os.environ.get("DEM_GSPS", "2.133"))
prelim.NominalTriggerThresholds = [float(os.environ.get("DEM_THR", "-0.0248"))] * 32

# A functional algorithm throws if its input is absent, and the BOR/EOR marker
# events carry no SAMPIC bank at all -- so the analysis is scheduled inside a
# sequencer that skips events without /Event/SampicEvent. This is the pattern
# the wavedream algorithms document for exactly the same reason.
# The demonstrator hits, also published as PIRECTrackletHit so the existing
# tracking code can consume them. Set TrackletHits.Enable = False to skip it.
tracklets = PIDEMTrackletHits("TrackletHits")

# Strip positions come from a conditions table, not from code.
conditions = PIConditionsSvc("PIConditionsSvc")
conditions.JsonFiles = [os.environ.get(
    "DEM_STRIP_MAP",
    "/workdir/main/reco_testbeam/conditions/bt2026_demonstrator_strip_map.json")]

sampic_seq = Gaudi__Sequencer("SampicSequence")
sampic_seq.Members = [prelim, tracklets]
sampic_seq.RequireObjects = ["/Event/SampicEvent"]

writer = PIAOutputStream("RNTupleWriter")
writer.destination = output_file

mgr = ApplicationMgr()
mgr.TopAlg = [decoder, sampic_seq]
mgr.ExtSvc = [
    PIHeaderSvc(),
    PIDataModelSvc(),
    EvtDataSvc(),
    # The selector must come before PIConditionsSvc: the conditions service
    # takes the run number from the ODBHeader, which PIMidasSelector::ReadBOR()
    # publishes into PIHeaderSvc during its own initialize().
    selector,
    conditions,
    conversion,
    persistency,
]
mgr.OutStream = [writer]
mgr.EvtSel = selector
mgr.EvtMax = int(os.environ.get("SAMPIC_EVTMAX", "-1"))
mgr.OutputLevel = INFO
