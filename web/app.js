// Earth Jukebox — Planetary Data Sonification Console
// Audio-visual spectrogram coordination, probe mechanics, and spatialization.

import { extractFeatures } from "./features.js";
import { playFrame, stopAll, setMasterVolume, setMuted, getAudioContext } from "./synth.js";

/* ════════════════════════════════════════════════════════════
   0. State Management
════════════════════════════════════════════════════════════ */
const state = {
  currentRecord: null,     // Object from RECORDS or custom BYO record
  isCustomMode: false,     // True when Bring Your Own Earth mode is active
  customFile: null,        // Uploaded File object
  customVideoUrl: null,    // ObjectURL for user-provided video
  features: null,          // Parsed features.json telemetry or live frame features
  liveFrames: [],          // Extracted frames for custom video
  prevGray: null,          // Grayscale luma buffer for motion calculation
  lastExtractedSecond: -1, // Throttle to 1 fps frame extraction during video playback
  customViewMode: "overlay", // "overlay" | "video" | "spectrogram"
  isPlaying: false,
  isMuted: false,
  isProbeMode: false,
  isThenVsNow: false,
  volumeBeforeMute: 1,
  animFrameId: null,
  needleRafId: null,
  probeAudioCtx: null,     // Web Audio API context for coordinate probing
  probeOscillator: null,
  probeGain: null,
};

/* ════════════════════════════════════════════════════════════
   1. DOM Element Cache
════════════════════════════════════════════════════════════ */
const shelf             = document.getElementById("shelf");
const playerPanel       = document.getElementById("player-panel");
const playerStage       = document.getElementById("player-stage");
const vizCanvas         = document.getElementById("viz-canvas");
const needle            = document.getElementById("needle");
const probeTooltip      = document.getElementById("probe-tooltip");
const btnPlay           = document.getElementById("btn-play");
const btnMute           = document.getElementById("btn-mute");
const btnTvn            = document.getElementById("btn-tvn");
const btnProbe          = document.getElementById("btn-probe");
const progressBar       = document.getElementById("progress-bar");
const volumeBar         = document.getElementById("volume-bar");
const timeCurrent       = document.getElementById("time-current");
const timeTotal         = document.getElementById("time-total");
const playerTitle       = document.getElementById("player-title");
const playerSubtitle    = document.getElementById("player-subtitle");
const playerTag         = document.getElementById("player-tag");
const hudStatus         = document.getElementById("hud-status");
const catalogCount      = document.getElementById("catalog-count");

const descPanel         = document.getElementById("desc-panel");
const descTag           = document.getElementById("desc-tag");
const descCatalog       = document.getElementById("desc-catalog");
const descTitle         = document.getElementById("desc-title");
const descInstrument    = document.getElementById("desc-instrument");
const descRole          = document.getElementById("desc-role");
const descBody          = document.getElementById("desc-body");
const descSound         = document.getElementById("desc-sound");

const tvnBanner         = document.getElementById("tvn-banner");
const shortcutsOverlay  = document.getElementById("shortcuts-overlay");
const btnCloseShortcuts = document.getElementById("btn-close-shortcuts");
const btnHelp           = document.getElementById("btn-help");
const footerShortcutsBtn = document.getElementById("footer-shortcuts-btn");

/* Bring Your Own Earth custom elements */
const byoVideo          = document.getElementById("byo-video");
const byoFileInput      = document.getElementById("byo-file-input");
const btnViewToggle     = document.getElementById("btn-view-toggle");

/* Dedicated offscreen canvas for 640x360 downsampling */
const offscreenCanvas   = document.createElement("canvas");
offscreenCanvas.width   = 640;
offscreenCanvas.height  = 360;
const offscreenCtx      = offscreenCanvas.getContext("2d", { willReadFrequently: true });

/* Hidden HTML5 Audio pipelines */
const audioMain = new Audio();
const audioTvn  = new Audio();

audioMain.preload = "metadata";
audioMain.loop    = true;
audioTvn.preload  = "metadata";
audioTvn.loop     = true;

/* Visualizer 2D context */
let ctx2d = null;

/* ════════════════════════════════════════════════════════════
   2. Mathematical & Formatting Utilities
════════════════════════════════════════════════════════════ */
function fmt(secs) {
  if (!isFinite(secs) || secs < 0) return "00:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function hueToCategory(hue) {
  if (hue <= 65 || hue >= 315) return "WARM (STRING/BRASS)";
  if (hue >= 170 && hue <= 265) return "COOL (RESONANT PAD)";
  return "NEUTRAL (ORGANIC)";
}

function hueToRgb(hue, brightness) {
  const l = 16 + brightness * 54;
  return `hsl(${hue}, 75%, ${l}%)`;
}

/* ════════════════════════════════════════════════════════════
   3. A-Minor Pentatonic Probe Engine (Web Audio API)
════════════════════════════════════════════════════════════ */
// A-minor pentatonic intervals across 2 octaves: A3, C4, D4, E4, G4, A4, C5, D5, E5, G5, A5
const PENTA_FREQS = [
  220.00, 261.63, 293.66, 329.63, 392.00,
  440.00, 523.25, 587.33, 659.25, 783.99, 880.00
];

function getProbeCtx() {
  if (!state.probeAudioCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    state.probeAudioCtx = new AudioCtx();
  }
  return state.probeAudioCtx;
}

