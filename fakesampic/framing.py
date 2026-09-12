"""The brpc wire format between the analyzer and the custom pages.

Binary, not JSON. A 128-channel persistence reply is ~330 kB of int16 samples;
as JSON that is several MB of decimal text, parsed into Python floats at one
end and JS numbers at the other, several times a second.

Every reply is one envelope:

    <I total_size      including these 8 bytes
    char tag[4]        'hst1' 'hst2' 'pers' 'tmpl' 'list' 'json' 'err '

so a reader can dispatch on the tag without knowing what it asked for, and a
page that does not understand a tag can say so instead of misreading it.

Everything is little-endian, which every machine this will run on is, and is
also what the MIDAS banks themselves are.

THIS FILE AND pages/js/sampic-brpc.js ARE ONE FORMAT IN TWO LANGUAGES. Change
one and you must change the other; tests/test_framing.py pins the byte layout so
a change cannot pass unnoticed.
"""

import json
import struct
from typing import Iterable, List, Tuple

import numpy as np

from .hist import Hist1D, Hist2D, Persistence
from .template import PulseTemplate

VERSION = 1
ENVELOPE = struct.Struct("<I4s")

TAG_HIST1 = b"hst1"
TAG_HIST2 = b"hst2"
TAG_PERSIST = b"pers"
TAG_TEMPLATE = b"tmpl"
TAG_LIST = b"list"
TAG_JSON = b"json"
TAG_ERROR = b"err "

# volts = int16 counts / 1e4, the scale converter.sampic_banks applies.
ADC_SCALE = 1.0e-4


def _put_str(parts: List[bytes], text: str) -> None:
    """Length-prefixed UTF-8. 16-bit length: these are labels, not documents."""
    raw = (text or "").encode("utf-8")[:65535]
    parts.append(struct.pack("<H", len(raw)))
    parts.append(raw)


def _envelope(tag: bytes, body: bytes) -> bytes:
    return ENVELOPE.pack(ENVELOPE.size + len(body), tag) + body


def encode_error(message: str) -> bytes:
    return _envelope(TAG_ERROR, (message or "").encode("utf-8"))


def encode_list(names: Iterable[str]) -> bytes:
    return _envelope(TAG_LIST, "\n".join(names).encode("utf-8"))


def encode_json(obj) -> bytes:
    return _envelope(TAG_JSON, json.dumps(obj).encode("utf-8"))


def encode_hist1(h: Hist1D) -> bytes:
    parts: List[bytes] = [struct.pack("<II", VERSION, h.nbins),
                          struct.pack("<dd", h.xlow, h.xhigh),
                          struct.pack("<q", int(h.entries)),
                          struct.pack("<dd", h.underflow, h.overflow)]
    _put_str(parts, h.name)
    _put_str(parts, h.xlabel)
    _put_str(parts, h.title)
    parts.append(np.asarray(h.counts, dtype="<f4").tobytes())
    return _envelope(TAG_HIST1, b"".join(parts))


def encode_hist2(h: Hist2D) -> bytes:
    parts: List[bytes] = [struct.pack("<I", VERSION),
                          struct.pack("<Idd", h.nx, h.xlow, h.xhigh),
                          struct.pack("<Idd", h.ny, h.ylow, h.yhigh),
                          struct.pack("<q", int(h.entries))]
    _put_str(parts, h.name)
    _put_str(parts, h.xlabel)
    _put_str(parts, h.ylabel)
    _put_str(parts, h.title)
    # Row-major with y outer, matching the numpy (ny, nx) shape.
    parts.append(np.ascontiguousarray(h.counts, dtype="<f4").tobytes())
    return _envelope(TAG_HIST2, b"".join(parts))


def encode_persistence(p: Persistence, channels: Iterable[int]) -> bytes:
    chans = [int(c) for c in channels]
    parts: List[bytes] = [struct.pack("<I", VERSION)]
    blocks: List[bytes] = []
    n_blocks = 0
    for c in chans:
        wf = p.waveforms(c)
        if wf.shape[0] == 0:
            continue
        n_blocks += 1
        blocks.append(struct.pack("<III", c, wf.shape[0], wf.shape[1]))
        blocks.append(np.ascontiguousarray(wf, dtype="<i2").tobytes())
    parts.append(struct.pack("<I", n_blocks))
    parts.append(struct.pack("<f", ADC_SCALE))
    parts.extend(blocks)
    return _envelope(TAG_PERSIST, b"".join(parts))


