/**
 * synth.js — Earth Jukebox: Web Audio API Synthesizer
 * 
 * Real-time audio synthesis for "Bring Your Own Earth" mode.
 * Converts visual features extracted from user video into music using the Web Audio API.
 * 
 * DEFAULT OPTIONS:
 * - scale: "C_major_pentatonic" (C D E G A across octaves 3-6)
 * - rootNote: 60 (middle C, MIDI note number)
 * - oscillatorType: "triangle" (warm, balanced timbre)
 * - brightnessThreshold: 0.15 (strips dimmer than this are skipped)
 * - maxVoices: 4 (cap simultaneous notes to prevent noise)
 * - motionGain: 20 (rescale raw motion values, which are often tiny)
 * - masterVolume: 0.7 (overall output level, 0-1)
 */

// ══════════════════════════════════════════════════════════════════════
// AUDIO CONTEXT & MASTER CHAIN
// ══════════════════════════════════════════════════════════════════════

let audioContext = null;
let masterGain = null;
let compressor = null;
let lowPassFilter = null;

// Track active notes so we can stop them cleanly
const activeNotes = [];

/**
 * Initialize or resume the AudioContext and master effects chain.
 * MUST be called from a user gesture (button click) due to browser autoplay policy.
 */
export function initAudio() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    
    // Master chain: gain → compressor (soft limiter) → lowpass filter → destination
    masterGain = audioContext.createGain();
    masterGain.gain.value = options.masterVolume;
    
    compressor = audioContext.createDynamicsCompressor();
    compressor.threshold.value = -10;  // dB
    compressor.knee.value = 10;
    compressor.ratio.value = 4;
    compressor.attack.value = 0.003;
    compressor.release.value = 0.25;
    
    lowPassFilter = audioContext.createBiquadFilter();
    lowPassFilter.type = 'lowpass';
    lowPassFilter.frequency.value = 7000;  // gentle roll-off to prevent harshness
    lowPassFilter.Q.value = 0.7;
    
    masterGain.connect(compressor);
    compressor.connect(lowPassFilter);
    lowPassFilter.connect(audioContext.destination);
  }
  
  // Resume context if suspended (Chrome autoplay policy)
  if (audioContext.state === 'suspended') {
    audioContext.resume();
  }
  
  return audioContext;
}

/**
 * Stop all currently playing notes immediately.
 */
export function stopAll() {
  activeNotes.forEach(note => {
    try {
      note.osc.stop();
      note.osc.disconnect();
    } catch (e) {
      // Already stopped or disconnected
    }
  });
  activeNotes.length = 0;
}

// ══════════════════════════════════════════════════════════════════════
// OPTIONS
// ══════════════════════════════════════════════════════════════════════

const options = {
  scale: 'C_major_pentatonic',
  rootNote: 60,
  oscillatorType: 'triangle',
  brightnessThreshold: 0.15,
  maxVoices: 4,
  motionGain: 20,
  masterVolume: 0.7,
};

/**
 * Update synthesis options. Pass a partial object; only provided fields are updated.
 */
export function setOptions(opts) {
  Object.assign(options, opts);
  if (masterGain && opts.masterVolume !== undefined) {
    masterGain.gain.value = opts.masterVolume;
  }
}

/**
 * Get current options (returns a copy).
 */
export function getOptions() {
  return { ...options };
}

/**
 * Compatibility aliases for Person C UI integration
 */
export function getAudioContext() {
  return initAudio();
}

export function setMasterVolume(v) {
  setOptions({ masterVolume: Math.max(0, Math.min(1, v)) });
}

let _savedVolBeforeMute = 0.7;
export function setMuted(muted) {
  if (muted) {
    if (options.masterVolume > 0) _savedVolBeforeMute = options.masterVolume;
    setOptions({ masterVolume: 0 });
  } else {
    setOptions({ masterVolume: _savedVolBeforeMute || 0.7 });
  }
}

