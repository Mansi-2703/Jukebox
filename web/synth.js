/**
 * synth.js — Earth Jukebox: Web Audio API Synthesizer
 *
 * Real-time audio synthesis for "Bring Your Own Earth" mode.
 * Converts visual features extracted from user video into music using the Web Audio API.
 *
 * DEFAULT OPTIONS:
 * - scale: "C_major_pentatonic" (C D E G A across octaves 3-6)
 * - rootNote: 60 (middle C, MIDI note number)
 * - oscillatorType: "triangle" (kept for backward compat; prefer setInstrumentPreset())
 * - brightnessThreshold: 0.15 (strips dimmer than this are skipped)
 * - maxVoices: 4 (cap simultaneous notes to prevent noise)
 * - motionGain: 20 (rescale raw motion values, which are often tiny; calibrated for
 *                   raw pipeline motion values typically 0–0.05)
 * - masterVolume: 0.7 (overall output level, 0-1)
 *
 * AUDIO GRAPH (created once in initAudio):
 *
 *   [voice nodes] ──► instrumentGain ──► masterAnalyser ──► masterGain ──► compressor ──► destination
 *                                              ▲
 *   [<audio> elements via connectAudioElement]─┘
 *
 * The masterAnalyser sits BEFORE masterGain, so visualisations do not change
 * when the user moves the volume slider.
 *
 * EXPORTS (all public API):
 *   initAudio()                              → AudioContext
 *   stopAll()
 *   setOptions(opts)
 *   getOptions()                             → Object
 *   playFrame(features, frameIndex?, totalFrames?)
 *   getAudioContext()                        → AudioContext  [compat alias]
 *   setMasterVolume(v)                       [compat alias]
 *   setMuted(muted)                          [compat alias]
 *   getAudioAnalyser()                       → AnalyserNode | null
 *   connectAudioElement(audioEl)             → MediaElementAudioSourceNode
 *   setInstrumentPreset(id)
 *   getInstrumentPreset()                    → string
 *   getAvailableInstruments()                → Array<{id, name, description, sensorMatch}>
 *   playRecordLive(audioEl, frames, options)
 *   stopRecordLive()
 */

// ══════════════════════════════════════════════════════════════════════
// AUDIO CONTEXT & MASTER CHAIN
// ══════════════════════════════════════════════════════════════════════

let audioContext    = null;
let instrumentGain  = null;   // receives all voice nodes & media elements
let masterAnalyser  = null;   // shared AnalyserNode — read by Person C via getAudioAnalyser()
let masterGain      = null;
let compressor      = null;
// (lowPassFilter removed from master chain; each synth_pad voice has its own per-voice filter)

// Track active notes so we can stop them cleanly
const activeNotes = [];

/**
 * Initialize or resume the AudioContext and master effects chain.
 * MUST be called from a user gesture (button click) due to browser autoplay policy.
 *
 * Graph: instrumentGain → masterAnalyser → masterGain → compressor → destination
 */
export function initAudio() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();

    // ── Instrument gain (input bus for all voices and media elements) ──
    instrumentGain = audioContext.createGain();
    instrumentGain.gain.value = 1.0;

    // ── Shared AnalyserNode — sits BEFORE masterGain so visuals don't track the volume slider ──
    masterAnalyser = audioContext.createAnalyser();
    masterAnalyser.fftSize = 2048;
    masterAnalyser.smoothingTimeConstant = 0.8;

    // ── Master gain (volume slider) ──
    masterGain = audioContext.createGain();
    masterGain.gain.value = options.masterVolume;

    // ── DynamicsCompressor (soft limiter, prevents digital clipping) ──
    compressor = audioContext.createDynamicsCompressor();
    compressor.threshold.value = -10;   // dB
    compressor.knee.value       = 10;
    compressor.ratio.value      = 4;
    compressor.attack.value     = 0.003;
    compressor.release.value    = 0.25;

    // Wire up the master chain
    instrumentGain.connect(masterAnalyser);
    masterAnalyser.connect(masterGain);
    masterGain.connect(compressor);
    compressor.connect(audioContext.destination);
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
    } catch (_) { /* already stopped */ }
    // Extra nodes specific to preset (lfo, filter, osc2…) are cleaned up in osc.onended
  });
  activeNotes.length = 0;
}

// ══════════════════════════════════════════════════════════════════════
// OPTIONS
// ══════════════════════════════════════════════════════════════════════

