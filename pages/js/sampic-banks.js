// Decoding AD00 / AT00 banks in the browser.
//
// The layout is the contract in
// main/reco_testbeam/pi_midas/include/sampic/EventBankUnpacker.hh, mirrored in
// converter/sampic_banks.py. Both are #pragma pack(1), so every field is at a
// byte offset with no padding and DataView reads them directly.
//
// AD00, 344 bytes per hit:
//     0   11 x int32   fe_board_index, channel, hit_number, sampic_index,
//                      channel_index, data_size, inl_corrected, adc_corrected,
//                      residual_pedestal_corrected, cell_info,
//                      first_cell_physical_index
//    44   64 x float32 corrected waveform, VOLTS (already divided by 1e4)
//   300   scalars      raw_tot i32, tot f32, amplitude f32, baseline f32,
//                      peak f32, time_index f32, time_instant f64,
//                      time_amplitude f32, first_cell_timestamp f64
//
// AT00, one 56-byte record: fe_timestamp_ns u64, nhits u32, nparents u32,
// then 10 x u32 of timing profile counters (all zero from this frontend).
//
// The waveform is volts, not counts: build_ad_records divides the int16 samples
// by 1e4 (converter/sampic_banks.py:84). Do not divide again.

const SAMPIC = (function () {
  "use strict";

  const HIT_BYTES = 344;
  const N_SAMPLES = 64;
  const WF_OFFSET = 44;
  const SCALAR_OFFSET = 300;
  const LITTLE_ENDIAN = true;

  // bkToObj() (midas.js) returns the banks as `event.bank`, an ARRAY of
  // {name, type, size, data, hexdata, array} -- not `event.banks`, and not
  // keyed by name. Getting that wrong means never finding AD00 and drawing
  // nothing, with no error anywhere.
  function bankByName(event, name) {
    if (!event) return null;
    const list = event.bank || event.banks;
    if (!list) return null;
    if (Array.isArray(list)) {
      for (const b of list) if (b.name === name) return b;
      return null;
    }
    return list[name] || null;
  }

  // `hexdata` is always a Uint8Array over the bank payload, whatever the TID;
  // `array` is typed by TID and `data` is a raw ArrayBuffer. Prefer hexdata so
  // a bank that is bytes-on-the-wire is read as bytes regardless of how midas.js
  // chose to interpret its type.
  function bankBytes(bank) {
    if (!bank) return null;
    const d = (bank.hexdata !== undefined) ? bank.hexdata
            : (bank.array !== undefined) ? bank.array : bank.data;
    if (!d) return null;
    if (d instanceof Uint8Array) return d;
    if (d instanceof ArrayBuffer) return new Uint8Array(d);
    if (ArrayBuffer.isView(d)) return new Uint8Array(d.buffer, d.byteOffset, d.byteLength);
    if (Array.isArray(d)) return Uint8Array.from(d);
    return null;
  }

  // Decode one AD00 payload into an array of hit objects.
  //
  // `maxHits` exists because an event display only ever draws a few dozen
  // traces, while a mixer overlay can carry hundreds; decoding all of them each
  // refresh is work nobody sees.
  function decodeAD(bytes, maxHits) {
    if (!bytes || bytes.length === 0) return [];
    if (bytes.length % HIT_BYTES !== 0) {
      console.warn("AD00 payload of " + bytes.length +
                   " bytes is not a multiple of " + HIT_BYTES);
      return [];
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const total = bytes.length / HIT_BYTES;
    const n = (maxHits && maxHits > 0) ? Math.min(total, maxHits) : total;
    const hits = [];
    for (let i = 0; i < n; i++) {
      const o = i * HIT_BYTES;
      const dataSize = view.getInt32(o + 20, LITTLE_ENDIAN);
      const nWf = Math.max(0, Math.min(dataSize, N_SAMPLES));
      const wf = new Float32Array(nWf);
      for (let s = 0; s < nWf; s++) {
        wf[s] = view.getFloat32(o + WF_OFFSET + 4 * s, LITTLE_ENDIAN);
      }
      hits.push({
        feBoard:      view.getInt32(o +  0, LITTLE_ENDIAN),
        channel:      view.getInt32(o +  4, LITTLE_ENDIAN),
        hitNumber:    view.getInt32(o +  8, LITTLE_ENDIAN),
        sampicIndex:  view.getInt32(o + 12, LITTLE_ENDIAN),
        channelIndex: view.getInt32(o + 16, LITTLE_ENDIAN),
        dataSize:     dataSize,
        firstCell:    view.getInt32(o + 40, LITTLE_ENDIAN),
        waveform:     wf,
        rawToT:       view.getInt32(o + SCALAR_OFFSET +  0, LITTLE_ENDIAN),
        tot:          view.getFloat32(o + SCALAR_OFFSET +  4, LITTLE_ENDIAN),
        amplitude:    view.getFloat32(o + SCALAR_OFFSET +  8, LITTLE_ENDIAN),
        baseline:     view.getFloat32(o + SCALAR_OFFSET + 12, LITTLE_ENDIAN),
        peak:         view.getFloat32(o + SCALAR_OFFSET + 16, LITTLE_ENDIAN),
        timeInstant:  view.getFloat64(o + SCALAR_OFFSET + 24, LITTLE_ENDIAN),
        t0:           view.getFloat64(o + SCALAR_OFFSET + 36, LITTLE_ENDIAN)
      });
    }
    hits.totalHits = total;
    return hits;
  }

  function decodeAT(bytes) {
    if (!bytes || bytes.length < 56) return null;
    const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return {
      // getBigUint64 would give a BigInt that will not go into arithmetic with
      // Numbers. Timestamps here are well under 2^53 ns (104 days), so recombine
      // the halves as Numbers instead.
      feTimestampNs: v.getUint32(0, LITTLE_ENDIAN) + v.getUint32(4, LITTLE_ENDIAN) * 4294967296,
      nHits:    v.getUint32(8, LITTLE_ENDIAN),
      nParents: v.getUint32(12, LITTLE_ENDIAN)
    };
  }

  // The whole "event -> hits" step in one call, since every page that reads
  // events wants exactly this. Returns [] for an event with no AD00 bank
  // rather than throwing, because that is a normal event from other equipment.
  function hitsOf(event, maxHits) {
    const bytes = bankBytes(bankByName(event, "AD00"));
    return bytes ? decodeAD(bytes, maxHits) : [];
  }

  // Sample spacing in ns. 6400 MS/s -> 0.15625 ns, the run914 setting.
  function dtNs(samplingMSps) {
    return 1e3 / (samplingMSps || 6400);
  }

  return {
    HIT_BYTES: HIT_BYTES,
    N_SAMPLES: N_SAMPLES,
    bankByName: bankByName,
    bankBytes: bankBytes,
    decodeAD: decodeAD,
    decodeAT: decodeAT,
    hitsOf: hitsOf,
    dtNs: dtNs
  };
})();
