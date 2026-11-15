/**
 * features.js
 *
 * Browser-side port of vision/measure_features.py.
 * Extracts per-frame visual features from a canvas ImageData object.
 *
 * Usage (ES module):
 *   import { extractFeatures } from './features.js';
 *
 *   const features = extractFeatures(imageData, prevImageData);
 *   // features = { brightness, hue, saturation, motion, columns[], column_heights[], column_hues[] }
 *
 * Notes:
 *   - No CROP / PIXEL_MASK / TRIM — this is generic mode for any video.
 *   - Brightness values are raw (not 5th/95th normalised) since we don't
 *     have all frames upfront in live mode.
 *   - imageData is a standard ImageData object from canvas.getImageData().
 *   - prevImageData may be null for the very first frame (motion = 0).
 */

// ── constants ────────────────────────────────────────────────────────
const N_STRIPS = 32;         // number of vertical strips (matches Python)
const DARK_THRESHOLD = 30;   // pixels with V < this are "dark" and ignored
const HUE_BINS = 180;        // histogram bins for hue (each bin = 2°)


// ── RGB → HSV conversion ─────────────────────────────────────────────
/**
 * Convert a single pixel from RGB to HSV.
 * Input:  r, g, b in 0-255
 * Output: { h: 0-179 (OpenCV-style, so 1 bin = 2°), s: 0-255, v: 0-255 }
 *
 * We use the OpenCV convention (h in 0-179) so the histogram math
 * stays identical to the Python code.
 */
function rgbToHsv(r, g, b) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;

  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;

  // Value
  const v = max;

  // Saturation
  const s = max === 0 ? 0 : delta / max;

  // Hue  (0-360°, then ÷ 2 → 0-179 for OpenCV convention)
  let h = 0;
  if (delta !== 0) {
    if (max === rn) {
      h = 60 * (((gn - bn) / delta) % 6);
    } else if (max === gn) {
      h = 60 * ((bn - rn) / delta + 2);
    } else {
      h = 60 * ((rn - gn) / delta + 4);
    }
    if (h < 0) h += 360;
  }

  return {
    h: Math.round(h / 2),   // 0-179 (OpenCV-style)
    s: Math.round(s * 255), // 0-255
    v: Math.round(v * 255), // 0-255
  };
}


// ── build HSV arrays from ImageData ──────────────────────────────────
/**
 * Convert an ImageData (RGBA, uint8) into three flat typed arrays:
 *   hArr[i], sArr[i], vArr[i]  for pixel i = row*width + col
 *
 * Returns { hArr, sArr, vArr, width, height }
 */
function imageDataToHsv(imageData) {
  const { data, width, height } = imageData;
  const n = width * height;
  const hArr = new Uint8Array(n);
  const sArr = new Uint8Array(n);
  const vArr = new Uint8Array(n);

  for (let i = 0; i < n; i++) {
    const offset = i * 4;
    const { h, s, v } = rgbToHsv(data[offset], data[offset + 1], data[offset + 2]);
    hArr[i] = h;
    sArr[i] = s;
    vArr[i] = v;
  }

  return { hArr, sArr, vArr, width, height };
}


// ── generic mask ─────────────────────────────────────────────────────
/**
 * Returns a Uint8Array (1 = keep, 0 = ignore) for every pixel.
 * Generic rule: keep pixels with V >= DARK_THRESHOLD.
 */
function buildMask(vArr) {
  const mask = new Uint8Array(vArr.length);
  for (let i = 0; i < vArr.length; i++) {
    mask[i] = vArr[i] >= DARK_THRESHOLD ? 1 : 0;
  }
  return mask;
}


// ── dominant hue from a set of pixels ────────────────────────────────
/**
 * Returns the dominant hue in degrees (0-360) from selected pixels,
 * weighted by saturation.  Falls back to 0 if no pixels pass.
 *
 * hSlice and sSlice are sub-arrays (or views) of hArr / sArr.
 * maskSlice is the corresponding sub-array of the mask.
 */