const options = {
  scale:               'C_major_pentatonic',
  rootNote:            60,
  oscillatorType:      'triangle',   // DEPRECATED — use setInstrumentPreset() instead
  brightnessThreshold: 0.15,
  maxVoices:           4,
  motionGain:          20,
  masterVolume:        0.7,
};

// Whether the user has explicitly set a preset via setInstrumentPreset()
// (used to suppress legacy oscillatorType back-compat mapping)
let _presetExplicitlySet = false;

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

// ══════════════════════════════════════════════════════════════════════
// COMPAT ALIASES (for Person C UI integration, unchanged signatures)
// ══════════════════════════════════════════════════════════════════════

/** @deprecated Use initAudio() directly. */
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
// ANALYSER & MEDIA ELEMENT (B1)
// ══════════════════════════════════════════════════════════════════════

/**
 * Return the shared AnalyserNode so Person C can read live FFT/waveform data.
 *
 * Usage:
 *   const analyser = getAudioAnalyser();
 *   if (analyser) {
 *     const buf = new Uint8Array(analyser.frequencyBinCount);
 *     analyser.getByteFrequencyData(buf);
 *   }
 *
 * The analyser sits BEFORE masterGain, so visualisation amplitude is independent
 * of the volume slider.
 *
 * Returns null (and logs a warning) if initAudio() has not been called yet.
 */
export function getAudioAnalyser() {
  if (!masterAnalyser) {
    console.warn('synth.js: getAudioAnalyser() called before initAudio(). Call initAudio() first from a user gesture.');
    return null;
  }
  return masterAnalyser;
}

/**
 * WeakMap: HTMLMediaElement → MediaElementAudioSourceNode
 * An element can only be wrapped once per AudioContext; we reuse the existing source.
 */
const _mediaElementSources = new WeakMap();

/**
 * Connect an HTML5 <audio> element into the shared master audio chain.
 * Safe to call multiple times with the same element (returns the cached source node).
 *
 * @param {HTMLMediaElement} audioEl
 * @returns {MediaElementAudioSourceNode}
 */
export function connectAudioElement(audioEl) {
  if (!audioContext) {
    console.warn('synth.js: connectAudioElement() called before initAudio(). Calling initAudio() now.');
    initAudio();
  }
  if (_mediaElementSources.has(audioEl)) {
    return _mediaElementSources.get(audioEl);
  }
  const source = audioContext.createMediaElementSource(audioEl);
  source.connect(instrumentGain);
  _mediaElementSources.set(audioEl, source);
  return source;
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
  C_major_pentatonic: buildScale([0, 2, 4, 7, 9],        [3, 4, 5, 6]),
  C_minor_pentatonic: buildScale([0, 3, 5, 7, 10],       [3, 4, 5, 6]),
  C_major:            buildScale([0, 2, 4, 5, 7, 9, 11], [3, 4, 5, 6]),
  C_blues:            buildScale([0, 3, 5, 6, 7, 10],    [3, 4, 5, 6]),
  A_minor_pentatonic: buildScale([9, 12, 14, 16, 19],    [2, 3, 4, 5]),
};

// ══════════════════════════════════════════════════════════════════════
// MAPPING FUNCTIONS — Pure functions, shared by all instrument presets
// ══════════════════════════════════════════════════════════════════════

/**
 * Map hue (0-360°) to a base MIDI note in the current scale.
 * Hue is mapped linearly across the scale: 0° → lowest note, 360° → highest note.
 * This creates a "colour keyboard" where warm colours are low, cool colours are high.
 */
function hueToNote(hue) {
  const scale = SCALES[options.scale] || SCALES.C_major_pentatonic;
  const normalizedHue = ((hue % 360) + 360) % 360;
  const index = Math.floor((normalizedHue / 360) * scale.length);
  return scale[Math.max(0, Math.min(scale.length - 1, index))];
}

/**
 * Map column_height (0-1) to a pitch offset in scale steps.
 * Applies a contrast stretch so vertical position produces audible pitch variation
 * (roughly ±1.5 octaves across the full scale).
 */
function heightToOffset(height) {
  const centered = (height - 0.5) * 2.5;           // amplify deviation from centre
  const clamped  = Math.max(-1, Math.min(1, centered));
  return Math.round(clamped * 10);                  // ±10 scale steps
}

/**
 * Apply pitch offset (in scale steps) to a base MIDI note, clamped to scale bounds.
 */