// ══════════════════════════════════════════════════════════════════════
// SCALES — MIDI note number collections
// ══════════════════════════════════════════════════════════════════════

function buildScale(intervals, octaves) {
  const notes = [];
  for (const octave of octaves) {
    const root = (octave + 1) * 12;  // C in this octave
    for (const interval of intervals) {
      notes.push(root + interval);
    }
  }
  return notes.sort((a, b) => a - b);
}

const SCALES = {
  C_major_pentatonic: buildScale([0, 2, 4, 7, 9], [3, 4, 5, 6]),
  C_minor_pentatonic: buildScale([0, 3, 5, 7, 10], [3, 4, 5, 6]),
  C_major: buildScale([0, 2, 4, 5, 7, 9, 11], [3, 4, 5, 6]),
  C_blues: buildScale([0, 3, 5, 6, 7, 10], [3, 4, 5, 6]),
  A_minor_pentatonic: buildScale([9, 12, 14, 16, 19], [2, 3, 4, 5]),
};

// ══════════════════════════════════════════════════════════════════════
// MAPPING FUNCTIONS — Pure functions for the sonification logic
// ══════════════════════════════════════════════════════════════════════

/**
 * Map hue (0-360°) to a base MIDI note in the current scale.
 * Hue is mapped linearly across the scale: 0° → lowest note, 360° → highest note.
 * This creates a "color keyboard" where warm colors (reds, oranges) are low notes
 * and cool colors (blues, purples) are high notes.
 */
function hueToNote(hue) {
  const scale = SCALES[options.scale] || SCALES.C_major_pentatonic;
  const normalizedHue = ((hue % 360) + 360) % 360;  // normalize to [0, 360)
  const index = Math.floor((normalizedHue / 360) * scale.length);
  return scale[Math.max(0, Math.min(scale.length - 1, index))];
}

/**
 * Map column_height (0-1) to a pitch offset in scale steps.
 * Raw heights cluster near 0.5, so we apply a contrast stretch:
 *   height < 0.5 → negative offset (lower notes)
 *   height > 0.5 → positive offset (higher notes)
 * This ensures vertical position produces audible pitch variation (about 2 octaves).
 */
function heightToOffset(height) {
  // Contrast stretch: map 0.3-0.7 range to full -1 to +1
  const centered = (height - 0.5) * 2.5;  // amplify deviation from center
  const clamped = Math.max(-1, Math.min(1, centered));
  
  // Map to scale steps: -1 → -10 steps (down ~1.5 octaves), +1 → +10 steps (up ~1.5 octaves)
  const scale = SCALES[options.scale] || SCALES.C_major_pentatonic;
  const offset = Math.round(clamped * 10);
  
  return offset;
}

/**
 * Apply pitch offset (in scale steps) to a base MIDI note, staying within the scale.
 */
function applyOffset(baseNote, offset) {
  const scale = SCALES[options.scale] || SCALES.C_major_pentatonic;
  const baseIndex = scale.indexOf(baseNote);
  if (baseIndex === -1) return baseNote;  // shouldn't happen
  
  const newIndex = Math.max(0, Math.min(scale.length - 1, baseIndex + offset));
  return scale[newIndex];
}

/**
 * Map brightness (0-1) to gain (0-1) with a gentle curve.
 * Uses sqrt to give perceptually even volume changes (closer to how we hear loudness).
 */
function brightnessToGain(brightness) {
  const clamped = Math.max(0, Math.min(1, brightness));
  return Math.sqrt(clamped);  // gentler than linear, prevents quiet regions from vanishing
}

/**
 * Map motion (0-1, but usually very small in raw data) to rhythm parameters.
 * Returns { noteLength, attackTime, releaseTime }.
 * High motion → short, percussive notes. Low motion → longer, sustained notes.
 */