function dominantHue(hSlice, sSlice, maskSlice) {
  // Saturation-weighted histogram over HUE_BINS bins
  const hist = new Float64Array(HUE_BINS);
  let anyPixel = false;

  for (let i = 0; i < maskSlice.length; i++) {
    if (maskSlice[i]) {
      hist[hSlice[i]] += sSlice[i];
      anyPixel = true;
    }
  }

  if (!anyPixel) return 0;

  // Find peak bin
  let maxBin = 0;
  for (let b = 1; b < HUE_BINS; b++) {
    if (hist[b] > hist[maxBin]) maxBin = b;
  }

  return maxBin * 2; // convert from OpenCV bin index → degrees
}


// ── brightness ───────────────────────────────────────────────────────
/**
 * Mean V of masked pixels, normalised 0-1.
 */
function meanBrightness(vArr, mask) {
  let sum = 0;
  let count = 0;
  for (let i = 0; i < vArr.length; i++) {
    if (mask[i]) {
      sum += vArr[i];
      count++;
    }
  }
  return count === 0 ? 0 : sum / count / 255;
}


// ── saturation ───────────────────────────────────────────────────────
/**
 * Mean S of masked pixels, normalised 0-1.
 */
function meanSaturation(sArr, mask) {
  let sum = 0;
  let count = 0;
  for (let i = 0; i < sArr.length; i++) {
    if (mask[i]) {
      sum += sArr[i];
      count++;
    }
  }
  return count === 0 ? 0 : sum / count / 255;
}


// ── motion ───────────────────────────────────────────────────────────
/**
 * Mean absolute pixel difference between current and previous frame,
 * using the V channel only.  Returns 0 if prevVArr is null.
 * Normalised 0-1.
 */
function motionScore(vArr, prevVArr) {
  if (!prevVArr || prevVArr.length !== vArr.length) return 0;
  let sum = 0;
  for (let i = 0; i < vArr.length; i++) {
    sum += Math.abs(vArr[i] - prevVArr[i]);
  }
  return sum / vArr.length / 255;
}


// ── strip (column) helpers ───────────────────────────────────────────
/**
 * Returns an array of N_STRIPS { start, end } column ranges.
 */
function stripSlices(width) {
  const slices = [];
  for (let i = 0; i < N_STRIPS; i++) {
    const start = Math.round((i / N_STRIPS) * width);
    const end   = Math.round(((i + 1) / N_STRIPS) * width);
    slices.push({ start, end });
  }
  return slices;
}

/**
 * Extract a flat sub-array for pixels in columns [colStart, colEnd).
 * Returns { hSlice, sSlice, vSlice, maskSlice } as plain arrays.
 *
 * We iterate row by row so that we can also compute height-weighted sums
 * efficiently later.
 */
function getStripData(hArr, sArr, vArr, mask, width, height, colStart, colEnd) {
  const stripW = colEnd - colStart;
  const n = stripW * height;
  const hSlice    = new Uint8Array(n);
  const sSlice    = new Uint8Array(n);
  const vSlice    = new Uint8Array(n);
  const maskSlice = new Uint8Array(n);

  for (let row = 0; row < height; row++) {
    for (let col = colStart; col < colEnd; col++) {
      const srcIdx = row * width + col;
      const dstIdx = row * stripW + (col - colStart);
      hSlice[dstIdx]    = hArr[srcIdx];
      sSlice[dstIdx]    = sArr[srcIdx];
      vSlice[dstIdx]    = vArr[srcIdx];
      maskSlice[dstIdx] = mask[srcIdx];
    }
  }

  return { hSlice, sSlice, vSlice, maskSlice, stripW };
}

/**
 * Per-strip brightness: mean V of masked pixels, normalised 0-1.
 */
function stripBrightness(vSlice, maskSlice) {
  let sum = 0;
  let count = 0;
  for (let i = 0; i < maskSlice.length; i++) {
    if (maskSlice[i]) {
      sum += vSlice[i];
      count++;
    }
  }
  return count === 0 ? 0 : sum / count / 255;
}

/**
 * Brightness-weighted average row position for a strip.
 * 0 = bottom, 1 = top.  Returns 0.5 if the strip is fully dark.
 *
 * stripW is the number of columns in this strip.
 * height is the total number of rows.
 */