function applyOffset(baseNote, offset) {
  const scale = SCALES[options.scale] || SCALES.C_major_pentatonic;
  const baseIndex = scale.indexOf(baseNote);
  if (baseIndex === -1) return baseNote;
  const newIndex = Math.max(0, Math.min(scale.length - 1, baseIndex + offset));
  return scale[newIndex];
}

/**
 * Map brightness (0-1) to gain (0-1) with a sqrt curve (perceptually even).
 */
function brightnessToGain(brightness) {
  return Math.sqrt(Math.max(0, Math.min(1, brightness)));
}

/**
 * Map motion (0-1, usually very small raw values) to rhythm parameters.
 * Returns { noteLength, attackTime, releaseTime }.
 * High motion → short, percussive notes. Low motion → longer, sustained notes.
 *
 * motionGain=20 is calibrated for raw pipeline motion values (typically 0–0.05).
 */
function motionToRhythm(motion) {
  const scaled = Math.min(1, motion * options.motionGain);
  return {
    noteLength:  Math.max(0.1,   1.5  - scaled * 1.2),   // 1.5s → 0.3s
    attackTime:  Math.max(0.005, 0.05 - scaled * 0.04),  // 50ms → 10ms
    releaseTime: Math.max(0.05,  0.2  - scaled * 0.1),   // 200ms → 100ms
  };
}

/**
 * Convert MIDI note number to frequency (Hz). A4 = MIDI 69 = 440 Hz.
 */
function midiToFreq(midi) {
  return 440 * Math.pow(2, (midi - 69) / 12);
}

/**
 * Choose oscillator type from hue + saturation for legacy "auto" mode.
 * - Warm (0-60°, 300-360°) → square  (urban, bright)
 * - Cool (180-270°)        → sine    (smooth, oceanic)
 * - Others                 → triangle (balanced)
 * - Low saturation         → sine    (desaturated = soft, ambient)
 */
function autoOscillatorType(hue, saturation) {
  if (saturation < 0.3) return 'sine';
  const h = ((hue % 360) + 360) % 360;
  if (h <= 60 || h >= 300) return 'square';
  if (h >= 180 && h <= 270) return 'sine';
  return 'triangle';
}

/**
 * Clamp a value to [min, max].
 */
function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

// ══════════════════════════════════════════════════════════════════════
// INSTRUMENT PRESETS (B2)
// ══════════════════════════════════════════════════════════════════════

/**
 * All presets share identical pitch/rhythm/pan mapping.
 * Only the synthesis (timbre) differs.
 *
 * Each preset exposes one function:
 *   schedulePresetNote(ctx, instrumentGainNode, frequency, gain, pan, rhythm, startTime, motion)
 * which creates its own oscillators, filters, and envelopes, and returns the
 * primary oscillator (for activeNotes tracking and stopAll()).
 */

// ── synth_pad ──────────────────────────────────────────────────────────
// Triangle + sine oscillator, per-voice lowpass filter whose cutoff tracks brightness.
// Slow attack (90ms) + exponential decay (700ms) → warm, ambient pad.
function _scheduleSynthPad(ctx, destNode, frequency, gain, pan, rhythm, startTime, brightness) {
  const osc1 = ctx.createOscillator();
  osc1.type = 'triangle';
  osc1.frequency.value = frequency;

  const osc2 = ctx.createOscillator();
  osc2.type = 'sine';
  osc2.frequency.value = frequency;

  // Per-voice lowpass: cutoff follows strip brightness (brighter = richer harmonics)
  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  const cutoff = 400 + clamp(brightness, 0, 1) * (4500 - 400);
  filter.frequency.setValueAtTime(cutoff, startTime);

  const gainNode = ctx.createGain();
  gainNode.gain.value = 0;

  const panNode = ctx.createStereoPanner();
  panNode.pan.value = clamp(pan, -1, 1);

  // Wire: [osc1, osc2] → filter → gainNode → panNode → instrumentGain
  osc1.connect(filter);
  osc2.connect(filter);
  filter.connect(gainNode);
  gainNode.connect(panNode);
  panNode.connect(destNode);

  // ADSR: 90ms attack, 700ms exponential decay, sustain ~0.3, release from rhythm
  const { noteLength, attackTime, releaseTime } = rhythm;
  const attackMs  = 0.09;
  const decayMs   = 0.7;
  const sustainLv = 0.3;
  const peakGain  = gain * 0.3;
  const t0 = startTime;
  const t1 = t0 + attackMs;
  const t2 = t1 + decayMs;
  const sustainTime = Math.max(0.05, noteLength - attackMs - decayMs - releaseTime);
  const t3 = t2 + sustainTime;
  const t4 = t3 + releaseTime;

  gainNode.gain.setValueAtTime(0, t0);
  gainNode.gain.linearRampToValueAtTime(peakGain, t1);
  gainNode.gain.setTargetAtTime(peakGain * sustainLv, t1, decayMs / 3);
  gainNode.gain.setValueAtTime(peakGain * sustainLv, t3);
  gainNode.gain.linearRampToValueAtTime(0.0001, t4);

  osc1.start(t0); osc1.stop(t4);
  osc2.start(t0); osc2.stop(t4);

  osc1.onended = () => {
    osc1.disconnect(); osc2.disconnect();
    filter.disconnect(); gainNode.disconnect(); panNode.disconnect();
  };

  return osc1;
}