function playProbeTone(brightness, height, panX, chordMode = "single") {
  try {
    const actx = getProbeCtx();
    if (actx.state === "suspended") {
      actx.resume();
    }

    if (state.probeOscillator) {
      try { state.probeOscillator.stop(); } catch (_) {}
    }
    if (state.probeOscillator2) {
      try { state.probeOscillator2.stop(); } catch (_) {}
    }

    // Latitude maps inversely to pitch register
    const noteIdx = Math.round(clamp(height, 0, 1) * (PENTA_FREQS.length - 1));
    const freq    = PENTA_FREQS[noteIdx];
    const vol     = clamp(brightness * 0.45, 0.04, 0.5);

    const osc  = actx.createOscillator();
    const gain = actx.createGain();
    const pan  = actx.createStereoPanner ? actx.createStereoPanner() : null;

    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, actx.currentTime);

    // Smooth envelope attack / decay to eliminate digital clicks
    gain.gain.setValueAtTime(0.001, actx.currentTime);
    gain.gain.exponentialRampToValueAtTime(vol, actx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.38);

    if (pan) {
      pan.pan.setValueAtTime(clamp(panX, -1, 1), actx.currentTime);
      osc.connect(gain);
      gain.connect(pan);
      pan.connect(actx.destination);
    } else {
      osc.connect(gain);
      gain.connect(actx.destination);
    }

    osc.start(actx.currentTime);
    osc.stop(actx.currentTime + 0.4);
    state.probeOscillator = osc;

    // Dual-epoch comparison chord generation
    if (chordMode === "harmonic" || chordMode === "dissonant") {
      const osc2 = actx.createOscillator();
      const gain2 = actx.createGain();
      // Harmonic = perfect 5th (x1.5); Dissonant = minor 2nd clash (x1.05946 - 1 semitone apart)
      const freq2 = chordMode === "harmonic" ? freq * 1.5 : freq * 1.05946;
      osc2.type = chordMode === "harmonic" ? "sine" : "sawtooth";
      osc2.frequency.setValueAtTime(freq2, actx.currentTime);

      gain2.gain.setValueAtTime(0.001, actx.currentTime);
      gain2.gain.exponentialRampToValueAtTime(vol * 0.75, actx.currentTime + 0.02);
      gain2.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + 0.38);

      if (pan) {
        osc2.connect(gain2);
        gain2.connect(pan);
      } else {
        osc2.connect(gain2);
        gain2.connect(actx.destination);
      }
      osc2.start(actx.currentTime);
      osc2.stop(actx.currentTime + 0.4);
      state.probeOscillator2 = osc2;
    }
  } catch (err) {
    console.debug("Web Audio probe synthesis paused:", err);
  }
}

