/**
 * features.js — Browser-side visual feature extraction
 *
 * Port of vision/measure_features.py for the "Bring Your Own Earth" mode.
 * Takes raw ImageData from a <canvas> and returns the same feature shape
 * as the pre-computed JSON files:
 *
 *   { brightness, hue, saturation, edges, motion,
 *     columns[32], column_heights[32], column_hues[32] }
 *
 * Usage:
 *   import { extractFeatures } from './features.js';
 *
 *   const ctx = canvas.getContext('2d');
 *   ctx.drawImage(video, 0, 0, 640, 360);
 *   const imageData = ctx.getImageData(0, 0, 640, 360);
 *
 *   const { features, gray } = extractFeatures(imageData, 640, 360);
 *   // On the next frame, pass gray back as prevGray:
 *   const next = extractFeatures(nextImageData, 640, 360, gray);
 */

// ── constants ────────────────────────────────────────────────────────

export const N_STRIPS = 32;
const DARK_V = 30;          // pixels with V < 30 are masked out
const HUE_BINS = 180;       // matches OpenCV's 0-179 hue range
const EDGE_THRESHOLD = 30;  // gradient magnitude for simple edge detection

// ── RGB → HSV (OpenCV convention) ────────────────────────────────────
// Returns [h 0-179, s 0-255, v 0-255], same scale as OpenCV so the
// hue histogram logic matches the Python version exactly.

function rgbToHsv(r, g, b) {
  const v = Math.max(r, g, b);
  const diff = v - Math.min(r, g, b);

  if (diff === 0) return [0, 0, v];

  const s = Math.round((diff / v) * 255);
  let h;
  if (v === r)      h = 30 * ((g - b) / diff);          // 0-60° → 0-30
  else if (v === g)  h = 30 * ((b - r) / diff) + 60;     // 60-120° → 30-60
  else               h = 30 * ((r - g) / diff) + 120;    // 120-180° → 60-90

  if (h < 0) h += 180;
  return [Math.round(h), s, v];
}

// ── main API ─────────────────────────────────────────────────────────
/**
 * Extract visual features from one video frame.
 *
 * @param {ImageData} imageData  - from canvas.getImageData()
 * @param {number}    width      - frame width  (should be ~640 for speed)
 * @param {number}    height     - frame height (should be ~360 for speed)
 * @param {Uint8Array|null} prevGray - grayscale of previous frame (for motion)
 * @returns {{ features: Object, gray: Uint8Array }}
 */