// ── mallet ────────────────────────────────────────────────────────────
// Sine fundamental + sine overtone at 2.76× + optional faint 5.4× harmonic.
// Very fast attack (5ms) + exponential decay (180ms) → bell/marimba character.
function _scheduleMallet(ctx, destNode, frequency, gain, pan, rhythm, startTime) {
  const { releaseTime } = rhythm;
  const panNode = ctx.createStereoPanner();
  panNode.pan.value = clamp(pan, -1, 1);
  panNode.connect(destNode);

  const t0  = startTime;
  const dec = 0.18;      // 180ms decay
  const dur = dec * 4 + releaseTime;

  // Fundamental
  const osc1 = ctx.createOscillator();
  osc1.type = 'sine';
  osc1.frequency.value = frequency;
  const gain1 = ctx.createGain();
  gain1.gain.setValueAtTime(0, t0);
  gain1.gain.linearRampToValueAtTime(gain * 0.3, t0 + 0.005);        // 5ms attack
  gain1.gain.setTargetAtTime(0.0001, t0 + 0.005, dec / 3);
  osc1.connect(gain1); gain1.connect(panNode);
  osc1.start(t0); osc1.stop(t0 + dur);

  // Overtone at 2.76× (~−12 dB relative to fundamental) — gives the "clang"
  const osc2 = ctx.createOscillator();
  osc2.type = 'sine';
  osc2.frequency.value = frequency * 2.76;
  const gain2 = ctx.createGain();
  gain2.gain.setValueAtTime(0, t0);
  gain2.gain.linearRampToValueAtTime(gain * 0.075, t0 + 0.005);      // 5ms attack, −12 dB
  gain2.gain.setTargetAtTime(0.0001, t0 + 0.005, 0.08 / 3);         // faster decay
  osc2.connect(gain2); gain2.connect(panNode);
  osc2.start(t0); osc2.stop(t0 + dur);

  // Faint 5.4× harmonic (~−20 dB) — adds shimmer on bright strips
  const osc3 = ctx.createOscillator();
  osc3.type = 'sine';
  osc3.frequency.value = frequency * 5.4;
  const gain3 = ctx.createGain();
  gain3.gain.setValueAtTime(0, t0);
  gain3.gain.linearRampToValueAtTime(gain * 0.03, t0 + 0.005);       // −20 dB
  gain3.gain.setTargetAtTime(0.0001, t0 + 0.005, 0.04 / 3);
  osc3.connect(gain3); gain3.connect(panNode);
  osc3.start(t0); osc3.stop(t0 + dur);

  osc1.onended = () => {
    osc1.disconnect(); osc2.disconnect(); osc3.disconnect();
    gain1.disconnect(); gain2.disconnect(); gain3.disconnect();
    panNode.disconnect();
  };

  return osc1;
}