function motionToRhythm(motion) {
  // Rescale raw motion (often 0.001-0.05) to usable range
  const scaled = Math.min(1, motion * options.motionGain);
  
  // High motion (0.7-1.0) → short notes (0.1-0.3s), fast attack
  // Low motion (0.0-0.3)  → long notes (0.5-1.5s), slow attack
  const noteLength = 1.5 - scaled * 1.2;  // 1.5s down to 0.3s
  const attackTime = 0.05 - scaled * 0.04;  // 0.05s down to 0.01s
  const releaseTime = 0.2 - scaled * 0.1;   // 0.2s down to 0.1s
  
  return {
    noteLength: Math.max(0.1, noteLength),
    attackTime: Math.max(0.005, attackTime),
    releaseTime: Math.max(0.05, releaseTime),
  };
}

/**
 * Choose oscillator type based on hue and saturation ("auto" mode).
 * - Warm colors (reds, oranges, yellows: 0-60° and 300-360°) → square (urban, bright)
 * - Cool colors (cyans, blues: 180-270°) → sine (smooth, oceanic)
 * - Greens and transitional colors → triangle (balanced, forest-like)
 * Low saturation always uses sine (desaturated = soft, ambient).
 */
function autoOscillatorType(hue, saturation) {
  if (saturation < 0.3) return 'sine';  // desaturated → soft
  
  const h = ((hue % 360) + 360) % 360;
  if (h <= 60 || h >= 300) return 'square';   // warm → sharp
  if (h >= 180 && h <= 270) return 'sine';    // cool → smooth
  return 'triangle';                           // transitional → balanced
}

// ══════════════════════════════════════════════════════════════════════
// MAIN PLAYBACK FUNCTION
// ══════════════════════════════════════════════════════════════════════

/**
 * Play one frame's worth of audio, scheduled on the AudioContext clock.
 * 
 * @param {Object} features - feature object from extractFeatures() or JSON
 * @param {number} frameIndex - current frame number (for timing)
 * @param {number} totalFrames - total frame count (for UI, not used in scheduling)
 */
export function playFrame(features, frameIndex, totalFrames) {
  if (!audioContext) {
    console.warn('synth.js: AudioContext not initialized. Call initAudio() first.');
    return;
  }
  
  // Defensive: handle missing or malformed features
  if (!features || typeof features !== 'object') {
    console.warn('synth.js: Invalid features object:', features);
    return;
  }
  
  const {
    brightness = 0.5,
    hue = 180,
    saturation = 0.5,
    motion = 0,
    columns = [],
    column_heights = [],
    column_hues = [],
  } = features;
  
  // Safety: skip empty or very dark frames
  if (brightness < 0.05 || columns.length === 0) {
    return;  // silent frame
  }
  
  // ── 1. Select strips to play (brightest N strips above threshold) ──
  const nStrips = columns.length;
  const candidates = [];
  
  for (let i = 0; i < nStrips; i++) {
    const stripBrightness = columns[i] || 0;
    if (stripBrightness >= options.brightnessThreshold) {
      candidates.push({ index: i, brightness: stripBrightness });
    }
  }
  
  // Sort by brightness, take top N
  candidates.sort((a, b) => b.brightness - a.brightness);
  const selectedStrips = candidates.slice(0, options.maxVoices);
  
  if (selectedStrips.length === 0) {
    return;  // no strips bright enough
  }
  
  // ── 2. Rhythm parameters from motion ──
  const rhythm = motionToRhythm(motion);
  
  // ── 3. Oscillator type (fixed or auto) ──
  let oscType = options.oscillatorType;
  if (oscType === 'auto') {
    oscType = autoOscillatorType(hue, saturation);
  }
  
  // ── 4. Schedule notes across the frame's 1-second window ──
  const now = audioContext.currentTime;
  const frameStart = now + 0.01;  // small offset to avoid scheduling in the past
  
  // Spread notes across the second according to motion: high motion = more pulses
  const notesPerFrame = Math.max(1, Math.floor(1 + motion * options.motionGain * 2));
  const timeStep = 1.0 / notesPerFrame;
  
  selectedStrips.forEach((strip, voiceIndex) => {
    const i = strip.index;
    const stripBrightness = strip.brightness;
    
    // ── Pitch calculation ──
    // Use strip hue if available and strip is bright; otherwise frame hue
    let stripHue = hue;
    if (column_hues.length > i && stripBrightness >= options.brightnessThreshold) {
      stripHue = column_hues[i];
    }
    
    const baseNote = hueToNote(stripHue);
    
    // Apply height offset if available
    let finalNote = baseNote;
    if (column_heights.length > i) {
      const height = column_heights[i];
      if (!isNaN(height)) {
        const offset = heightToOffset(height);
        finalNote = applyOffset(baseNote, offset);
      }
    }
    
    const frequency = midiToFreq(finalNote);
    
    // ── Volume calculation ──
    const stripGain = brightnessToGain(stripBrightness * brightness);
    
    // ── Pan calculation ──
    const pan = nStrips > 1 ? (i / (nStrips - 1)) * 2 - 1 : 0;  // -1 (left) to +1 (right)
    
    // ── Schedule note(s) ──
    // For high motion, play multiple short notes per strip; for low motion, one long note
    const noteCount = Math.max(1, Math.ceil(notesPerFrame / selectedStrips.length));
    
    for (let n = 0; n < noteCount; n++) {
      const startTime = frameStart + voiceIndex * 0.02 + n * timeStep;  // stagger slightly
      playNote(frequency, stripGain, pan, oscType, rhythm, startTime);
    }
  });
}

