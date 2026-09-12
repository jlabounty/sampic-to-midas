// Talking to the histogram backend.
//
// THIS FILE AND fakesampic/framing.py ARE ONE FORMAT IN TWO LANGUAGES. Change
// one and you must change the other; tests/test_framing.py pins the byte layout
// on the python side and tests/js/framing.test.js checks this decoder against
// bytes that python produced.
//
// Every reply is one envelope:
//     <I total_size      including these 8 bytes
//     char tag[4]        'hst1' 'hst2' 'pers' 'tmpl' 'list' 'json' 'err '
// so a reply can be dispatched on its tag without knowing what was asked, and
// an unrecognised tag can be reported rather than misread.
//
// Binary rather than JSON because a 128-channel persistence reply is ~330 kB of
// int16 samples; as JSON that is several MB of decimal text several times a
// second.

const SBRPC = (function () {
  "use strict";

  const LE = true;
  const DEFAULT_CLIENT = "sampic-analyzer";
  const MAX_REPLY = 8 * 1024 * 1024;

  function call(cmd, args, clientName, maxLen) {
    return mjsonrpc_call("brpc", {
      client_name: clientName || DEFAULT_CLIENT,
      cmd: cmd,
      args: args || "",
      max_reply_length: maxLen || MAX_REPLY
    }, "arraybuffer").then(function (rpc) {
      // A JSON reply means mhttpd itself failed -- usually the analyzer is not
      // running, or is not answering brpc. Surface that as an error rather than
      // trying to decode it as a histogram.
      if (!(rpc instanceof ArrayBuffer)) {
        throw new Error("analyzer did not answer (is it running?)");
      }
      return decode(rpc);
    });
  }

  function Reader(buf, pos) {
    this.view = new DataView(buf);
    this.buf = buf;
    this.pos = pos || 0;
  }
  Reader.prototype.u32 = function () { const v = this.view.getUint32(this.pos, LE); this.pos += 4; return v; };
  Reader.prototype.i64 = function () {
    // Entry counts are well under 2^53; recombining two 32-bit halves as a
    // Number keeps them usable in arithmetic, which a BigInt would not be.
    const lo = this.view.getUint32(this.pos, LE);
    const hi = this.view.getInt32(this.pos + 4, LE);
    this.pos += 8;
    return hi * 4294967296 + lo;
  };
  Reader.prototype.f32 = function () { const v = this.view.getFloat32(this.pos, LE); this.pos += 4; return v; };
  Reader.prototype.f64 = function () { const v = this.view.getFloat64(this.pos, LE); this.pos += 8; return v; };
  Reader.prototype.str = function () {
    const n = this.view.getUint16(this.pos, LE);
    this.pos += 2;
    const bytes = new Uint8Array(this.buf, this.pos, n);
    this.pos += n;
    return new TextDecoder("utf-8").decode(bytes);
  };
  Reader.prototype.f32a = function (count) {
    // Copy rather than view: the payload is not guaranteed 4-byte aligned
    // within the envelope, and a misaligned typed-array view throws.
    const out = new Float32Array(count);
    for (let i = 0; i < count; i++) { out[i] = this.view.getFloat32(this.pos + 4 * i, LE); }
    this.pos += 4 * count;
    return out;
  };
  Reader.prototype.i16a = function (count) {
    const out = new Int16Array(count);
    for (let i = 0; i < count; i++) { out[i] = this.view.getInt16(this.pos + 2 * i, LE); }
    this.pos += 2 * count;
    return out;
  };

  function decode(buf) {
    if (buf.byteLength < 8) throw new Error("reply shorter than its envelope");
    const r = new Reader(buf, 0);
    const total = r.u32();
    // Built from char codes rather than TextDecoder: the tag is four ASCII
    // bytes, and the "ascii" label is not universally supported (node without
    // full ICU rejects it outright).
    const tagBytes = new Uint8Array(buf, 4, 4);
    const tag = String.fromCharCode(tagBytes[0], tagBytes[1], tagBytes[2], tagBytes[3]);
    if (total !== buf.byteLength) {
      throw new Error("envelope says " + total + " bytes, got " + buf.byteLength);
    }
    r.pos = 8;
    const bodyBytes = new Uint8Array(buf, 8, buf.byteLength - 8);

    if (tag === "err ") {
      throw new Error(new TextDecoder("utf-8").decode(bodyBytes));
    }
    if (tag === "list") {
      const text = new TextDecoder("utf-8").decode(bodyBytes);
      return { tag: "list", names: text ? text.split("\n") : [] };
    }
    if (tag === "json") {
      return { tag: "json", value: JSON.parse(new TextDecoder("utf-8").decode(bodyBytes)) };
    }
    if (tag === "hst1") {
      r.u32();                                   // format version
      const nbins = r.u32();
      const xlow = r.f64(), xhigh = r.f64();
      const entries = r.i64();
      const underflow = r.f64(), overflow = r.f64();
      const name = r.str(), xlabel = r.str(), title = r.str();
      return { tag: "hst1", name: name, nbins: nbins, xlow: xlow, xhigh: xhigh,
               entries: entries, underflow: underflow, overflow: overflow,
               xlabel: xlabel, title: title, counts: r.f32a(nbins) };
    }
    if (tag === "hst2") {
      r.u32();
      const nx = r.u32(), xlow = r.f64(), xhigh = r.f64();
      const ny = r.u32(), ylow = r.f64(), yhigh = r.f64();
      const entries = r.i64();
      const name = r.str(), xlabel = r.str(), ylabel = r.str(), title = r.str();
      // Row-major with y outer, matching the numpy (ny, nx) shape.
      return { tag: "hst2", name: name, nx: nx, ny: ny, xlow: xlow, xhigh: xhigh,
               ylow: ylow, yhigh: yhigh, entries: entries, xlabel: xlabel,
               ylabel: ylabel, title: title, counts: r.f32a(nx * ny) };
    }
    if (tag === "pers") {
      r.u32();
      const nBlocks = r.u32();
      const scale = r.f32();                     // volts per int16 count
      const channels = {};
      for (let b = 0; b < nBlocks; b++) {
        const ch = r.u32(), nWaves = r.u32(), nSamples = r.u32();
        channels[ch] = { nWaves: nWaves, nSamples: nSamples,
                         samples: r.i16a(nWaves * nSamples) };
      }
      return { tag: "pers", scale: scale, channels: channels };
    }
    if (tag === "tmpl") {
      r.u32();
      const n = r.u32();
      const dtNs = r.f32();
      const peakIndex = r.u32();
      const nAveraged = r.u32();
      const name = r.str(), source = r.str(), created = r.str();
      const samples = r.f32a(n);
      const hasSpread = r.u32();
      return { tag: "tmpl", name: name, source: source, created: created,
               dtNs: dtNs, peakIndex: peakIndex, nAveraged: nAveraged,
               samples: samples, spread: hasSpread ? r.f32a(n) : null };
    }
    throw new Error("unknown reply tag '" + tag + "'");
  }

  // One waveform out of a persistence block, in volts.
  function persistWave(block, index, scale) {
    const out = new Float32Array(block.nSamples);
    const off = index * block.nSamples;
    for (let s = 0; s < block.nSamples; s++) out[s] = block.samples[off + s] * scale;
    return out;
  }

  function binCentre(h, i) {
    return h.xlow + (i + 0.5) * (h.xhigh - h.xlow) / h.nbins;
  }

  return {
    DEFAULT_CLIENT: DEFAULT_CLIENT,
    call: call, decode: decode,
    persistWave: persistWave, binCentre: binCentre
  };
})();
