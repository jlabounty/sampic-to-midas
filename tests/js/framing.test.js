// Does the browser's wire-format decoder agree with the python encoder?
//
//     node tests/js/framing.test.js
//
// fakesampic/framing.py and pages/js/sampic-brpc.js are one format written
// twice. The fixture here was produced BY the python encoder, so this test
// compares the two implementations against each other rather than against a
// second copy of the same assumptions. A field added on one side and forgotten
// on the other shows up as a decode failure or a shifted value.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// The page script expects browser globals; stub the two it touches at load time.
const sandbox = {
  console: console,
  TextDecoder: require("util").TextDecoder,
  mjsonrpc_call: function () { throw new Error("not used in this test"); }
};
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, "..", "..", "pages", "js",
                                      "sampic-brpc.js"), "utf8");
const SBRPC = vm.runInContext(src + "\n;SBRPC;", sandbox);

const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "framing-fixture.json"), "utf8"));

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log("  [PASS] " + name);
  else { console.log("  [FAIL] " + name + (detail ? ": " + detail : "")); failures++; }
}
function buf(b64) {
  const b = Buffer.from(b64, "base64");
  // Copy into a standalone ArrayBuffer: Buffer views share a pooled backing
  // store, so passing b.buffer straight through would hand the decoder far more
  // bytes than the reply and fail its envelope-length check.
  const ab = new ArrayBuffer(b.length);
  new Uint8Array(ab).set(b);
  return ab;
}
function close(a, b, tol) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > (tol || 1e-6)) return false;
  return true;
}

console.log("brpc wire format: JS decoder vs the python encoder");

// --- 1D histogram ---
const h1 = SBRPC.decode(buf(fx.hst1.b64));
const e1 = fx.hst1.expect;
check("hst1 tag", h1.tag === "hst1");
check("hst1 name/labels", h1.name === e1.name && h1.xlabel === e1.xlabel &&
                          h1.title === e1.title);
check("hst1 binning", h1.nbins === e1.nbins &&
      Math.abs(h1.xlow - e1.xlow) < 1e-12 && Math.abs(h1.xhigh - e1.xhigh) < 1e-12);
check("hst1 entries (64-bit)", h1.entries === e1.entries,
      h1.entries + " vs " + e1.entries);
check("hst1 under/overflow carried", h1.underflow === e1.underflow &&
      h1.overflow === e1.overflow);
check("hst1 counts", close(Array.from(h1.counts), e1.counts, 1e-3));

// --- 2D histogram ---
const h2 = SBRPC.decode(buf(fx.hst2.b64));
const e2 = fx.hst2.expect;
check("hst2 tag and shape", h2.tag === "hst2" && h2.nx === e2.nx && h2.ny === e2.ny);
check("hst2 axis ranges", Math.abs(h2.ylow - e2.ylow) < 1e-12 &&
                          Math.abs(h2.yhigh - e2.yhigh) < 1e-12);
check("hst2 counts are row-major with y outer",
      close(Array.from(h2.counts), e2.counts, 1e-3));

// --- persistence ---
const p = SBRPC.decode(buf(fx.pers.b64));
check("pers tag", p.tag === "pers");
check("pers skips channels with no data", Object.keys(p.channels).sort().join() === "2,7");
check("pers scale is the AD count->volt factor",
      Math.abs(p.scale - 1e-4) < 1e-9, String(p.scale));
const want2 = fx.pers.expect.channels["2"];
check("pers ring buffer is oldest-first and complete",
      p.channels[2].nWaves === want2.length &&
      close(Array.from(SBRPC.persistWave(p.channels[2], 0, 1.0)), want2[0], 0));
check("pers last waveform matches",
      close(Array.from(SBRPC.persistWave(p.channels[2], want2.length - 1, 1.0)),
            want2[want2.length - 1], 0));
check("persistWave applies the scale",
      Math.abs(SBRPC.persistWave(p.channels[2], 0, p.scale)[1] -
               want2[0][1] * p.scale) < 1e-9);

// --- template ---
const t = SBRPC.decode(buf(fx.tmpl.b64));
const et = fx.tmpl.expect;
check("tmpl metadata", t.tag === "tmpl" && t.name === et.name &&
      t.source === et.source && t.created === et.created &&
      t.peakIndex === et.peakIndex && t.nAveraged === et.nAveraged);
check("tmpl samples", close(Array.from(t.samples), et.samples, 1e-6));
check("tmpl spread present", t.spread !== null && t.spread.length === et.samples.length);

// --- small payloads ---
check("list", SBRPC.decode(buf(fx.list.b64)).names.join() === fx.list.expect.join());
check("json", SBRPC.decode(buf(fx.json.b64)).value.events === fx.json.expect.events);

let threw = "";
try { SBRPC.decode(buf(fx.err.b64)); } catch (e) { threw = e.message; }
check("err tag is raised, not returned as data", threw === fx.err.expect, threw);

// --- malformed input must be refused ---
let bad = "";
try { SBRPC.decode(new ArrayBuffer(4)); } catch (e) { bad = e.message; }
check("refuses a reply shorter than its envelope", /envelope/.test(bad), bad);

bad = "";
const truncated = buf(fx.hst1.b64).slice(0, 40);
try { SBRPC.decode(truncated); } catch (e) { bad = e.message; }
check("refuses a truncated reply", bad.length > 0, bad);

console.log(failures === 0 ? "\nALL FRAMING CHECKS PASSED"
                           : "\n" + failures + " FRAMING CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