function stripHeight(vSlice, maskSlice, stripW, height) {
  let weightedRowSum = 0;
  let totalWeight = 0;

  for (let row = 0; row < height; row++) {
    for (let col = 0; col < stripW; col++) {
      const i = row * stripW + col;
      if (maskSlice[i]) {
        const w = vSlice[i];
        weightedRowSum += row * w;
        totalWeight += w;
      }
    }
  }

  if (totalWeight === 0) return 0.5;
  const meanRow = weightedRowSum / totalWeight;
  return 1 - meanRow / (height - 1); // 0 = bottom, 1 = top
}


// ── main exported function ───────────────────────────────────────────
/**
 * Extract visual features from one frame.
 *
 * @param {ImageData} imageData      - Current frame pixels (from canvas.getImageData)
 * @param {ImageData|null} prevImageData - Previous frame pixels, or null for first frame
 * @returns {Object} Feature dict matching the JSON schema:
 *   {
 *     brightness,       // 0-1
 *     hue,              // 0-360 degrees
 *     saturation,       // 0-1
 *     motion,           // 0-1
 *     columns[32],      // 0-1 per strip
 *     column_heights[32],// 0-1 per strip (0=bottom, 1=top)
 *     column_hues[32],  // 0-360 per strip
 *   }
 */
export function extractFeatures(imageData, prevImageData = null) {
  const { width, height } = imageData;

  // ── build HSV arrays ──
  const { hArr, sArr, vArr } = imageDataToHsv(imageData);
  const mask = buildMask(vArr);

  // ── build previous V array for motion (if available) ──
  let prevVArr = null;
  if (prevImageData) {
    const prev = imageDataToHsv(prevImageData);
    prevVArr = prev.vArr;
  }

  // ── scalar features ──
  const brightness = meanBrightness(vArr, mask);
  const saturation = meanSaturation(sArr, mask);
  const hue        = dominantHue(hArr, sArr, mask);
  const motion     = motionScore(vArr, prevVArr);

  // ── strip features ──
  const slices        = stripSlices(width);
  const columns       = [];
  const columnHeights = [];
  const columnHues    = [];

  for (const { start, end } of slices) {
    const { hSlice, sSlice, vSlice, maskSlice, stripW } =
      getStripData(hArr, sArr, vArr, mask, width, height, start, end);

    columns.push(stripBrightness(vSlice, maskSlice));
    columnHeights.push(stripHeight(vSlice, maskSlice, stripW, height));

    // Strip hue: use frame hue as fallback if strip is fully dark
    const anyMasked = maskSlice.some(v => v === 1);
    columnHues.push(anyMasked ? dominantHue(hSlice, sSlice, maskSlice) : hue);
  }

  return {
    brightness,
    hue,
    saturation,
    motion,
    columns,
    column_heights: columnHeights,
    column_hues:    columnHues,
  };
}


// ── convenience: analyse a <video> frame via an offscreen canvas ─────
/**
 * Draw the current video frame onto an offscreen canvas and extract
 * features.  Call this inside a requestAnimationFrame / timeupdate loop.
 *
 * @param {HTMLVideoElement} video     - The playing video element
 * @param {OffscreenCanvas|HTMLCanvasElement} canvas - Offscreen canvas (downscaled)
 * @param {ImageData|null} prevImageData - Previous frame's ImageData (or null)
 * @returns {{ features: Object, imageData: ImageData }}
 *
 * Example:
 *   const canvas = new OffscreenCanvas(640, 360);
 *   let prevImageData = null;
 *
 *   video.addEventListener('timeupdate', () => {
 *     const { features, imageData } = analyzeVideoFrame(video, canvas, prevImageData);
 *     prevImageData = imageData;
 *     synth.playFrame(features);
 *   });
 */
export function analyzeVideoFrame(video, canvas, prevImageData = null) {
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const features  = extractFeatures(imageData, prevImageData);
  return { features, imageData };
}


// ── round helper (matches Python's round-to-3-decimals) ─────────────
/**
 * Round all scalar and array values in a feature dict to 3 decimals.
 * Useful if you want to log or display values without floating-point noise.
 */
export function roundFeatures(f) {
  const r = v => Math.round(v * 1000) / 1000;
  return {
    brightness:     r(f.brightness),
    hue:            r(f.hue),
    saturation:     r(f.saturation),
    motion:         r(f.motion),
    columns:        f.columns.map(r),
    column_heights: f.column_heights.map(r),
    column_hues:    f.column_hues.map(r),
  };
}