/* ════════════════════════════════════════════════════════════
   4. Spectrogram Visualizer Canvas
════════════════════════════════════════════════════════════ */
function initCanvas() {
  ctx2d = vizCanvas.getContext("2d");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

function resizeCanvas() {
  vizCanvas.width  = playerStage.clientWidth;
  vizCanvas.height = playerStage.clientHeight;
  if (state.features) {
    drawViz(state.features);
  }
}

function drawViz(features) {
  if (!ctx2d) return;
  const W = vizCanvas.width;
  const H = vizCanvas.height;
  ctx2d.clearRect(0, 0, W, H);

  const frames = features.frames;
  if (!frames || frames.length === 0) return;

  const isTvnActive = state.isThenVsNow && state.currentRecord?.id === "night_lights";

  if (isTvnActive) {
    // ════════════════════════════════════════════════════════════
    // DUAL-EPOCH COMPARISON VIEW (Then vs Now)
    // Stacked Tracks: Top = 2012 Baseline | Bottom = 2023 Delta
    // ════════════════════════════════════════════════════════════
    const midY = Math.floor(H / 2);
    const baselineFrames = frames.slice(0, 10);
    const deltaFrames    = frames.slice(10);
    const nStrips        = frames[0].columns.length;

    // Track 1: 2012 Baseline (Top Half)
    const colW1 = W / baselineFrames.length;
    for (let f = 0; f < baselineFrames.length; f++) {
      const frame = baselineFrames[f];
      const x = f * colW1;
      for (let s = 0; s < nStrips; s++) {
        const brightness = frame.columns[s];
        const hue = frame.column_hues ? frame.column_hues[s] : frame.hue;
        const y = (s / nStrips) * midY;
        const rH = midY / nStrips + 0.8;
        ctx2d.fillStyle = hueToRgb(hue, brightness);
        ctx2d.globalAlpha = clamp(brightness * 0.95 + 0.08, 0.05, 1.0);
        ctx2d.fillRect(Math.floor(x), Math.floor(y), Math.ceil(colW1) + 0.8, Math.ceil(rH));
      }
    }

    // Track 2: 2023 Delta & Growth/Loss (Bottom Half)
    const colW2 = W / deltaFrames.length;
    for (let f = 0; f < deltaFrames.length; f++) {
      const frame = deltaFrames[f];
      const x = f * colW2;
      for (let s = 0; s < nStrips; s++) {
        const brightness = frame.columns[s];
        const hue = frame.column_hues ? frame.column_hues[s] : frame.hue;
        const y = midY + (s / nStrips) * (H - midY);
        const rH = (H - midY) / nStrips + 0.8;
        ctx2d.fillStyle = hueToRgb(hue, brightness);
        ctx2d.globalAlpha = clamp(brightness * 0.95 + 0.08, 0.05, 1.0);
        ctx2d.fillRect(Math.floor(x), Math.floor(y), Math.ceil(colW2) + 0.8, Math.ceil(rH));
      }
    }

    // Divider Line between 2012 and 2023
    ctx2d.globalAlpha = 1.0;
    ctx2d.strokeStyle = "#f59e0b";
    ctx2d.lineWidth = 1.5;
    ctx2d.beginPath();
    ctx2d.moveTo(0, midY);
    ctx2d.lineTo(W, midY);
    ctx2d.stroke();

    // HUD Track Identification Badges
    ctx2d.fillStyle = "#fbbf24";
    ctx2d.font = "bold 9px 'JetBrains Mono', monospace";
    ctx2d.fillText("TRACK 01 // 2012 BASELINE (HARMONIC FOUNDATION)", 12, 14);

    ctx2d.fillStyle = "#c084fc";
    ctx2d.fillText("TRACK 02 // 2023 DELTA (PURPLE = GROWTH CHORD / ORANGE = DISSONANCE)", 12, midY + 14);

  } else {
    // Normal single-timeline rendering
    const nFrames = frames.length;
    const nStrips = frames[0].columns.length;
    const colW    = W / nFrames;

    for (let f = 0; f < nFrames; f++) {
      const frame = frames[f];
      const x     = f * colW;

      for (let s = 0; s < nStrips; s++) {
        const brightness = frame.columns[s];
        const hue        = frame.column_hues ? frame.column_hues[s] : frame.hue;

        const y  = (s / nStrips) * H;
        const rH = H / nStrips + 0.8;

        ctx2d.fillStyle = hueToRgb(hue, brightness);
        ctx2d.globalAlpha = clamp(brightness * 0.95 + 0.06, 0.04, 1.0);
        ctx2d.fillRect(Math.floor(x), Math.floor(y), Math.ceil(colW) + 0.8, Math.ceil(rH));
      }
    }

    // Draw discrete coordinate grid lines
    ctx2d.globalAlpha = 0.15;
    ctx2d.strokeStyle = "#ffffff";
    ctx2d.lineWidth = 1;
    const gridLines = 4;
    for (let i = 1; i < gridLines; i++) {
      const gy = (H / gridLines) * i;
      ctx2d.beginPath();
      ctx2d.moveTo(0, gy);
      ctx2d.lineTo(W, gy);
      ctx2d.stroke();
    }
  }

  ctx2d.globalAlpha = 1.0;
}

/* ════════════════════════════════════════════════════════════
   5. Sweep Needle Synchronization
════════════════════════════════════════════════════════════ */
function getActiveAudio() {
  return (state.isThenVsNow && state.currentRecord?.thenVsNowAudio) ? audioTvn : audioMain;
}

function getInactiveAudio() {
  return (state.isThenVsNow && state.currentRecord?.thenVsNowAudio) ? audioMain : audioTvn;
}

function updateNeedle() {
  const W = playerStage.clientWidth;
  if (state.isCustomMode) {
    if (!byoVideo.duration) return;
    const pct = byoVideo.currentTime / byoVideo.duration;
    needle.style.left = `${clamp(pct * W, 0, W - 1)}px`;
    return;
  }
  const active = getActiveAudio();
  if (!active || !active.duration) return;
  const pct = active.currentTime / active.duration;
  needle.style.left = `${clamp(pct * W, 0, W - 1)}px`;
}

function startNeedleLoop() {
  stopNeedleLoop();
  function loop() {
    updateNeedle();
    state.needleRafId = requestAnimationFrame(loop);
  }
  state.needleRafId = requestAnimationFrame(loop);
}

function stopNeedleLoop() {
  if (state.needleRafId) {
    cancelAnimationFrame(state.needleRafId);
    state.needleRafId = null;
  }
}

/* ════════════════════════════════════════════════════════════
   6. Touch the Earth Spatial Probe
════════════════════════════════════════════════════════════ */
function handleProbeEvent(e) {
  if (!state.isProbeMode || !state.features) return;

  const rect    = playerStage.getBoundingClientRect();
  const clientX = e.touches ? e.touches[0].clientX : e.clientX;
  const clientY = e.touches ? e.touches[0].clientY : e.clientY;

  const relX = clamp((clientX - rect.left) / rect.width, 0, 1);
  const relY = clamp((clientY - rect.top)  / rect.height, 0, 1);

  const frames  = state.features.frames;
  const nFrames = frames.length;
  const isTvnActive = state.isThenVsNow && state.currentRecord?.id === "night_lights";

  let brightness, height, panX, hue, category, noteIdx, noteFreq, chordMode = "single", trackBadge = "";

  if (isTvnActive) {
    const isBaseline = relY < 0.5;
    const baselineFrames = frames.slice(0, 10);
    const deltaFrames    = frames.slice(10);

    if (isBaseline) {
      trackBadge = "TRACK 01 // 2012 BASELINE";
      const fIdx = Math.min(Math.floor(relX * baselineFrames.length), baselineFrames.length - 1);
      const frame = baselineFrames[fIdx];
      const nStrips = frame.columns.length;
      const sIdx = Math.min(Math.floor((relY * 2) * nStrips), nStrips - 1);
      brightness = frame.columns[sIdx];
      height = 1 - (relY * 2);
      panX = relX * 2 - 1;
      hue = frame.column_hues ? frame.column_hues[sIdx] : frame.hue;
      category = "PURE HARMONIC";
      chordMode = "single";
    } else {
      const fIdx = Math.min(Math.floor(relX * deltaFrames.length), deltaFrames.length - 1);
      const frame = deltaFrames[fIdx];
      const nStrips = frame.columns.length;
      const sIdx = Math.min(Math.floor(((relY - 0.5) * 2) * nStrips), nStrips - 1);
      brightness = frame.columns[sIdx];
      height = 1 - ((relY - 0.5) * 2);
      panX = relX * 2 - 1;
      hue = frame.column_hues ? frame.column_hues[sIdx] : frame.hue;

      const isGrowth = frame.purple > 0.4 || (hue >= 240 && hue <= 320);
      if (isGrowth) {
        trackBadge = "TRACK 02 // 2023 GROWTH (+LUM)";
        category = "HARMONIC CHORD (GROWTH)";
        chordMode = "harmonic";
      } else {
        trackBadge = "TRACK 02 // 2023 DECLINE (-LUM)";
        category = "DISSONANT CLASH (LOSS)";
        chordMode = "dissonant";
      }
    }
  } else {
    const fIdx    = Math.min(Math.floor(relX * nFrames), nFrames - 1);
    const frame   = frames[fIdx];
    const nStrips = frame.columns.length;
    const sIdx    = Math.min(Math.floor(relY * nStrips), nStrips - 1);

    brightness = frame.columns[sIdx];
    height     = 1 - relY;
    panX       = relX * 2 - 1;
    hue        = frame.column_hues ? frame.column_hues[sIdx] : frame.hue;
    category   = hueToCategory(hue);
    trackBadge = `T+${String(frame.t).padStart(2, "0")}s`;
  }

  noteIdx  = Math.round(clamp(height, 0, 1) * (PENTA_FREQS.length - 1));
  noteFreq = PENTA_FREQS[noteIdx].toFixed(0);

  // Technical HUD tooltip formatting
  probeTooltip.innerHTML = `
    <span class="probe-hud__t">${trackBadge}</span>
    <span class="probe-hud__sep">|</span>
    <span class="probe-hud__cat">${category}</span>
    <span class="probe-hud__sep">|</span>
    <span class="probe-hud__freq">${noteFreq} Hz</span>
    <span class="probe-hud__sep">|</span>
    <span class="probe-hud__mag">MAG ${(brightness * 100).toFixed(0)}%</span>
  `;

  probeTooltip.style.left = `${clamp(clientX - rect.left, 24, rect.width - 280)}px`;
  probeTooltip.classList.add("visible");
  probeTooltip.setAttribute("aria-label", `${trackBadge}, category ${category}, frequency ${noteFreq} Hertz, magnitude ${Math.round(brightness * 100)} percent`);

  if (brightness > 0.02) {
    playProbeTone(brightness, height, panX, chordMode);
  }
}

function hideProbeTooltip() {
  probeTooltip.classList.remove("visible");
}

function attachProbeListeners() {
  playerStage.addEventListener("mousemove", handleProbeEvent);
  playerStage.addEventListener("touchstart", handleProbeEvent, { passive: true });
  playerStage.addEventListener("mouseleave", hideProbeTooltip);
  playerStage.addEventListener("touchend", hideProbeTooltip);
}

function detachProbeListeners() {
  playerStage.removeEventListener("mousemove", handleProbeEvent);
  playerStage.removeEventListener("touchstart", handleProbeEvent);
  playerStage.removeEventListener("mouseleave", hideProbeTooltip);
  playerStage.removeEventListener("touchend", hideProbeTooltip);
  hideProbeTooltip();
}

/* ════════════════════════════════════════════════════════════
   7. Audio Pipeline Synchronization
════════════════════════════════════════════════════════════ */
function syncVolume() {
  const v = state.isMuted ? 0 : parseFloat(volumeBar.value);
  audioMain.volume = v;
  audioTvn.volume  = v;
  setMasterVolume(v);
  setMuted(state.isMuted);
  updateMuteIcon();
}

function updateMuteIcon() {
  btnMute.classList.toggle("is-muted", state.isMuted);
  btnMute.setAttribute("aria-label", state.isMuted ? "Unmute audio" : "Mute audio");
}

function syncProgress() {
  if (state.isCustomMode) {
    if (!byoVideo.duration) return;
    const pct = (byoVideo.currentTime / byoVideo.duration) * 100;
    progressBar.value = pct;
    timeCurrent.textContent = fmt(byoVideo.currentTime);
    timeTotal.textContent   = fmt(byoVideo.duration);
    return;
  }
  const active = getActiveAudio();
  if (!active || !active.duration) return;
  const pct = (active.currentTime / active.duration) * 100;
  progressBar.value = pct;
  timeCurrent.textContent = fmt(active.currentTime);
  timeTotal.textContent   = fmt(active.duration);
}

audioMain.addEventListener("timeupdate", () => {
  if (!state.isThenVsNow && !state.isCustomMode) syncProgress();
});
audioTvn.addEventListener("timeupdate", () => {
  if (state.isThenVsNow && !state.isCustomMode) syncProgress();
});

audioMain.addEventListener("loadedmetadata", () => {
  if (!state.isThenVsNow && !state.isCustomMode) {
    timeTotal.textContent = fmt(audioMain.duration);
    progressBar.max = 100;
  }
});
audioTvn.addEventListener("loadedmetadata", () => {
  if (state.isThenVsNow && !state.isCustomMode) {
    timeTotal.textContent = fmt(audioTvn.duration);
    progressBar.max = 100;
  }
});

audioMain.addEventListener("ended", () => {
  if (!state.isThenVsNow && !state.isCustomMode) {
    state.isPlaying = false;
    updatePlayBtn();
    stopNeedleLoop();
  }
});
audioTvn.addEventListener("ended", () => {
  if (state.isThenVsNow && !state.isCustomMode) {
    state.isPlaying = false;
    updatePlayBtn();
    stopNeedleLoop();
  }
});

/* Bring Your Own Earth video synchronization */
byoVideo.addEventListener("timeupdate", () => {
  if (!state.isCustomMode) return;
  syncProgress();
  const sec = Math.floor(byoVideo.currentTime);
  if (sec !== state.lastExtractedSecond && byoVideo.readyState >= 2) {
    state.lastExtractedSecond = sec;
    extractAndSonifyCurrentFrame(sec);
  }
});

byoVideo.addEventListener("ended", () => {
  if (state.isCustomMode) {
    state.isPlaying = false;
    updatePlayBtn();
    stopNeedleLoop();
    stopAll();
    if (hudStatus) hudStatus.textContent = "TRANSMISSION COMPLETE";
  }
});

/* ════════════════════════════════════════════════════════════
   8. Record Mount & Playback Control
════════════════════════════════════════════════════════════ */
async function loadRecord(record) {
  if (state.isCustomMode) {
    state.isCustomMode = false;
    byoVideo.pause();
    byoVideo.hidden = true;
    btnViewToggle.hidden = true;
    playerStage.className = "player-stage";
    stopAll();
    document.getElementById("card-byo")?.classList.remove("active");
  }

  if (state.currentRecord) {
    document.getElementById(`card-${state.currentRecord.id}`)?.classList.remove("active");
  }

  audioMain.pause();
  audioTvn.pause();
  state.isPlaying   = false;
  state.isThenVsNow = false;
  stopNeedleLoop();

  state.currentRecord = record;

  const card = document.getElementById(`card-${record.id}`);
  card?.classList.add("active");

  // Telemetry display updates
  playerTitle.textContent    = record.title.toUpperCase();
  playerSubtitle.textContent = record.subtitle;
  playerTag.textContent      = record.tag;
  if (hudStatus) hudStatus.textContent = "SYNCHRONIZING";

  // Dossier metadata injection
  if (descTag)        descTag.textContent = record.tag;
  if (descCatalog)    descCatalog.textContent = record.catalog;
  if (descTitle)      descTitle.textContent = record.title;
  if (descInstrument) descInstrument.textContent = record.instrument || "Observation Payload";
  if (descRole)       descRole.textContent = record.soundRole || "Acoustic Mapping";
  if (descBody)       descBody.textContent = record.description;
  if (descSound)      descSound.textContent = record.soundDescription;
  descPanel.classList.add("visible");

  // Epoch comparative button visibility
  btnTvn.hidden = !record.thenVsNow;
  btnTvn.setAttribute("aria-pressed", "false");
  btnTvn.classList.remove("active");
  tvnBanner.classList.remove("visible");

  playerPanel.classList.remove("hidden");

  // Reset time scrubbers
  progressBar.value       = 0;
  timeCurrent.textContent = "00:00";
  timeTotal.textContent   = "00:00";
  needle.style.left       = "0px";

  // Load telemetry features
  try {
    let res = await fetch(record.features);
    if (!res.ok) {
      const alt = record.features.startsWith("../") ? record.features.slice(3) : ("../" + record.features);
      res = await fetch(alt);
    }
    state.features = await res.json();
    drawViz(state.features);
    if (hudStatus) hudStatus.textContent = "SPECTRAL LOCK";
  } catch (err) {
    console.warn("Unable to fetch telemetry JSON:", err);
    state.features = null;
    if (ctx2d) ctx2d.clearRect(0, 0, vizCanvas.width, vizCanvas.height);
    if (hudStatus) hudStatus.textContent = "TELEMETRY OFFLINE";
  }

  // Mount audio pipelines
  audioMain.src = record.audio;
  audioMain.onerror = () => {
    if (audioMain.src.endsWith(".mp3")) {
      audioMain.src = audioMain.src.replace(/\.mp3$/, ".wav");
    }
  };
  audioMain.load();

  if (record.thenVsNow && record.thenVsNowAudio) {
    audioTvn.src = record.thenVsNowAudio;
    audioTvn.onerror = () => {
      if (audioTvn.src.endsWith(".mp3")) {
        audioTvn.src = audioTvn.src.replace(/\.mp3$/, ".wav");
      }
    };
    audioTvn.load();
  }

  play();
}

function play() {
  if (state.isCustomMode) {
    getAudioContext();
    byoVideo.play().then(() => {
      state.isPlaying = true;
      updatePlayBtn();
      startNeedleLoop();
      if (hudStatus) hudStatus.textContent = "OPTICAL TRACKING ACTIVE";
    }).catch(err => {
      console.info("Custom video play paused pending user interaction:", err);
      state.isPlaying = false;
      updatePlayBtn();
      if (hudStatus) hudStatus.textContent = "READY FOR PLAYBACK";
    });
    return;
  }

  const active = getActiveAudio();
  const inactive = getInactiveAudio();
  inactive.pause();

  const playPromise = active.play();
  if (playPromise !== undefined) {
    playPromise
      .then(() => {
        state.isPlaying = true;
        updatePlayBtn();
        startNeedleLoop();
        if (hudStatus) {
          hudStatus.textContent = state.isThenVsNow ? "DUAL-EPOCH COMPARISON" : "SWEEP ACTIVE";
        }
      })
      .catch((err) => {
        // Browser autoplay policy or missing local mp3
        console.info("Acoustic playback paused pending user interaction or file load.", err);
        state.isPlaying = false;
        updatePlayBtn();
        if (hudStatus) hudStatus.textContent = "READY FOR PLAYBACK";
      });
  }
}

function pause() {
  if (state.isCustomMode) {
    byoVideo.pause();
    stopAll();
    state.isPlaying = false;
    updatePlayBtn();
    stopNeedleLoop();
    if (hudStatus) hudStatus.textContent = "STANDBY";
    return;
  }

  audioMain.pause();
  audioTvn.pause();
  state.isPlaying = false;
  updatePlayBtn();
  stopNeedleLoop();
  if (hudStatus) hudStatus.textContent = "STANDBY";
}

function togglePlay() {
  if (state.isPlaying) {
    pause();
  } else {
    play();
  }
}

function updatePlayBtn() {
  btnPlay.setAttribute("aria-pressed", state.isPlaying ? "true" : "false");
  btnPlay.setAttribute("aria-label", state.isPlaying ? "Pause playback" : "Initiate playback");
  btnPlay.classList.toggle("is-playing", state.isPlaying);
}

/* ════════════════════════════════════════════════════════════
   9. Epoch Comparative Analysis (Then vs Now)
════════════════════════════════════════════════════════════ */
function toggleThenVsNow() {
  state.isThenVsNow = !state.isThenVsNow;
  btnTvn.setAttribute("aria-pressed", state.isThenVsNow ? "true" : "false");
  btnTvn.classList.toggle("active", state.isThenVsNow);
  tvnBanner.classList.toggle("visible", state.isThenVsNow);

  // Redraw canvas with the dual-epoch comparison view
  if (state.features) {
    drawViz(state.features);
  }

  if (hudStatus) {
    hudStatus.textContent = state.isThenVsNow ? "DUAL-EPOCH COMPARISON" : (state.isPlaying ? "SWEEP ACTIVE" : "STANDBY");
  }

  if (state.isThenVsNow) {
    // Switch exclusively from audioMain to audioTvn
    if (audioMain.duration) {
      audioTvn.currentTime = audioMain.currentTime;
    }
    audioMain.pause();
    if (state.isPlaying) {
      audioTvn.play().catch((err) => console.debug("TVN play error:", err));
    }
  } else {
    // Switch exclusively from audioTvn to audioMain
    if (audioTvn.duration) {
      audioMain.currentTime = audioTvn.currentTime;
    }
    audioTvn.pause();
    if (state.isPlaying) {
      audioMain.play().catch((err) => console.debug("Main play error:", err));
    }
  }
  syncProgress();
}

/* ════════════════════════════════════════════════════════════
   10. Spatial Coordinate Probe Toggle
════════════════════════════════════════════════════════════ */
function toggleProbe() {
  state.isProbeMode = !state.isProbeMode;
  btnProbe.setAttribute("aria-pressed", state.isProbeMode ? "true" : "false");
  btnProbe.classList.toggle("active", state.isProbeMode);
  playerStage.classList.toggle("is-probing", state.isProbeMode);

  if (state.isProbeMode) {
    attachProbeListeners();
    if (hudStatus) hudStatus.textContent = "PROBE ARMED";
  } else {
    detachProbeListeners();
    if (hudStatus) hudStatus.textContent = state.isPlaying ? "SWEEP ACTIVE" : "STANDBY";
  }
}

/* ════════════════════════════════════════════════════════════
   11. Console Hardware Key Commands
════════════════════════════════════════════════════════════ */
document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;

  switch (e.code) {
    case "Space":
      e.preventDefault();
      if (state.currentRecord) togglePlay();
      break;
    case "ArrowLeft": {
      if (state.isCustomMode) {
        if (byoVideo.duration) {
          byoVideo.currentTime = Math.max(0, byoVideo.currentTime - 5);
          syncProgress();
          state.lastExtractedSecond = -1;
          if (byoVideo.readyState >= 2) {
            extractAndSonifyCurrentFrame(Math.floor(byoVideo.currentTime));
          }
        }
        break;
      }
      const active = getActiveAudio();
      if (active.duration) {
        active.currentTime = Math.max(0, active.currentTime - 5);
        const inactive = getInactiveAudio();
        if (inactive.duration) inactive.currentTime = active.currentTime;
        syncProgress();
      }
      break;
    }
    case "ArrowRight": {
      if (state.isCustomMode) {
        if (byoVideo.duration) {
          byoVideo.currentTime = Math.min(byoVideo.duration, byoVideo.currentTime + 5);
          syncProgress();
          state.lastExtractedSecond = -1;
          if (byoVideo.readyState >= 2) {
            extractAndSonifyCurrentFrame(Math.floor(byoVideo.currentTime));
          }
        }
        break;
      }
      const active = getActiveAudio();
      if (active.duration) {
        active.currentTime = Math.min(active.duration, active.currentTime + 5);
        const inactive = getInactiveAudio();
        if (inactive.duration) inactive.currentTime = active.currentTime;
        syncProgress();
      }
      break;
    }
    case "ArrowUp":
      e.preventDefault();
      volumeBar.value = String(clamp(parseFloat(volumeBar.value) + 0.1, 0, 1));
      syncVolume();
      break;
    case "ArrowDown":
      e.preventDefault();
      volumeBar.value = String(clamp(parseFloat(volumeBar.value) - 0.1, 0, 1));
      syncVolume();
      break;
    case "KeyM":
      toggleMute();
      break;
    case "KeyP":
      if (state.currentRecord) toggleProbe();
      break;
    case "KeyT":
      if (state.currentRecord?.thenVsNow) toggleThenVsNow();
      break;
    default:
      if (e.key === "?") {
        openShortcuts();
        break;
      }
      if (e.key >= "1" && e.key <= "4") {
        const idx = parseInt(e.key, 10) - 1;
        if (RECORDS[idx]) loadRecord(RECORDS[idx]);
      }
      if (e.key === "Escape") closeShortcuts();
      break;
  }
});