def encode_template(t: PulseTemplate) -> bytes:
    parts: List[bytes] = [
        struct.pack("<I", VERSION),
        struct.pack("<I", t.n_samples),
        struct.pack("<f", float(t.dt_ns)),
        struct.pack("<I", t.peak_index),
        struct.pack("<I", int(t.n_averaged)),
    ]
    _put_str(parts, t.name)
    _put_str(parts, t.source)
    _put_str(parts, t.created)
    parts.append(np.asarray(t.samples, dtype="<f4").tobytes())
    has_spread = t.spread is not None
    parts.append(struct.pack("<I", 1 if has_spread else 0))
    if has_spread:
        parts.append(np.asarray(t.spread, dtype="<f4").tobytes())
    return _envelope(TAG_TEMPLATE, b"".join(parts))


# --- decoding, for tests and for offline tooling ----------------------------

class _Reader:
    def __init__(self, buf: bytes, pos: int = 0):
        self.buf, self.pos = buf, pos

    def unpack(self, fmt: str):
        s = struct.Struct(fmt)
        v = s.unpack_from(self.buf, self.pos)
        self.pos += s.size
        return v

    def string(self) -> str:
        (n,) = self.unpack("<H")
        out = self.buf[self.pos:self.pos + n].decode("utf-8")
        self.pos += n
        return out

    def array(self, dtype, count: int) -> np.ndarray:
        dt = np.dtype(dtype)
        n = dt.itemsize * count
        a = np.frombuffer(self.buf, dtype=dt, count=count, offset=self.pos)
        self.pos += n
        return a


def decode(buf: bytes):
    """(tag, payload) for any reply this module produces."""
    if len(buf) < ENVELOPE.size:
        raise ValueError(f"reply of {len(buf)} bytes is shorter than its envelope")
    total, tag = ENVELOPE.unpack_from(buf, 0)
    if total != len(buf):
        raise ValueError(f"envelope says {total} bytes, got {len(buf)}")
    r = _Reader(buf, ENVELOPE.size)
    body = buf[ENVELOPE.size:]

    if tag == TAG_ERROR:
        return tag, body.decode("utf-8")
    if tag == TAG_LIST:
        text = body.decode("utf-8")
        return tag, (text.split("\n") if text else [])
    if tag == TAG_JSON:
        return tag, json.loads(body.decode("utf-8"))
    if tag == TAG_HIST1:
        version, nbins = r.unpack("<II")
        xlow, xhigh = r.unpack("<dd")
        (entries,) = r.unpack("<q")
        under, over = r.unpack("<dd")
        name, xlabel, title = r.string(), r.string(), r.string()
        counts = r.array("<f4", nbins)
        return tag, Hist1D(name=name, nbins=nbins, xlow=xlow, xhigh=xhigh,
                           xlabel=xlabel, title=title,
                           counts=counts.astype(np.float64), entries=entries,
                           underflow=under, overflow=over)
    if tag == TAG_HIST2:
        (version,) = r.unpack("<I")
        nx, xlow, xhigh = r.unpack("<Idd")
        ny, ylow, yhigh = r.unpack("<Idd")
        (entries,) = r.unpack("<q")
        name, xlabel, ylabel, title = r.string(), r.string(), r.string(), r.string()
        counts = r.array("<f4", nx * ny).reshape(ny, nx)
        return tag, Hist2D(name=name, nx=nx, xlow=xlow, xhigh=xhigh,
                           ny=ny, ylow=ylow, yhigh=yhigh, xlabel=xlabel,
                           ylabel=ylabel, title=title,
                           counts=counts.astype(np.float64), entries=entries)
    if tag == TAG_PERSIST:
        (version,) = r.unpack("<I")
        (n_blocks,) = r.unpack("<I")
        (scale,) = r.unpack("<f")
        out = {"scale": scale, "channels": {}}
        for _ in range(n_blocks):
            ch, n_waves, n_samples = r.unpack("<III")
            out["channels"][ch] = r.array("<i2", n_waves * n_samples).reshape(
                n_waves, n_samples)
        return tag, out
    if tag == TAG_TEMPLATE:
        (version,) = r.unpack("<I")
        (n_samples,) = r.unpack("<I")
        (dt_ns,) = r.unpack("<f")
        (peak_index,) = r.unpack("<I")
        (n_averaged,) = r.unpack("<I")
        name, source, created = r.string(), r.string(), r.string()
        samples = r.array("<f4", n_samples).astype(np.float64)
        (has_spread,) = r.unpack("<I")
        spread = r.array("<f4", n_samples).astype(np.float64) if has_spread else None
        return tag, PulseTemplate(samples=samples, dt_ns=dt_ns, name=name,
                                  source=source, created=created,
                                  n_averaged=n_averaged, spread=spread)
    raise ValueError(f"unknown reply tag {tag!r}")