// ── strings ───────────────────────────────────────────────────────────
// Two sawtooth oscillators detuned ±4 cents (chorus effect) + 3kHz lowpass.
// LFO vibrato whose depth tracks motion (capped at ~30 cents to avoid seasickness).
// Slow 120ms attack → orchestral string feel.
//
// Design note: motionGain=20 is calibrated for raw pipeline motion values
// (typically 0–0.05). The vibrato is capped at 30 cents (~±4.5 Hz on middle C)
// so even high-motion frames stay musical.
function _scheduleStrings(ctx, destNode, frequency, gain, pan, rhythm, startTime, motion) {
  const { noteLength, releaseTime } = rhythm;
  const attackTime = 0.12;   // 120ms attack

  const osc1 = ctx.createOscillator();
  osc1.type = 'sawtooth';
  osc1.frequency.value = frequency;
  osc1.detune.value = +4;   // cents

  const osc2 = ctx.createOscillator();
  osc2.type = 'sawtooth';
  osc2.frequency.value = frequency;
  osc2.detune.value = -4;   // cents

  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  filter.frequency.value = 3000;

  const gainNode = ctx.createGain();
  gainNode.gain.value = 0;

  const panNode = ctx.createStereoPanner();
  panNode.pan.value = clamp(pan, -1, 1);

  osc1.connect(filter); osc2.connect(filter);
  filter.connect(gainNode);
  gainNode.connect(panNode);
  panNode.connect(destNode);

  // LFO vibrato — depth capped at 30 cents (~4.5 Hz at middle C = 261 Hz)
  // vibrato_hz_at_freq = freq * (2^(cents/1200) - 1)  → cap ≈ freq * 0.0174
  const rawDepthHz = clamp(motion * options.motionGain, 0, 1) * 6;
  const maxDepthHz = frequency * 0.0174;   // ≈ 30 cents
  const lfoDepthHz = Math.min(rawDepthHz, maxDepthHz);

  const lfo = ctx.createOscillator();
  lfo.type = 'sine';
  lfo.frequency.value = 5.5;   // Hz

  const lfoGain = ctx.createGain();
  lfoGain.gain.value = lfoDepthHz;

  lfo.connect(lfoGain);
  lfoGain.connect(osc1.frequency);
  lfoGain.connect(osc2.frequency);
  lfo.start(startTime);

  // ADSR
  const peakGain    = gain * 0.3;
  const sustainTime = Math.max(0.05, noteLength - attackTime - releaseTime);
  const t0 = startTime;
  const t1 = t0 + attackTime;
  const t2 = t1 + sustainTime;
  const t3 = t2 + releaseTime;

  gainNode.gain.setValueAtTime(0, t0);
  gainNode.gain.linearRampToValueAtTime(peakGain, t1);
  gainNode.gain.setValueAtTime(peakGain, t2);
  gainNode.gain.linearRampToValueAtTime(0.0001, t3);

  osc1.start(t0); osc1.stop(t3);
  osc2.start(t0); osc2.stop(t3);
  lfo.stop(t3);

  osc1.onended = () => {
    osc1.disconnect(); osc2.disconnect();
    filter.disconnect(); gainNode.disconnect(); panNode.disconnect();
    lfo.disconnect(); lfoGain.disconnect();
  };

  return osc1;
}

// ── lab_sine ──────────────────────────────────────────────────────────
// Pure sine, no filtering. Linear 10ms attack + linear release.
// Cleanest, most transparent sound — good for data sonification demos.
function _scheduleLabSine(ctx, destNode, frequency, gain, pan, rhythm, startTime) {
  const { releaseTime } = rhythm;
  const attackTime = 0.01;   // 10ms linear attack

  const osc = ctx.createOscillator();
  osc.type = 'sine';
  osc.frequency.value = frequency;

  const gainNode = ctx.createGain();
  gainNode.gain.value = 0;

  const panNode = ctx.createStereoPanner();
  panNode.pan.value = clamp(pan, -1, 1);

  osc.connect(gainNode);
  gainNode.connect(panNode);
  panNode.connect(destNode);

  const peakGain = gain * 0.3;
  const noteLength = Math.max(0.05, rhythm.noteLength - attackTime - releaseTime);
  const t0 = startTime;
  const t1 = t0 + attackTime;
  const t2 = t1 + noteLength;
  const t3 = t2 + releaseTime;

  gainNode.gain.setValueAtTime(0, t0);
  gainNode.gain.linearRampToValueAtTime(peakGain, t1);
  gainNode.gain.setValueAtTime(peakGain, t2);
  gainNode.gain.linearRampToValueAtTime(0.0001, t3);

  osc.start(t0);
  osc.stop(t3);

  osc.onended = () => {
    osc.disconnect(); gainNode.disconnect(); panNode.disconnect();
  };

  return osc;
}

// ══════════════════════════════════════════════════════════════════════
// INSTRUMENT PRESET REGISTRY (B3)
// ══════════════════════════════════════════════════════════════════════

