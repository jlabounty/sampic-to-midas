// Does the browser-side AD00/AT00 decoder agree with the authoritative layout?
//
//     node tests/js/decode.test.js
//
// The fixture is a REAL event captured from a live run's SYSTEM buffer, with the
// expected values computed by converter.sampic_banks' numpy dtype -- the thing
// that actually defines the byte layout. So this compares the JS offsets against
// the format's own definition, on real data, rather than against a second
// hand-written copy of the same offsets.
//
// Run without a browser: sampic-banks.js declares a top-level `const SAMPIC`
// with no module system, so it is evaluated here and the binding read back.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const here = __dirname;
const src = fs.readFileSync(path.join(here, "..", "..", "pages", "js",
                                      "sampic-banks.js"), "utf8");
const sandbox = { console: console };
vm.createContext(sandbox);
// `const SAMPIC = ...` at the top level of a classic script is a lexical
// binding, not a property of the global object -- browsers share those between
// scripts in a document, but vm does not expose them on the sandbox. Evaluate
// the name as a trailing expression to get the value out.
const SAMPIC = vm.runInContext(src + "\n;SAMPIC;", sandbox);

const fixture = JSON.parse(fs.readFileSync(path.join(here, "event-fixture.json"), "utf8"));
const expect = fixture.expect;

let failures = 0;
function check(name, ok, detail) {
  if (ok) { console.log("  [PASS] " + name); }
  else { console.log("  [FAIL] " + name + (detail ? ": " + detail : "")); failures++; }
}
function closeEnough(a, b, tol) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (!(Math.abs(a[i] - b[i]) <= (tol === undefined ? 1e-6 : tol))) return false;
  }
  return true;
}

const ad = Buffer.from(fixture.ad00_b64, "base64");
const at = Buffer.from(fixture.at00_b64, "base64");
const adBytes = new Uint8Array(ad.buffer, ad.byteOffset, ad.byteLength);
const atBytes = new Uint8Array(at.buffer, at.byteOffset, at.byteLength);

console.log("AD00/AT00 decoder vs the numpy dtype, on a real captured event");

const hits = SAMPIC.decodeAD(adBytes);
check("hit count", hits.length === expect.nHits,
      hits.length + " vs " + expect.nHits);
check("payload is a whole number of 344-byte hits",
      ad.byteLength === expect.nHits * SAMPIC.HIT_BYTES);

check("channel", closeEnough(hits.map(h => h.channel), expect.channel, 0));
check("dataSize", closeEnough(hits.map(h => h.dataSize), expect.dataSize, 0));
check("firstCell", closeEnough(hits.map(h => h.firstCell), expect.firstCell, 0));

// float32 fields: exact once both sides are float32, so a tight tolerance is
// the point -- a loose one would not notice a field read at the wrong offset.
check("amplitude", closeEnough(hits.map(h => h.amplitude), expect.amplitude, 1e-9));
check("baseline", closeEnough(hits.map(h => h.baseline), expect.baseline, 1e-9));
check("peak", closeEnough(hits.map(h => h.peak), expect.peak, 1e-9));
check("first_cell_timestamp (f64)", closeEnough(hits.map(h => h.t0), expect.t0, 1e-9));
check("time_instant (f64)",
      closeEnough(hits.map(h => h.timeInstant), expect.timeInstant, 1e-9));

check("waveform length", hits[0].waveform.length === expect.waveform0.length);
check("waveform samples", closeEnough(Array.from(hits[0].waveform),
                                      expect.waveform0, 1e-9));
check("waveform is volts, not counts",
      Math.max.apply(null, expect.waveform0) < 10.0);

const atDec = SAMPIC.decodeAT(atBytes);
check("AT00 nHits matches the AD00 hit count",
      atDec.nHits === expect.nHits && atDec.nHits === expect.at.nHits);
check("AT00 fe_timestamp_ns (u64 recombined as Number)",
      Math.abs(atDec.feTimestampNs - expect.at.feTimestampNs) < 1);
check("AT00 timestamp survives the 2^32 boundary",
      Number.isFinite(atDec.feTimestampNs) && atDec.feTimestampNs >= 0);

// maxHits must cap work without corrupting what it does return.
const capped = SAMPIC.decodeAD(adBytes, 3);
check("maxHits caps decoding", capped.length === 3 && capped.totalHits === expect.nHits);
check("capped hits are still correct",
      closeEnough(capped.map(h => h.amplitude), expect.amplitude.slice(0, 3), 1e-9));

// Malformed input must be refused, not silently misread.
check("rejects a truncated payload",
      SAMPIC.decodeAD(adBytes.slice(0, 100)).length === 0);
check("handles an empty payload", SAMPIC.decodeAD(new Uint8Array(0)).length === 0);

// --- the shape bkToObj() actually returns -----------------------------------
//
// midas.js puts the banks in `event.bank` -- an ARRAY of
// {name, type, size, data, hexdata, array} -- not `event.banks`, and not keyed
// by name. Reading the wrong property finds no AD00, draws nothing, and reports
// no error anywhere, which is exactly how the scope page first shipped empty.
const bkToObjShaped = {
  event_id: 1,
  bank: [
    { name: "AD00", type: 1, size: ad.byteLength,
      data: ad.buffer.slice(ad.byteOffset, ad.byteOffset + ad.byteLength),
      hexdata: adBytes, array: adBytes },
    { name: "AT00", type: 1, size: at.byteLength,
      data: at.buffer.slice(at.byteOffset, at.byteOffset + at.byteLength),
      hexdata: atBytes, array: atBytes }
  ]
};
const foundAd = SAMPIC.bankByName(bkToObjShaped, "AD00");
check("bankByName finds a bank in bkToObj's event.bank array", foundAd !== null);
check("bankByName returns null for a bank that is absent",
      SAMPIC.bankByName(bkToObjShaped, "DRSV") === null);
const viaBank = SAMPIC.decodeAD(SAMPIC.bankBytes(foundAd));
check("decoding through bankByName/bankBytes matches decoding the payload",
      viaBank.length === expect.nHits &&
      closeEnough(viaBank.map(h => h.amplitude), expect.amplitude, 1e-9));
check("bankBytes prefers hexdata and yields the exact payload length",
      SAMPIC.bankBytes(foundAd).length === ad.byteLength);

console.log(failures === 0 ? "\nALL JS CHECKS PASSED"
                           : "\n" + failures + " JS CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