export function extractFeatures(imageData, width, height, prevGray = null) {
  const px = imageData.data;          // Uint8ClampedArray [R,G,B,A, …]
  const numPixels = width * height;

  // ── 1. Convert every pixel to HSV + grayscale + mask ───────────
  const hArr = new Uint8Array(numPixels);   // hue   0-179
  const sArr = new Uint8Array(numPixels);   // sat   0-255
  const vArr = new Uint8Array(numPixels);   // value 0-255
  const gray = new Uint8Array(numPixels);   // BT.601 luma
  const mask = new Uint8Array(numPixels);   // 1 = keep

  for (let i = 0; i < numPixels; i++) {
    const off = i * 4;
    const r = px[off], g0 = px[off + 1], b = px[off + 2];

    // grayscale (BT.601 weights, same as OpenCV cvtColor)
    gray[i] = (0.299 * r + 0.587 * g0 + 0.114 * b + 0.5) | 0;

    const [h, s, v] = rgbToHsv(r, g0, b);
    hArr[i] = h;
    sArr[i] = s;
    vArr[i] = v;

    mask[i] = v >= DARK_V ? 1 : 0;
  }

  // ── 2. Scalar features ─────────────────────────────────────────

  // brightness: mean V of masked pixels, /255
  let vSum = 0, maskCount = 0;
  for (let i = 0; i < numPixels; i++) {
    if (mask[i]) { vSum += vArr[i]; maskCount++; }
  }
  const brightness = maskCount > 0 ? vSum / maskCount / 255 : 0;

  // saturation: mean S of masked pixels, /255
  let sSum = 0;
  for (let i = 0; i < numPixels; i++) {
    if (mask[i]) sSum += sArr[i];
  }
  const saturation = maskCount > 0 ? sSum / maskCount / 255 : 0;

  // hue: saturation-weighted histogram, peak bin × 2 → degrees
  const hueHist = new Float64Array(HUE_BINS);
  for (let i = 0; i < numPixels; i++) {
    if (mask[i]) hueHist[hArr[i]] += sArr[i];
  }
  let peakBin = 0, peakVal = 0;
  for (let b = 0; b < HUE_BINS; b++) {
    if (hueHist[b] > peakVal) { peakVal = hueHist[b]; peakBin = b; }
  }
  const hue = peakBin * 2;   // 0-360°

  // edges: simple gradient magnitude (Sobel-like, no Canny)
  let edgeCount = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const idx = y * width + x;
      const gx = Math.abs(gray[idx + 1] - gray[idx - 1]);
      const gy = Math.abs(gray[idx + width] - gray[idx - width]);
      if (gx + gy > EDGE_THRESHOLD) edgeCount++;
    }
  }
  const edges = edgeCount / ((width - 2) * (height - 2));

  // motion: mean |current − previous| / 255
  let motion = 0;
  if (prevGray) {
    let diffSum = 0;
    for (let i = 0; i < numPixels; i++) {
      diffSum += Math.abs(gray[i] - prevGray[i]);
    }
    motion = diffSum / numPixels / 255;
  }

  // ── 3. Strip (column) features ─────────────────────────────────
  const columns      = new Array(N_STRIPS);
  const columnHeights = new Array(N_STRIPS);
  const columnHues    = new Array(N_STRIPS);

  // Pre-compute strip edges (same as Python np.linspace)
  const stripEdges = new Array(N_STRIPS + 1);
  for (let i = 0; i <= N_STRIPS; i++) {
    stripEdges[i] = Math.round(i * width / N_STRIPS);
  }

  for (let s = 0; s < N_STRIPS; s++) {
    const x0 = stripEdges[s];
    const x1 = stripEdges[s + 1];

    // ── strip brightness ──
    let stripV = 0, stripMask = 0;
    for (let y = 0; y < height; y++) {
      for (let x = x0; x < x1; x++) {
        const idx = y * width + x;
        if (mask[idx]) { stripV += vArr[idx]; stripMask++; }
      }
    }
    columns[s] = stripMask > 0 ? stripV / stripMask / 255 : 0;

    // ── strip height: brightness-weighted row position ──
    //    0 = bottom, 1 = top; 0.5 if no signal
    if (stripMask === 0) {
      columnHeights[s] = 0.5;
    } else {
      let weightedRow = 0, totalWeight = 0;
      for (let y = 0; y < height; y++) {
        let rowWeight = 0;
        for (let x = x0; x < x1; x++) {
          const idx = y * width + x;
          if (mask[idx]) rowWeight += vArr[idx];
        }
        weightedRow += y * rowWeight;
        totalWeight += rowWeight;
      }
      const meanRow = weightedRow / totalWeight;
      columnHeights[s] = 1 - meanRow / (height - 1);
    }

    // ── strip hue: saturation-weighted histogram ──
    if (stripMask === 0) {
      columnHues[s] = hue;  // fallback to frame hue
    } else {
      const stripHist = new Float64Array(HUE_BINS);
      for (let y = 0; y < height; y++) {
        for (let x = x0; x < x1; x++) {
          const idx = y * width + x;
          if (mask[idx]) stripHist[hArr[idx]] += sArr[idx];
        }
      }
      let sPeak = 0, sPeakVal = 0;
      for (let b = 0; b < HUE_BINS; b++) {
        if (stripHist[b] > sPeakVal) { sPeakVal = stripHist[b]; sPeak = b; }
      }
      columnHues[s] = sPeak * 2;
    }
  }

  // ── 4. Assemble output ─────────────────────────────────────────
  const features = {
    brightness: round3(brightness),
    hue,
    saturation: round3(saturation),
    edges:  round3(edges),
    motion: round3(motion),
    columns:        columns.map(round3),
    column_heights: columnHeights.map(round3),
    column_hues:    columnHues.map(v => Math.round(v)),
  };

  return { features, gray };
}


// ── helpers ──────────────────────────────────────────────────────────

function round3(v) {
  return Math.round(v * 1000) / 1000;
}


// ── batch mode (optional) ────────────────────────────────────────────
/**
 * Process a whole video and return an array of feature objects.
 * Useful for pre-scanning a dropped video before playback.
 *
 * @param {HTMLVideoElement} video - loaded video element
 * @param {HTMLCanvasElement} canvas - offscreen canvas for frame capture
 * @param {number} fps - frames per second to sample (default 1)
 * @param {function} onProgress - callback(fractionComplete) for UI
 * @returns {Promise<Object[]>} array of feature dicts
 */
export async function extractAllFrames(video, canvas, fps = 1, onProgress = null) {
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const w = canvas.width;
  const h = canvas.height;
  const duration = video.duration;
  const step = 1 / fps;
  const frames = [];
  let prevGray = null;

  for (let t = 0; t < duration; t += step) {
    // seek to time t
    video.currentTime = t;
    await new Promise(resolve => {
      video.onseeked = resolve;
    });

    ctx.drawImage(video, 0, 0, w, h);
    const imageData = ctx.getImageData(0, 0, w, h);
    const { features, gray } = extractFeatures(imageData, w, h, prevGray);
    features.t = Math.round(t);
    frames.push(features);
    prevGray = gray;

    if (onProgress) onProgress(t / duration);
  }

  return frames;
}
