// A 2-D histogram drawn as an image, because that is what it is.
//
// MPlotGraph's colormap fills one rectangle per bin, and for each bin builds a
// CSS colour string and assigns it to ctx.fillStyle. For the 64x220 persistence
// map that is 14,080 string allocations and 14,080 CSS colour parses per
// redraw -- and it redraws on every refresh AND on every mouse move. Its
// setData also does `Math.min(...zData.filter(...))`, spreading a 14k array
// twice. Together that is enough to make the page visibly lag.
//
// Here the density is written straight into an ImageData buffer -- one pass,
// no strings, no per-bin canvas state changes -- put onto an offscreen canvas
// of exactly nx by ny pixels, and blitted to the plot area with smoothing off.
// The browser scales it; we never touch more pixels than there are bins.
//
// Why not JSROOT: it would do this well, but it is a megabyte-plus external
// dependency that MIDAS does not ship, and DAQ machines are frequently offline.
// Vendoring it would be worth it for ROOT-style fitting, projections and stats
// boxes; for drawing a density map it is a lot of machinery to import.

const SH2D = (function () {
  "use strict";

  const MARGIN = { left: 62, right: 72, top: 26, bottom: 42 };

  // Blue -> cyan -> green -> yellow -> red, as a lookup table built once. A
  // 256-entry table means the per-bin work is an index and three array reads.
  const LUT = (function () {
    const stops = [[8, 16, 90], [12, 74, 170], [0, 150, 190], [40, 185, 120],
                   [210, 200, 50], [230, 120, 40], [200, 30, 30]];
    const lut = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
      const x = i / 255 * (stops.length - 1);
      const k = Math.min(stops.length - 2, Math.floor(x));
      const f = x - k;
      for (let c = 0; c < 3; c++) {
        lut[i * 3 + c] = Math.round(stops[k][c] + f * (stops[k + 1][c] - stops[k][c]));
      }
    }
    return lut;
  })();

  const EMPTY = [246, 246, 248];      // bins with zero counts

  function niceTicks(lo, hi, want) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / Math.max(want, 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
      out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    }
    return out;
  }

  function fmt(v) {
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 1e5 || a < 1e-3) return v.toExponential(1);
    if (a >= 100) return v.toFixed(0);
    if (a >= 1) return v.toFixed(2).replace(/\.?0+$/, "");
    return v.toFixed(4).replace(/0+$/, "");
  }

  // Returns how long the draw took, in ms -- surfaced by the page so a
  // regression here is visible rather than merely felt.
  function draw(canvas, h, opts) {
    const t0 = performance.now();
    const o = opts || {};
    const logZ = !!o.logZ;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 900;
    const cssH = canvas.clientHeight || 420;
    if (canvas.width !== Math.round(cssW * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const px = MARGIN.left, py = MARGIN.top;
    const pw = Math.max(10, cssW - MARGIN.left - MARGIN.right);
    const ph = Math.max(10, cssH - MARGIN.top - MARGIN.bottom);

    let zmax = 0;
    for (let i = 0; i < h.counts.length; i++) if (h.counts[i] > zmax) zmax = h.counts[i];
    const zTop = logZ ? Math.log1p(zmax) : zmax;

    // --- the density itself -------------------------------------------------
    const img = ctx.createImageData(h.nx, h.ny);
    const d = img.data;
    for (let iy = 0; iy < h.ny; iy++) {
      // Row 0 of the histogram is the LOWEST y, but image row 0 is the TOP of
      // the canvas, so rows are written bottom-up. Getting this wrong flips the
      // plot vertically, which looks plausible and is wrong.
      const src = (h.ny - 1 - iy) * h.nx;
      const dst = iy * h.nx * 4;
      for (let ix = 0; ix < h.nx; ix++) {
        const v = h.counts[src + ix];
        const p = dst + ix * 4;
        if (v <= 0) {
          d[p] = EMPTY[0]; d[p + 1] = EMPTY[1]; d[p + 2] = EMPTY[2]; d[p + 3] = 255;
        } else {
          const t = zTop > 0 ? (logZ ? Math.log1p(v) : v) / zTop : 0;
          const k = (t >= 1 ? 255 : (t <= 0 ? 0 : (t * 255) | 0)) * 3;
          d[p] = LUT[k]; d[p + 1] = LUT[k + 1]; d[p + 2] = LUT[k + 2]; d[p + 3] = 255;
        }
      }
    }
    // Via an offscreen canvas: putImageData ignores transforms and scaling, so
    // the only way to stretch the bins to the plot area is to blit it.
    const off = document.createElement("canvas");
    off.width = h.nx; off.height = h.ny;
    off.getContext("2d").putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;       // show the bins, do not blur them
    ctx.drawImage(off, px, py, pw, ph);

    // --- frame, ticks, labels ----------------------------------------------
    ctx.strokeStyle = "#9a9aa2";
    ctx.lineWidth = 1;
    ctx.strokeRect(px + 0.5, py + 0.5, pw, ph);
    ctx.fillStyle = "#40414a";
    ctx.font = "11px system-ui, sans-serif";

    ctx.textAlign = "center"; ctx.textBaseline = "top";
    niceTicks(h.xlow, h.xhigh, 8).forEach(function (v) {
      const x = px + (v - h.xlow) / (h.xhigh - h.xlow) * pw;
      if (x < px - 1 || x > px + pw + 1) return;
      ctx.beginPath(); ctx.moveTo(x, py + ph); ctx.lineTo(x, py + ph + 4); ctx.stroke();
      ctx.fillText(fmt(v), x, py + ph + 6);
    });
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    niceTicks(h.ylow, h.yhigh, 6).forEach(function (v) {
      const y = py + ph - (v - h.ylow) / (h.yhigh - h.ylow) * ph;
      if (y < py - 1 || y > py + ph + 1) return;
      ctx.beginPath(); ctx.moveTo(px - 4, y); ctx.lineTo(px, y); ctx.stroke();
      ctx.fillText(fmt(v), px - 7, y);
    });

    ctx.fillStyle = "#33343a";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    if (h.xlabel) ctx.fillText(h.xlabel, px + pw / 2, cssH - 4);
    if (h.title) {
      ctx.textBaseline = "top";
      ctx.fillText(h.title, px + pw / 2, 4);
    }
    if (h.ylabel) {
      ctx.save();
      ctx.translate(11, py + ph / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.fillText(h.ylabel, 0, 0);
      ctx.restore();
    }

    // --- colour bar ---------------------------------------------------------
    const bx = px + pw + 14, bw = 13;
    const bar = ctx.createImageData(1, ph);
    for (let i = 0; i < ph; i++) {
      const k = (((ph - 1 - i) / Math.max(ph - 1, 1)) * 255 | 0) * 3;
      const p = i * 4;
      bar.data[p] = LUT[k]; bar.data[p + 1] = LUT[k + 1];
      bar.data[p + 2] = LUT[k + 2]; bar.data[p + 3] = 255;
    }
    const boff = document.createElement("canvas");
    boff.width = 1; boff.height = ph;
    boff.getContext("2d").putImageData(bar, 0, 0);
    ctx.drawImage(boff, bx, py, bw, ph);
    ctx.strokeRect(bx + 0.5, py + 0.5, bw, ph);

    ctx.fillStyle = "#40414a";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    const nz = 5;
    for (let i = 0; i <= nz; i++) {
      const frac = i / nz;
      const v = logZ ? Math.expm1(frac * zTop) : frac * zmax;
      ctx.fillText(fmt(v), bx + bw + 4, py + ph - frac * ph);
    }

    return { ms: performance.now() - t0, zmax: zmax, plot: { px: px, py: py, pw: pw, ph: ph } };
  }

  // Which bin is under the pointer, for a hover readout.
  function binAt(h, geom, offsetX, offsetY) {
    const g = geom.plot;
    if (offsetX < g.px || offsetX > g.px + g.pw ||
        offsetY < g.py || offsetY > g.py + g.ph) return null;
    const fx = (offsetX - g.px) / g.pw;
    const fy = 1 - (offsetY - g.py) / g.ph;
    const ix = Math.min(h.nx - 1, Math.max(0, Math.floor(fx * h.nx)));
    const iy = Math.min(h.ny - 1, Math.max(0, Math.floor(fy * h.ny)));
    return {
      ix: ix, iy: iy,
      x: h.xlow + (ix + 0.5) * (h.xhigh - h.xlow) / h.nx,
      y: h.ylow + (iy + 0.5) * (h.yhigh - h.ylow) / h.ny,
      count: h.counts[iy * h.nx + ix]
    };
  }

  return { draw: draw, binAt: binAt };
})();