/* ════════════════════════════════════════════════════════════
   12. Attenuation / Mute Mechanics
════════════════════════════════════════════════════════════ */
function toggleMute() {
  state.isMuted = !state.isMuted;
  syncVolume();
}

/* ════════════════════════════════════════════════════════════
   13. Command Reference Dialog
════════════════════════════════════════════════════════════ */
function openShortcuts() {
  shortcutsOverlay.classList.add("visible");
  shortcutsOverlay.focus();
}

function closeShortcuts() {
  shortcutsOverlay.classList.remove("visible");
}

btnHelp.addEventListener("click", openShortcuts);
btnCloseShortcuts.addEventListener("click", closeShortcuts);
footerShortcutsBtn?.addEventListener("click", openShortcuts);

shortcutsOverlay.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeShortcuts();
});

/* ════════════════════════════════════════════════════════════
   14. Control Surface Event Handlers
════════════════════════════════════════════════════════════ */
btnPlay.addEventListener("click", () => {
  if (state.currentRecord) togglePlay();
});

btnMute.addEventListener("click", toggleMute);

btnTvn.addEventListener("click", () => {
  if (state.currentRecord?.thenVsNow) toggleThenVsNow();
});

btnProbe.addEventListener("click", () => {
  if (state.currentRecord) toggleProbe();
});