const INSTRUMENT_REGISTRY = [
  {
    id:          'synth_pad',
    name:        'Synth Pad',
    description: 'Warm triangle + sine pad with per-voice lowpass filter. Brightness opens the filter. Slow attack, long decay. Good for smooth, ambient Earth data.',
    sensorMatch: 'brightness, saturation',
  },
  {
    id:          'mallet',
    name:        'Mallet',
    description: 'Bell/marimba character: sine fundamental + 2.76× overtone + faint 5.4× shimmer. Fast percussive attack. Good for night lights and point-source data.',
    sensorMatch: 'edges, motion',
  },
  {
    id:          'strings',
    name:        'Strings',
    description: 'Two detuned sawtooth oscillators (±4 cents chorus) through a 3kHz lowpass. Motion-driven LFO vibrato capped at 30 cents. Slow 120ms attack. Good for ocean/wind data.',
    sensorMatch: 'motion',
  },
  {
    id:          'lab_sine',
    name:        'Lab Sine',
    description: 'Pure sine tone, no coloration. 10ms linear attack, linear release. Most transparent preset — ideal for scientific demonstrations.',
    sensorMatch: 'all',
  },
];

let _currentPresetId = 'synth_pad';

/**
 * Switch the active instrument preset.
 * Notes already playing are not changed; new calls to playFrame() use the new preset.
 *
 * @param {string} id  One of: 'synth_pad' | 'mallet' | 'strings' | 'lab_sine'
 */
export function setInstrumentPreset(id) {
  const valid = INSTRUMENT_REGISTRY.find(p => p.id === id);
  if (!valid) {
    console.warn(`synth.js: Unknown preset "${id}". Valid values: ${INSTRUMENT_REGISTRY.map(p => p.id).join(', ')}`);
    return;
  }
  _currentPresetId       = id;
  _presetExplicitlySet   = true;
}

/**
 * Get the currently active preset id.
 * @returns {string}
 */
export function getInstrumentPreset() {
  return _currentPresetId;
}

/**
 * List all available instrument presets.
 * @returns {Array<{id: string, name: string, description: string, sensorMatch: string}>}
 */
export function getAvailableInstruments() {
  return INSTRUMENT_REGISTRY.map(p => ({ ...p }));
}

// ══════════════════════════════════════════════════════════════════════
// LOW-LEVEL NOTE DISPATCH (routes to current preset)
// ══════════════════════════════════════════════════════════════════════

/**
 * Schedule one note using the active instrument preset.
 * All presets receive identical pitch/rhythm/pan; only timbre changes.
 *
 * @param {number} frequency   Hz
 * @param {number} gain        0-1
 * @param {number} pan         -1 (left) to +1 (right)
 * @param {Object} rhythm      { noteLength, attackTime, releaseTime }
 * @param {number} startTime   AudioContext time
 * @param {number} brightness  strip brightness (for filter cutoff in synth_pad)
 * @param {number} motion      frame motion (for LFO depth in strings)
 */
function _scheduleNote(frequency, gain, pan, rhythm, startTime, brightness, motion) {
  if (!audioContext || !instrumentGain) return;

  let primaryOsc;
  switch (_currentPresetId) {
    case 'mallet':
      primaryOsc = _scheduleMallet(audioContext, instrumentGain, frequency, gain, pan, rhythm, startTime);
      break;
    case 'strings':
      primaryOsc = _scheduleStrings(audioContext, instrumentGain, frequency, gain, pan, rhythm, startTime, motion);
      break;
    case 'lab_sine':
      primaryOsc = _scheduleLabSine(audioContext, instrumentGain, frequency, gain, pan, rhythm, startTime);
      break;
    case 'synth_pad':
    default:
      primaryOsc = _scheduleSynthPad(audioContext, instrumentGain, frequency, gain, pan, rhythm, startTime, brightness);
      break;
  }

  if (primaryOsc) {
    activeNotes.push({ osc: primaryOsc });
    primaryOsc.addEventListener('ended', () => {
      const idx = activeNotes.findIndex(n => n.osc === primaryOsc);
      if (idx !== -1) activeNotes.splice(idx, 1);
    });
  }
}

// Legacy playNote kept for internal backward compat (unused by new code, but safe)
function playNote(frequency, gain, pan, oscType, rhythm, startTime) {
  // Map legacy oscType → preset id (deprecated path)
  let savedPreset = _currentPresetId;
  let savedExplicit = _presetExplicitlySet;
  if (!_presetExplicitlySet) {
    switch (oscType) {
      case 'sine':     _currentPresetId = 'lab_sine';   break;
      case 'square':   _currentPresetId = 'mallet';     break;
      case 'triangle': _currentPresetId = 'synth_pad';  break;
    }
  }
  _scheduleNote(frequency, gain, pan, rhythm, startTime, gain, 0);
  _currentPresetId     = savedPreset;
  _presetExplicitlySet = savedExplicit;
}

