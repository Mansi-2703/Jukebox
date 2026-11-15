/**
 * synth.js — Real-Time Web Audio Synthesizer for "Bring Your Own Earth"
 * Translates visual telemetry features into acoustic sonification in the browser.
 *
 * Implements Person B's specification:
 *   - Pitch: maps hue to musical scale notes
 *   - Volume: maps brightness to gain
 *   - Stereo pan: maps 32 strips across spatial sound field (-1 left to +1 right)
 *   - Vertical position: maps column heights to pitch offsets
 *   - Rhythm / Duration: maps motion to note density and duration
 *   - Instrument: triangle/sine/square Web Audio oscillators
 *   - Timing: 1-second audio frame intervals
 */

// A-Minor / C-Major Pentatonic frequencies (C3 to C6)
const PENTATONIC_FREQS = [
  130.81, 146.83, 164.81, 196.00, 220.00,  // C3, D3, E3, G3, A3
  261.63, 293.66, 329.63, 392.00, 440.00,  // C4, D4, E4, G4, A4
  523.25, 587.33, 659.25, 783.99, 880.00,  // C5, D5, E5, G5, A5
  1046.50                                  // C6
];

let actx = null;
let masterGain = null;
let isMuted = false;
let masterVol = 1.0;
let activeNodes = [];

/**
 * Lazily initialize the Web Audio context
 */
export function getAudioContext() {
  if (!actx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    actx = new AudioContextClass();
    masterGain = actx.createGain();
    masterGain.gain.setValueAtTime(isMuted ? 0 : masterVol, actx.currentTime);
    masterGain.connect(actx.destination);
  }
  if (actx.state === "suspended") {
    actx.resume().catch(() => {});
  }
  return actx;
}

export function setMasterVolume(v) {
  masterVol = Math.max(0, Math.min(1, v));
  if (masterGain && actx) {
    masterGain.gain.setValueAtTime(isMuted ? 0 : masterVol, actx.currentTime);
  }
}

export function setMuted(muted) {
  isMuted = !!muted;
  if (masterGain && actx) {
    masterGain.gain.setValueAtTime(isMuted ? 0 : masterVol, actx.currentTime);
  }
}

export function stopAll() {
  const now = actx ? actx.currentTime : 0;
  for (const node of activeNodes) {
    try {
      if (node.stop) node.stop(now);
      node.disconnect();
    } catch (_) {}
  }
  activeNodes = [];
}

/**
 * Determine oscillator waveform based on hue characteristics
 */
function getOscillatorType(hue) {
  if (hue >= 180 && hue <= 250) {
    return "sine";       // Cool/Oceanic blues
  } else if (hue >= 280 && hue <= 330) {
    return "square";     // Urban/Synthetic purples & magentas
  }
  return "triangle";     // Warm terrestrial & vegetated earth
}

/**
 * Synthesizes one frame of video features over a 1.0s interval.
 *
 * @param {Object} features - frame telemetry { brightness, hue, motion, columns, column_heights, column_hues }
 * @param {number} frameIndex - current frame index
 * @param {number} totalFrames - estimated or known total frames
 */
export function playFrame(features, frameIndex = 0, totalFrames = 1) {
  if (!features) return;
  const ctx = getAudioContext();
  const now = ctx.currentTime;

  // Clean up nodes finished in previous cycles
  activeNodes = activeNodes.filter(n => n._endTime && n._endTime > now);

  const brightness = Math.max(0, Math.min(1, features.brightness || 0));
  const hue = Math.max(0, Math.min(360, features.hue || 0));
  const motion = Math.max(0, Math.min(1, features.motion || 0));
  const columns = features.columns || [];
  const columnHeights = features.column_heights || [];
  const columnHues = features.column_hues || [];

  if (brightness < 0.02 && columns.every(c => c < 0.02)) {
    return; // Frame is too dark/silent
  }

  // 1. Root atmospheric pad tone
  const baseScaleIdx = Math.floor((hue / 360) * (PENTATONIC_FREQS.length - 6));
  const rootFreq = PENTATONIC_FREQS[baseScaleIdx] || 220.0;
  const oscType = getOscillatorType(hue);

  try {
    const padOsc = ctx.createOscillator();
    const padGain = ctx.createGain();

    padOsc.type = oscType;
    padOsc.frequency.setValueAtTime(rootFreq, now);

    // Subtle drift on long drone
    padOsc.frequency.exponentialRampToValueAtTime(rootFreq * 1.005, now + 0.95);

    const padVol = brightness * 0.18;
    padGain.gain.setValueAtTime(0.0001, now);
    padGain.gain.exponentialRampToValueAtTime(Math.max(0.001, padVol), now + 0.1);
    padGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.98);

    padOsc.connect(padGain);
    padGain.connect(masterGain);

    padOsc.start(now);
    padOsc.stop(now + 1.0);
    padOsc._endTime = now + 1.0;
    activeNodes.push(padOsc);
  } catch (err) {
    console.debug("Atmospheric pad error:", err);
  }

  // 2. Spatial column sonification (32 strips)
  const nStrips = columns.length;
  if (nStrips === 0) return;

  // Rhythm & note length modulation from motion:
  // High motion = crisp staccato percussion; low motion = sustained ambient bells
  const baseNoteDuration = Math.max(0.06, 0.38 - motion * 0.28);

  // Group columns or pick prominent peaks to avoid polyphony overload
  for (let s = 0; s < nStrips; s++) {
    const colBright = columns[s];
    if (colBright < 0.04) continue; // Skip dark strips

    const colHeight = columnHeights[s] !== undefined ? columnHeights[s] : 0.5;
    const colHue = columnHues[s] !== undefined ? columnHues[s] : hue;

    // Pitch: Base hue + height offset
    const heightShift = Math.floor(colHeight * 7); // 0 to 7 scale steps
    const hueShift = Math.floor((colHue / 360) * 8);
    const scaleIdx = Math.min(PENTATONIC_FREQS.length - 1, baseScaleIdx + heightShift + hueShift);
    const noteFreq = PENTATONIC_FREQS[scaleIdx];

    // Stereo Panning (-1 = Left, +1 = Right)
    const panVal = (s / (nStrips - 1)) * 2 - 1;

    // Temporal stagger across the 1.0s window
    // High motion aggregates into rhythmic clusters; low motion spreads linearly
    const stagger = motion > 0.3
      ? ((s % 4) * 0.22 + (s >= 16 ? 0.1 : 0))
      : (s / nStrips) * 0.65;
    const startTime = now + stagger;
    const stopTime = startTime + baseNoteDuration;

    try {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      let panner = null;

      if (ctx.createStereoPanner) {
        panner = ctx.createStereoPanner();
        panner.pan.setValueAtTime(Math.max(-1, Math.min(1, panVal)), startTime);
      }

      osc.type = colBright > 0.7 ? "square" : oscType;
      osc.frequency.setValueAtTime(noteFreq, startTime);

      const noteVol = Math.max(0.001, (colBright * 0.22) / Math.sqrt(nStrips / 8));
      gain.gain.setValueAtTime(0.0001, startTime);
      gain.gain.exponentialRampToValueAtTime(noteVol, startTime + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, stopTime);

      osc.connect(gain);
      if (panner) {
        gain.connect(panner);
        panner.connect(masterGain);
      } else {
        gain.connect(masterGain);
      }

      osc.start(startTime);
      osc.stop(stopTime + 0.02);
      osc._endTime = stopTime + 0.02;
      activeNodes.push(osc);
    } catch (err) {
      console.debug("Strip synth error:", err);
    }
  }
}