volumeBar.addEventListener("input", () => {
  state.isMuted = parseFloat(volumeBar.value) === 0;
  syncVolume();
});

progressBar.addEventListener("input", () => {
  if (state.isCustomMode) {
    if (!byoVideo.duration) return;
    const t = (parseFloat(progressBar.value) / 100) * byoVideo.duration;
    byoVideo.currentTime = t;
    syncProgress();
    state.lastExtractedSecond = -1;
    if (byoVideo.readyState >= 2) {
      extractAndSonifyCurrentFrame(Math.floor(t));
    }
    return;
  }
  const active = getActiveAudio();
  if (!active.duration) return;
  const t = (parseFloat(progressBar.value) / 100) * active.duration;
  active.currentTime = t;
  const inactive = getInactiveAudio();
  if (inactive.duration) {
    inactive.currentTime = t;
  }
  syncProgress();
});

/* ════════════════════════════════════════════════════════════
   15. Bring Your Own Earth — Dynamic Video Frame Sonification
════════════════════════════════════════════════════════════ */
function handleCustomVideo(file) {
  if (!file) return;
  const isValidVideo = file.type.startsWith("video/") || /\.(mp4|webm|mov|mkv|avi)$/i.test(file.name);
  if (!isValidVideo) {
    if (hudStatus) hudStatus.textContent = "UNRECOGNIZED FILE TYPE";
    alert("Please provide a valid video file (MP4, WebM, QuickTime).");
    return;
  }

  // Deactivate pre-made audio
  audioMain.pause();
  audioTvn.pause();
  stopNeedleLoop();
  stopAll();

  // Clear existing custom video URL
  if (state.customVideoUrl) {
    URL.revokeObjectURL(state.customVideoUrl);
  }

  state.isCustomMode = true;
  state.customFile = file;
  state.customVideoUrl = URL.createObjectURL(file);
  state.isPlaying = false;
  state.isThenVsNow = false;
  state.lastExtractedSecond = -1;
  state.liveFrames = [];
  state.prevGray = null;

  // Unmark any active pre-made cards and activate custom card
  RECORDS.forEach(r => document.getElementById(`card-${r.id}`)?.classList.remove("active"));
  document.getElementById("card-byo")?.classList.add("active");

  const cleanName = file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ").toUpperCase();
  const mbSize = (file.size / (1024 * 1024)).toFixed(1);

  const customRecord = {
    id: "byo_earth",
    title: cleanName || "CUSTOM EARTH OBSERVATION",
    subtitle: `Live Optical Sonification (${mbSize} MB)`,
    tag: "BYO-LIVE",
    catalog: "USER OBSERVATION",
    instrument: "Client Frame Extractor (features.js)",
    soundRole: "Real-Time Web Audio Synthesis (synth.js)",
    description: `Custom video payload [${file.name}] mounted into live client-side pipeline. Video frames are dynamically downsampled to 640×360 and analyzed in real-time for optical luminance, dominant hue, 32 spatial strip profiles, and inter-frame motion.`,
    soundDescription: "Person B's Web Audio synthesizer generates sound in real time: dominant hue sets the pentatonic scale, horizontal column luminance pans across 32 stereo positions (-1.0 to +1.0), vertical column heights drive pitch offsets, and temporal motion modulates transient duration and rhythmic density.",
    color: "#38bdf8",
    thenVsNow: false
  };
  state.currentRecord = customRecord;

  // Telemetry HUD updates
  playerTitle.textContent    = customRecord.title;
  playerSubtitle.textContent = customRecord.subtitle;
  playerTag.textContent      = customRecord.tag;
  if (hudStatus) hudStatus.textContent = "ANALYZING VIDEO STREAM";

  // Dossier metadata injection
  if (descTag)        descTag.textContent = customRecord.tag;
  if (descCatalog)    descCatalog.textContent = customRecord.catalog;
  if (descTitle)      descTitle.textContent = customRecord.title;
  if (descInstrument) descInstrument.textContent = customRecord.instrument;
  if (descRole)       descRole.textContent = customRecord.soundRole;
  if (descBody)       descBody.textContent = customRecord.description;
  if (descSound)      descSound.textContent = customRecord.soundDescription;
  descPanel.classList.add("visible");

  // Epoch comparative button is hidden for custom mode
  btnTvn.hidden = true;
  btnTvn.setAttribute("aria-pressed", "false");
  btnTvn.classList.remove("active");
  tvnBanner.classList.remove("visible");

  // Show player and configure stage view
  playerPanel.classList.remove("hidden");
  state.customViewMode = "overlay";
  playerStage.className = "player-stage view-overlay";
  if (btnViewToggle) {
    btnViewToggle.hidden = false;
    btnViewToggle.textContent = "VIEW: OVERLAY";
  }

  // Reset scrubbers
  progressBar.value       = 0;
  timeCurrent.textContent = "00:00";
  timeTotal.textContent   = "00:00";
  needle.style.left       = "0px";

  // Mount video element
  byoVideo.hidden = false;
  byoVideo.src = state.customVideoUrl;
  byoVideo.muted = true; // Mute camera audio so sonification takes full auditory focus
  byoVideo.load();

  byoVideo.onloadedmetadata = () => {
    timeTotal.textContent = fmt(byoVideo.duration);
    progressBar.max = 100;
    if (hudStatus) hudStatus.textContent = "OPTICAL LOCK // READY";

    // Extract and sonify the initial frame at t=0
    byoVideo.currentTime = 0;
    setTimeout(() => {
      extractAndSonifyCurrentFrame(0);
      play();
    }, 150);
  };
}