// ══════════════════════════════════════════════════════════════════════
// MAIN PLAYBACK FUNCTION
// ══════════════════════════════════════════════════════════════════════

/**
 * Play one frame's worth of audio, scheduled on the AudioContext clock.
 *
 * Backward compat: oscillatorType option is still read as a deprecated fallback.
 * Explicit setInstrumentPreset() always takes priority.
 *
 * @param {Object} features    Feature object from extractFeatures() or JSON
 * @param {number} [frameIndex]   Current frame number (optional, for timing stagger)
 * @param {number} [totalFrames]  Total frame count (optional, unused in scheduling)
 */
export function playFrame(features, frameIndex, totalFrames) {
  if (!audioContext) {
    console.warn('synth.js: AudioContext not initialized. Call initAudio() first.');
    return;
  }
  if (!features || typeof features !== 'object') {
    console.warn('synth.js: Invalid features object:', features);
    return;
  }

  const {
    brightness   = 0.5,
    hue          = 180,
    saturation   = 0.5,
    motion       = 0,
    columns      = [],
    column_heights = [],
    column_hues  = [],
  } = features;

  // Silence very dark frames
  if (brightness < 0.05 || columns.length === 0) return;

  // ── Deprecated oscillatorType back-compat mapping ──
  // Only applies if the user has NOT explicitly called setInstrumentPreset().
  if (!_presetExplicitlySet) {
    const legacyType = options.oscillatorType;
    if      (legacyType === 'sine')     _currentPresetId = 'lab_sine';
    else if (legacyType === 'square')   _currentPresetId = 'mallet';
    else if (legacyType === 'auto') {
      const autoType = autoOscillatorType(hue, saturation);
      if      (autoType === 'sine')     _currentPresetId = 'lab_sine';
      else if (autoType === 'square')   _currentPresetId = 'mallet';
      else                              _currentPresetId = 'synth_pad';
    }
    else /* triangle / default */       _currentPresetId = 'synth_pad';
  }

  // ── 1. Select strips to play (brightest N strips above threshold) ──
  const nStrips    = columns.length;
  const candidates = [];
  for (let i = 0; i < nStrips; i++) {
    const sb = columns[i] || 0;
    if (sb >= options.brightnessThreshold) candidates.push({ index: i, brightness: sb });
  }
  candidates.sort((a, b) => b.brightness - a.brightness);
  const selectedStrips = candidates.slice(0, options.maxVoices);
  if (selectedStrips.length === 0) return;

  // ── 2. Rhythm parameters from motion ──
  const rhythm = motionToRhythm(motion);

  // ── 3. Schedule notes across the frame's 1-second window ──
  const now         = audioContext.currentTime;
  const frameStart  = now + 0.01;   // small offset to avoid scheduling in the past
  const notesPerFrame = Math.max(1, Math.floor(1 + motion * options.motionGain * 2));
  const timeStep    = 1.0 / notesPerFrame;

  selectedStrips.forEach((strip, voiceIndex) => {
    const i              = strip.index;
    const stripBrightness = strip.brightness;

    // ── Pitch ──
    // Use strip hue if available and strip is bright; otherwise use frame hue
    let stripHue = hue;
    if (column_hues.length > i && stripBrightness >= options.brightnessThreshold) {
      stripHue = column_hues[i];
    }
    const baseNote  = hueToNote(stripHue);
    let finalNote   = baseNote;
    if (column_heights.length > i && !isNaN(column_heights[i])) {
      finalNote = applyOffset(baseNote, heightToOffset(column_heights[i]));
    }
    const frequency = midiToFreq(finalNote);

    // ── Volume & pan ──
    const gain = brightnessToGain(stripBrightness * brightness);
    const pan  = nStrips > 1 ? (i / (nStrips - 1)) * 2 - 1 : 0;   // -1 (left) to +1 (right)

    // ── Schedule note(s) ──
    const noteCount = Math.max(1, Math.ceil(notesPerFrame / selectedStrips.length));
    for (let n = 0; n < noteCount; n++) {
      const startTime = frameStart + voiceIndex * 0.02 + n * timeStep;
      _scheduleNote(frequency, gain, pan, rhythm, startTime, stripBrightness, motion);
    }
  });
}

// ══════════════════════════════════════════════════════════════════════
// LIVE RECORD PLAYBACK (B3)
// ══════════════════════════════════════════════════════════════════════

let _livePlaybackState = null;