// ══════════════════════════════════════════════════════════════════════
// LOW-LEVEL NOTE SYNTHESIS
// ══════════════════════════════════════════════════════════════════════

/**
 * Play a single note with ADSR envelope.
 * 
 * @param {number} frequency - Hz
 * @param {number} gain - 0-1
 * @param {number} pan - -1 (left) to +1 (right)
 * @param {string} oscType - 'sine' | 'triangle' | 'square'
 * @param {Object} rhythm - { noteLength, attackTime, releaseTime }
 * @param {number} startTime - AudioContext time to start
 */
function playNote(frequency, gain, pan, oscType, rhythm, startTime) {
  const osc = audioContext.createOscillator();
  osc.type = oscType;
  osc.frequency.value = frequency;
  
  const gainNode = audioContext.createGain();
  gainNode.gain.value = 0;  // start silent
  
  const panNode = audioContext.createStereoPanner();
  panNode.pan.value = Math.max(-1, Math.min(1, pan));
  
  // Connect: osc → gain (ADSR) → pan → master chain
  osc.connect(gainNode);
  gainNode.connect(panNode);
  panNode.connect(masterGain);
  
  // ── ADSR envelope ──
  const { noteLength, attackTime, releaseTime } = rhythm;
  const sustainTime = Math.max(0.05, noteLength - attackTime - releaseTime);
  const peakGain = gain * 0.3;  // scale down to prevent clipping when many notes play
  
  const t0 = startTime;
  const t1 = t0 + attackTime;
  const t2 = t1 + sustainTime;
  const t3 = t2 + releaseTime;
  
  gainNode.gain.setValueAtTime(0, t0);
  gainNode.gain.linearRampToValueAtTime(peakGain, t1);  // attack
  gainNode.gain.setValueAtTime(peakGain, t2);           // sustain
  gainNode.gain.linearRampToValueAtTime(0, t3);         // release
  
  osc.start(t0);
  osc.stop(t3);
  
  // Clean up nodes after note ends to prevent memory leaks
  osc.onended = () => {
    osc.disconnect();
    gainNode.disconnect();
    panNode.disconnect();
    
    const idx = activeNotes.findIndex(n => n.osc === osc);
    if (idx !== -1) activeNotes.splice(idx, 1);
  };
  
  activeNotes.push({ osc, gainNode, panNode });
}

/**
 * Convert MIDI note number to frequency (Hz).
 * A440 tuning: A4 = MIDI 69 = 440 Hz.
 */
function midiToFreq(midi) {
  return 440 * Math.pow(2, (midi - 69) / 12);
}