function extractAndSonifyCurrentFrame(sec) {
  if (!byoVideo.videoWidth || !byoVideo.videoHeight) return;

  // Downscale to 640x360 for high-performance browser analysis
  offscreenCtx.drawImage(byoVideo, 0, 0, 640, 360);
  const imgData = offscreenCtx.getImageData(0, 0, 640, 360);
  const { features, gray } = extractFeatures(imgData, 640, 360, state.prevGray);
  state.prevGray = gray;
  features.t = sec;

  state.liveFrames[sec] = features;
  state.features = { frames: [features] };

  // Person B's synthesizer sonifies the frame in real time
  playFrame(features, sec, Math.ceil(byoVideo.duration || 1));

  // Render live visualization over the player stage
  drawLiveSpectrogram(features);

  if (hudStatus) {
    hudStatus.textContent = `OPTICAL TRACKING // HUE ${features.hue}° // LUM ${Math.round(features.brightness * 100)}%`;
  }
}

function drawLiveSpectrogram(features) {
  if (!ctx2d) return;
  const W = vizCanvas.width;
  const H = vizCanvas.height;
  ctx2d.clearRect(0, 0, W, H);

  if (!features || !features.columns) return;
  const columns = features.columns;
  const columnHeights = features.column_heights || [];
  const columnHues = features.column_hues || [];
  const nStrips = columns.length;
  const colW = W / nStrips;

  for (let s = 0; s < nStrips; s++) {
    const b = columns[s];
    const h = Math.max(4, (columnHeights[s] !== undefined ? columnHeights[s] : b) * H);
    const hue = columnHues[s] !== undefined ? columnHues[s] : features.hue;
    const x = s * colW;
    const y = H - h;

    ctx2d.fillStyle = hueToRgb(hue, b);
    ctx2d.globalAlpha = clamp(b * 0.85 + 0.15, 0.15, 0.95);
    ctx2d.fillRect(Math.floor(x) + 1, Math.floor(y), Math.ceil(colW) - 1, Math.ceil(h));
  }
  ctx2d.globalAlpha = 1.0;
}