/**
 * Connect an <audio> element to the audio graph and sync live synthesis to it.
 * On each 'timeupdate' event, the current frame is determined from currentTime
 * and playFrame() is called for newly entered frames.
 *
 * @param {HTMLMediaElement} audioEl   The <audio> (or <video>) element to follow
 * @param {Array}            frames    Array of feature-frame objects
 * @param {Object}           [opts]
 * @param {number}           [opts.fps=1]  Frames per second (matches JSON "fps" field)
 */
export function playRecordLive(audioEl, frames, opts = {}) {
  stopRecordLive();   // clean up any previous live session

  if (!audioContext) initAudio();

  const fps           = opts.fps || frames._fps || 1;
  const secondsPerFrame = 1 / fps;

  // Connect element through the shared graph (safe to call repeatedly)
  const source = connectAudioElement(audioEl);

  let lastFrameIndex = -1;

  function onTimeUpdate() {
    const frameIndex = Math.floor(audioEl.currentTime / secondsPerFrame);
    if (frameIndex === lastFrameIndex) return;   // same frame, skip
    lastFrameIndex = frameIndex;
    if (frameIndex >= 0 && frameIndex < frames.length) {
      playFrame(frames[frameIndex], frameIndex, frames.length);
    }
  }

  function onEnded() {
    stopRecordLive();
  }

  audioEl.addEventListener('timeupdate', onTimeUpdate);
  audioEl.addEventListener('ended',      onEnded);

  _livePlaybackState = { audioEl, source, onTimeUpdate, onEnded };
}

/**
 * Stop live record playback and clean up event listeners.
 * The <audio> element is not paused — only synthesis and listeners are removed.
 */
export function stopRecordLive() {
  if (!_livePlaybackState) return;
  const { audioEl, onTimeUpdate, onEnded } = _livePlaybackState;
  audioEl.removeEventListener('timeupdate', onTimeUpdate);
  audioEl.removeEventListener('ended',      onEnded);
  stopAll();
  _livePlaybackState = null;
}

// ══════════════════════════════════════════════════════════════════════
// EXPORT SUMMARY
// ══════════════════════════════════════════════════════════════════════
//
// ── Core lifecycle ────────────────────────────────────────────────────
//   initAudio()                           → AudioContext
//     Must be called from a user gesture. Safe to call multiple times.
//
//   stopAll()
//     Hard-stops all active oscillators and clears the voice list.
//
// ── Options ──────────────────────────────────────────────────────────
//   setOptions(opts: Partial<Options>)
//     Partial update. masterVolume is applied immediately to the gain node.
//
//   getOptions()                          → Options (copy)
//
// ── Playback ─────────────────────────────────────────────────────────
//   playFrame(features, frameIndex?, totalFrames?)
//     Synthesise one frame. Features must have { brightness, hue, columns[] }.
//     All other fields have safe defaults (see top of file).
//
//   playRecordLive(audioEl, frames, { fps? })
//     Sync live synthesis to an <audio> element's playback position.
//
//   stopRecordLive()
//     Remove listeners and stop live synthesis (does not pause the element).
//
// ── Audio graph ──────────────────────────────────────────────────────
//   getAudioAnalyser()                    → AnalyserNode | null
//     The shared AnalyserNode (fftSize=2048, smoothing=0.8).
//     Sits BEFORE masterGain — visuals don't respond to volume slider.
//     Returns null before initAudio(); never throws.
//
//   connectAudioElement(audioEl)          → MediaElementAudioSourceNode
//     Routes an <audio> element through the shared graph. Idempotent.
//
// ── Instrument presets ────────────────────────────────────────────────
//   setInstrumentPreset(id)
//     'synth_pad' | 'mallet' | 'strings' | 'lab_sine'
//
//   getInstrumentPreset()                 → string
//
//   getAvailableInstruments()             → Array<{id, name, description, sensorMatch}>
//
// ── Compat aliases ────────────────────────────────────────────────────
//   getAudioContext()   → same as initAudio()   [deprecated]
//   setMasterVolume(v)  → setOptions({masterVolume: v})
//   setMuted(muted)     → toggles masterVolume to 0 / restores saved value
//
// ── Deprecated ───────────────────────────────────────────────────────
//   options.oscillatorType  — still read as a fallback when no preset was explicitly
//     set via setInstrumentPreset(). Maps: triangle→synth_pad, sine→lab_sine,
//     square→mallet, auto→auto-detected preset. Will be removed in a future version.