/* ════════════════════════════════════════════════════════════
   16. Dynamic Record Shelf Generator
════════════════════════════════════════════════════════════ */
function buildShelf() {
  shelf.innerHTML = "";
  RECORDS.forEach((rec, idx) => {
    const card = document.createElement("article");
    card.id        = `card-${rec.id}`;
    card.className = "record-card";
    card.setAttribute("role", "listitem");
    card.style.setProperty("--card-accent", rec.color);
    card.tabIndex  = 0;
    card.setAttribute("aria-label", `${rec.title}: ${rec.subtitle}. Press Enter to mount dataset.`);

    card.innerHTML = `
      <div class="record-card__cover">
        <img
          src="${rec.image}"
          alt="${rec.title} scientific observation map"
          loading="${idx === 0 ? 'eager' : 'lazy'}"
          onerror="if(!this.dataset.retried){this.dataset.retried='1'; this.src = this.getAttribute('src').startsWith('../') ? this.getAttribute('src').slice(3) : ('../' + this.getAttribute('src'));} else { this.style.display='none'; }"
        />
        <div class="record-card__overlay"></div>
        <div class="record-card__header">
          <span class="catalog-badge">${rec.tag}</span>
          <span class="catalog-series">${rec.catalog}</span>
        </div>
        <div class="active-reticle" aria-hidden="true">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75">
            <circle cx="12" cy="12" r="9" stroke-dasharray="3 3"></circle>
            <circle cx="12" cy="12" r="4"></circle>
            <line x1="12" y1="2" x2="12" y2="6"></line>
            <line x1="12" y1="18" x2="12" y2="22"></line>
            <line x1="2" y1="12" x2="6" y2="12"></line>
            <line x1="18" y1="12" x2="22" y2="12"></line>
          </svg>
        </div>
      </div>
      <div class="record-card__body">
        <div class="record-card__top">
          <h3 class="record-card__title">${rec.title}</h3>
          <span class="record-card__dot"></span>
        </div>
        <div class="record-card__subtitle">${rec.subtitle}</div>
        <div class="record-card__meta">
          <span class="record-card__instrument">${rec.instrument || 'NASA Observation'}</span>
        </div>
      </div>
    `;

    card.addEventListener("click", () => loadRecord(rec));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        loadRecord(rec);
      }
    });

    shelf.appendChild(card);
  });

  // 5th Record Sleeve: "Bring Your Own Earth"
  const customCard = document.createElement("article");
  customCard.id        = "card-byo";
  customCard.className = "record-card record-card--custom";
  customCard.setAttribute("role", "listitem");
  customCard.tabIndex  = 0;
  customCard.setAttribute("aria-label", "Bring Your Own Earth: Drop or browse any NASA video to sonify in real time. Press Enter to select file.");

  customCard.innerHTML = `
    <div class="record-card__cover record-card__cover--custom">
      <div class="custom-drop-art">
        <div class="drop-reticle">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
          </svg>
        </div>
        <span class="custom-drop-prompt">DROP NASA VIDEO HERE</span>
        <span class="custom-drop-sub">OR CLICK TO BROWSE (MP4 / WEBM)</span>
      </div>
      <div class="record-card__header">
        <span class="catalog-badge catalog-badge--custom">LIVE OPTICAL</span>
        <span class="catalog-series">CUSTOM DATASET</span>
      </div>
      <div class="active-reticle" aria-hidden="true">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75">
          <circle cx="12" cy="12" r="9" stroke-dasharray="3 3"></circle>
          <circle cx="12" cy="12" r="4"></circle>
          <line x1="12" y1="2" x2="12" y2="6"></line>
          <line x1="12" y1="18" x2="12" y2="22"></line>
          <line x1="2" y1="12" x2="6" y2="12"></line>
          <line x1="18" y1="12" x2="22" y2="12"></line>
        </svg>
      </div>
    </div>
    <div class="record-card__body">
      <div class="record-card__top">
        <h3 class="record-card__title">Bring Your Own Earth</h3>
        <span class="record-card__dot record-card__dot--custom"></span>
      </div>
      <div class="record-card__subtitle">Client-Side Optical Sonification</div>
      <div class="record-card__meta">
        <span class="record-card__instrument">Dynamic Browser Frame Extractor</span>
      </div>
    </div>
  `;

  customCard.addEventListener("click", () => byoFileInput.click());
  customCard.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      byoFileInput.click();
    }
  });

  // Drag-and-drop handlers for the custom card
  customCard.addEventListener("dragover", (e) => {
    e.preventDefault();
    customCard.classList.add("drag-over");
  });
  customCard.addEventListener("dragleave", () => {
    customCard.classList.remove("drag-over");
  });
  customCard.addEventListener("drop", (e) => {
    e.preventDefault();
    customCard.classList.remove("drag-over");
    if (e.dataTransfer && e.dataTransfer.files.length) {
      handleCustomVideo(e.dataTransfer.files[0]);
    }
  });

  shelf.appendChild(customCard);
  if (catalogCount) catalogCount.textContent = "4 CURATED DATASETS + 1 CUSTOM BYO ENGINE";
}

/* ════════════════════════════════════════════════════════════
   17. Platform Initialization
════════════════════════════════════════════════════════════ */
function init() {
  buildShelf();
  initCanvas();

  volumeBar.value = "1";
  syncVolume();

  // Bring Your Own Earth file input handler
  if (byoFileInput) {
    byoFileInput.addEventListener("change", () => {
      if (byoFileInput.files && byoFileInput.files.length) {
        handleCustomVideo(byoFileInput.files[0]);
      }
    });
  }

  // View toggle button handler (overlay -> video -> spectrogram)
  if (btnViewToggle) {
    btnViewToggle.addEventListener("click", () => {
      if (!state.isCustomMode) return;
      const modes = ["overlay", "video", "spectrogram"];
      const nextIdx = (modes.indexOf(state.customViewMode) + 1) % modes.length;
      state.customViewMode = modes[nextIdx];
      playerStage.className = "player-stage view-" + state.customViewMode;
      btnViewToggle.textContent = "VIEW: " + state.customViewMode.toUpperCase();
    });
  }

  // Global window drop handler so user can drag video anywhere on screen
  window.addEventListener("dragover", (e) => {
    e.preventDefault();
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) {
      const file = e.dataTransfer.files[0];
      if (file.type.startsWith("video/") || /\.(mp4|webm|mov|mkv)$/i.test(file.name)) {
        handleCustomVideo(file);
      }
    }
  });

  // Accessible keyboard probe navigation
  playerStage.addEventListener("keydown", (e) => {
    if (!state.isProbeMode || !state.features) return;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
      e.preventDefault();
      const W = playerStage.clientWidth;
      const H = playerStage.clientHeight;
      const needleLeft = parseFloat(needle.style.left) || 0;
      let relX = needleLeft / W;
      let relY = 0.5;

      if (e.key === "ArrowLeft")  relX = Math.max(0, relX - 0.03);
      if (e.key === "ArrowRight") relX = Math.min(1, relX + 0.03);
      if (e.key === "ArrowUp")    relY = Math.max(0, relY - 0.1);
      if (e.key === "ArrowDown")  relY = Math.min(1, relY + 0.1);

      handleProbeEvent({
        clientX: playerStage.getBoundingClientRect().left + relX * W,
        clientY: playerStage.getBoundingClientRect().top  + relY * H,
      });
    }
  });

  audioMain.addEventListener("volumechange", () => {
    if (!state.isMuted) state.volumeBeforeMute = audioMain.volume;
  });

  console.info("NASA SVS Acoustic Sonification Console initialized.");
}

document.addEventListener("DOMContentLoaded", init);
