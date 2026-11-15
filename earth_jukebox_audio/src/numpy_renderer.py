"""
numpy_renderer.py — Fallback audio renderer for Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

Dependencies: numpy, scipy ONLY.
No FluidSynth, no MIDI, no soundfonts required.

Renders a list of NoteEvent objects to a stereo 44100 Hz numpy float32 array.
Every synthesis step is commented for the demo.
"""

import numpy as np
from scipy.signal import butter, lfilter
from scipy.io import wavfile


# ---------------------------------------------------------------------------
# ADSR Envelope
# ---------------------------------------------------------------------------

def mix_into(buffer: np.ndarray, signal: np.ndarray, start_sample: int) -> int:
    """
    Mix (add) signal into buffer starting at start_sample.
    Clamps start_sample to >= 0.
    Computes n = min(len(signal), len(buffer) - start_sample).
    Returns n (samples added). If n <= 0, returns 0 without modifying buffer.
    Adds only signal[:n]. Applies to both channels for stereo.
    """
    start = int(max(0, start_sample))
    buf_len = buffer.shape[-1]
    sig_len = signal.shape[-1]

    if start >= buf_len:
        return 0

    n = min(sig_len, buf_len - start)
    if n <= 0:
        return 0

    if buffer.ndim == 1:
        if signal.ndim == 1:
            buffer[start : start + n] += signal[:n]
        else:
            buffer[start : start + n] += signal[0, :n]
    elif buffer.ndim == 2:
        if signal.ndim == 1:
            buffer[0, start : start + n] += signal[:n]
            buffer[1, start : start + n] += signal[:n]
        elif signal.ndim == 2:
            buffer[0, start : start + n] += signal[0, :n]
            buffer[1, start : start + n] += signal[1, :n]

    return n


def adsr_envelope(
    n_samples: int,
    sample_rate: int,
    attack: float,
    decay: float,
    sustain_level: float,
    release: float,
    total_duration: float = 0.0,
) -> np.ndarray:
    """
    Shapes volume over time so notes don't click on/off abruptly.

    Attack  : ramp from 0 → 1
    Decay   : ramp from 1 → sustain_level
    Sustain : hold at sustain_level for the body of the note
    Release : ramp from sustain_level → 0 at the note's tail

    Returns a float32 array of length n_samples in [0, 1].
    """
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)

    envelope = np.zeros(n_samples, dtype=np.float32)

    # Convert time sections to sample counts; clamp sequentially so they fit in n_samples
    attack_samples = max(0, min(int(attack * sample_rate), n_samples))
    rem = n_samples - attack_samples
    decay_samples = max(0, min(int(decay * sample_rate), rem))
    rem -= decay_samples
    release_samples = max(0, min(int(release * sample_rate), rem))
    sustain_samples = max(0, rem - release_samples)

    idx = 0

    # --- Attack: linear ramp 0 → 1
    if attack_samples > 0:
        envelope[idx : idx + attack_samples] = np.linspace(0.0, 1.0, attack_samples, dtype=np.float32)
        idx += attack_samples

    # --- Decay: linear ramp 1 → sustain_level
    if decay_samples > 0:
        envelope[idx : idx + decay_samples] = np.linspace(1.0, sustain_level, decay_samples, dtype=np.float32)
        idx += decay_samples

    # --- Sustain: hold at sustain_level
    if sustain_samples > 0:
        envelope[idx : idx + sustain_samples] = sustain_level
        idx += sustain_samples

    # --- Release: linear ramp sustain_level → 0
    if release_samples > 0:
        actual_rel = min(release_samples, n_samples - idx)
        if actual_rel > 0:
            envelope[idx : idx + actual_rel] = np.linspace(sustain_level, 0.0, actual_rel, dtype=np.float32)
            idx += actual_rel

    return envelope


# ---------------------------------------------------------------------------
# Schroeder Reverb (comb + allpass)
# ---------------------------------------------------------------------------

def apply_reverb(
    signal: np.ndarray,
    sample_rate: int,
    reverb_amount: float = 0.25,
) -> np.ndarray:
    """
    Simple convolution reverb approximation using a Schroeder network.

    Architecture:
      • 4 parallel comb filters (different delays → "room echo" density)
      • 1 series allpass filter (diffuses the echo → smooths out flutter)

    Mix: dry * (1 - reverb_amount) + wet * reverb_amount

    Works on a 2-D (2, N) stereo array; each channel processed identically.
    """
    # Comb filter parameters: (delay_seconds, feedback_coefficient)
    comb_params = [
        (0.030, 0.84),
        (0.034, 0.84),
        (0.038, 0.84),
        (0.043, 0.84),
    ]
    # Allpass filter parameters: (delay_seconds, coefficient)
    allpass_delay   = 0.010
    allpass_coeff   = 0.7

    def _comb_filter(x: np.ndarray, delay_s: float, fb: float) -> np.ndarray:
        """Feedback comb filter — adds delayed copies of itself back in."""
        delay_samples = int(delay_s * sample_rate)
        out = np.zeros_like(x)
        for i in range(len(x)):
            # Mix current sample with the feedback tap from 'delay_samples' ago
            past = out[i - delay_samples] if i >= delay_samples else 0.0
            out[i] = x[i] + fb * past
        return out

    def _allpass_filter(x: np.ndarray, delay_s: float, coeff: float) -> np.ndarray:
        """
        Schroeder allpass — preserves spectral density while dispersing timing.
        Transfer function: H(z) = (-coeff + z^-M) / (1 - coeff * z^-M)
        """
        delay_samples = int(delay_s * sample_rate)
        out = np.zeros_like(x)
        for i in range(len(x)):
            delayed_in  = x[i - delay_samples]   if i >= delay_samples else 0.0
            delayed_out = out[i - delay_samples]  if i >= delay_samples else 0.0
            out[i] = -coeff * x[i] + delayed_in + coeff * delayed_out
        return out

    def _process_channel(ch: np.ndarray) -> np.ndarray:
        # Sum the outputs of all 4 parallel comb filters
        wet = np.zeros_like(ch)
        for delay_s, fb in comb_params:
            wet += _comb_filter(ch, delay_s, fb)
        # Normalise comb sum to avoid amplitude explosion
        wet /= len(comb_params)
        # Pass through the single allpass for diffusion
        wet = _allpass_filter(wet, allpass_delay, allpass_coeff)
        return wet

    if signal.ndim == 1:
        # Mono path (shouldn't happen in normal use)
        wet = _process_channel(signal.astype(np.float64))
        return (signal * (1.0 - reverb_amount) + wet * reverb_amount).astype(signal.dtype)

    # Stereo path: process each channel independently
    result = np.zeros_like(signal)
    for ch_idx in range(signal.shape[0]):
        ch = signal[ch_idx].astype(np.float64)
        wet = _process_channel(ch)
        result[ch_idx] = (ch * (1.0 - reverb_amount) + wet * reverb_amount).astype(signal.dtype)
    return result


# ---------------------------------------------------------------------------
# Equal-power pan helper (inlined from mapping.py — no cross-import)
# ---------------------------------------------------------------------------

def _pan_stereo(mono: np.ndarray, pan: float) -> np.ndarray:
    """
    Equal-power panning.

    pan: -1.0 (full left) … 0.0 (centre) … +1.0 (full right)

    Returns array of shape (2, len(mono)) where
      row 0 = left channel,  row 1 = right channel.

    Equal-power law: L = cos(θ), R = sin(θ), θ in [0, π/2]
    This keeps perceived loudness constant as the pan sweeps.
    """
    # Map pan from [-1, 1] to angle [0, π/2]
    angle = (pan + 1.0) * (np.pi / 4.0)
    left_gain  = np.cos(angle)
    right_gain = np.sin(angle)

    stereo = np.zeros((2, len(mono)), dtype=np.float32)
    stereo[0] = mono * left_gain   # left
    stereo[1] = mono * right_gain  # right
    return stereo


# ---------------------------------------------------------------------------
# Instrument synthesis helpers
# ---------------------------------------------------------------------------

def _sine_wave(frequency: float, n_samples: int, sample_rate: int) -> np.ndarray:
    """Pure sine wave — smooth, flute-like tone."""
    t = np.arange(n_samples) / sample_rate
    return np.sin(2.0 * np.pi * frequency * t).astype(np.float32)


def _triangle_wave(frequency: float, n_samples: int, sample_rate: int) -> np.ndarray:
    """
    Triangle wave — bright but softer than a square wave.
    Built by summing odd harmonics with alternating signs:
      Σ (-1)^k * sin((2k+1)·2πft) / (2k+1)²
    Truncated at the 9th harmonic to avoid aliasing at high pitches.
    """
    t = np.arange(n_samples) / sample_rate
    wave = np.zeros(n_samples, dtype=np.float32)
    for k in range(5):                    # harmonics: 1, 3, 5, 7, 9
        harmonic = 2 * k + 1
        sign = (-1) ** k
        wave += sign * np.sin(harmonic * 2.0 * np.pi * frequency * t).astype(np.float32) / (harmonic ** 2)
    # Normalise triangle to [-1, 1] peak (theoretical peak = π²/8 ≈ 1.234)
    wave *= (8.0 / (np.pi ** 2))
    return wave


def _lowpass_filter(signal: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """
    Butterworth IIR lowpass at cutoff_hz Hz.
    Used for the soft_pad instrument to remove harsh upper partials.
    """
    nyquist = sample_rate / 2.0
    normal_cutoff = cutoff_hz / nyquist
    # 4th-order Butterworth — gentle rolloff, no ringing
    b, a = butter(4, normal_cutoff, btype='low', analog=False)
    return lfilter(b, a, signal).astype(np.float32)


def _synthesise_note(
    frequency: float,
    n_samples: int,
    sample_rate: int,
    instrument: str,
    velocity_gain: float,
) -> np.ndarray:
    """
    Choose the correct waveform and ADSR profile for the given instrument family.

    Instrument families:
      "strings_bells" — triangle wave, slow attack, long release (bell-like shimmer)
      "soft_pad"      — sine wave with 2 kHz lowpass, slow attack, long sustain
      "blend"         — 50 % sine + 50 % triangle, medium ADSR (balanced, warm)
      default         — plain sine wave (safe fallback, universally pleasing)
    """
    instrument = (instrument or "").lower()

    if instrument == "strings_bells":
        # --- Bell / strings: triangle gives natural odd harmonics, slow attack
        # avoids a click, long release lets the note ring out naturally.
        raw = _triangle_wave(frequency, n_samples, sample_rate)
        env = adsr_envelope(
            n_samples, sample_rate,
            attack=0.12, decay=0.1, sustain_level=0.6, release=0.5,
            total_duration=n_samples / sample_rate,
        )

    elif instrument == "soft_pad":
        # --- Soft pad: pure sine (no overtones) + lowpass at 2 kHz
        # removes any residual aliasing, long attack fades in gently,
        # long release lets chords blend into one another smoothly.
        raw = _sine_wave(frequency, n_samples, sample_rate)
        raw = _lowpass_filter(raw, cutoff_hz=2000.0, sample_rate=sample_rate)
        env = adsr_envelope(
            n_samples, sample_rate,
            attack=0.20, decay=0.1, sustain_level=0.75, release=0.60,
            total_duration=n_samples / sample_rate,
        )

    elif instrument == "blend":
        # --- Blend: half sine + half triangle gives warmth without brittleness.
        # Medium ADSR: snappy enough for melodic lines, smooth enough for pads.
        sine_part     = _sine_wave(frequency, n_samples, sample_rate)
        triangle_part = _triangle_wave(frequency, n_samples, sample_rate)
        raw = 0.5 * sine_part + 0.5 * triangle_part
        env = adsr_envelope(
            n_samples, sample_rate,
            attack=0.06, decay=0.08, sustain_level=0.65, release=0.30,
            total_duration=n_samples / sample_rate,
        )

    else:
        # --- Default / fallback: simple sine wave.
        # Universally consonant; never clashes with other sounds.
        raw = _sine_wave(frequency, n_samples, sample_rate)
        env = adsr_envelope(
            n_samples, sample_rate,
            attack=0.02, decay=0.05, sustain_level=0.70, release=0.15,
            total_duration=n_samples / sample_rate,
        )

    # Apply envelope and velocity scaling
    return (raw * env.astype(np.float32) * velocity_gain).astype(np.float32)


# ---------------------------------------------------------------------------
# MIDI pitch → frequency conversion
# ---------------------------------------------------------------------------

def _pitch_to_hz(midi_pitch: int) -> float:
    """Standard equal-temperament: A4 = 440 Hz = MIDI 69."""
    return 440.0 * (2.0 ** ((midi_pitch - 69) / 12.0))


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_to_array(
    note_events: list,
    total_duration: float,
    sample_rate: int = 44100,
    reverb_amount: float = 0.25,
    seed: int = 42,
) -> np.ndarray:
    """
    Render a list of NoteEvent-like objects to a stereo float32 numpy array.

    Parameters
    ----------
    note_events     : list of NoteEvent (duck-typed — needs .time, .pitch,
                      .duration, .velocity, .instrument, .pan attributes)
    total_duration  : length of the output in seconds (frames / fps)
    sample_rate     : audio sample rate (default 44100 Hz)
    reverb_amount   : wet/dry mix for reverb (0 = dry, 1 = fully wet)
    seed            : random seed for reproducibility

    Returns
    -------
    np.ndarray of shape (2, N), dtype float32, values in [-1, 1]
    """

    # Fix random seed for reproducibility across runs
    np.random.seed(seed)

    # Allocate the buffer with an extra 3 seconds after the last frame so reverb
    # tails and long notes are not cut off.
    extra_tail_sec = 3.0
    nominal_samples = int(total_duration * sample_rate)
    alloc_samples = int((total_duration + extra_tail_sec) * sample_rate)
    buffer = np.zeros((2, alloc_samples), dtype=np.float32)

    skipped_notes = 0
    truncated_notes = 0

    for note in note_events:
        # --- Derive per-note parameters ---
        frequency    = _pitch_to_hz(int(note.pitch))          # MIDI pitch → Hz
        velocity_gain = float(note.velocity) / 127.0           # MIDI velocity → 0–1 gain
        pan          = float(getattr(note, "pan", 0.0))        # stereo position (-1 … +1)
        instrument   = str(getattr(note, "instrument", ""))    # waveform family
        duration     = float(note.duration)                    # note length in seconds

        # Convert all start positions to int sample indices
        start_sample = int(round(float(note.time) * sample_rate))

        if start_sample >= alloc_samples or start_sample < 0:
            skipped_notes += 1
            continue

        desired_samples = int(round(duration * sample_rate))
        if desired_samples <= 0:
            skipped_notes += 1
            continue

        # Synthesise the mono note signal (waveform + ADSR + velocity)
        mono_note = _synthesise_note(
            frequency, desired_samples, sample_rate, instrument, velocity_gain
        )

        # Pan to stereo using equal-power law
        stereo_note = _pan_stereo(mono_note, pan)  # shape (2, desired_samples)

        # Mix (add) using the shared mix_into helper
        mixed_n = mix_into(buffer, stereo_note, start_sample)
        if mixed_n < desired_samples:
            truncated_notes += 1

    # Store stats on render module for diagnostics
    render_to_array.last_skipped = skipped_notes
    render_to_array.last_truncated = truncated_notes

    # Apply Schroeder reverb to the full mixed buffer (including tail)
    buffer = apply_reverb(buffer, sample_rate, reverb_amount)

    # Trim trailing silence while keeping final duration equal to number of frames / fps (plus at most tail)
    mag = np.max(np.abs(buffer), axis=0)
    # Threshold for silence detection in tail
    active_indices = np.where(mag > 0.005)[0]
    last_active = active_indices[-1] if len(active_indices) > 0 else nominal_samples

    # Keep at least nominal duration, and at most nominal + 0.8s tail (so duration stays within 1s of len(frames)/fps)
    max_tail_samples = int(0.8 * sample_rate)
    if last_active > nominal_samples:
        cutoff = min(last_active + int(0.1 * sample_rate), nominal_samples + max_tail_samples)
    else:
        cutoff = nominal_samples
    cutoff = min(cutoff, alloc_samples)

    # Apply short fade-out at the very end of the cut to prevent any click
    fade_len = min(int(0.05 * sample_rate), cutoff)
    if fade_len > 0:
        fade = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        buffer[:, cutoff - fade_len : cutoff] *= fade

    # Trim to final cutoff
    buffer = buffer[:, :cutoff]

    # Normalise: bring peak to ≤ 0.95 to prevent digital clipping.
    # Skip if the signal is completely silent (avoids divide-by-zero).
    peak = np.max(np.abs(buffer))
    if peak > 0.95:
        buffer = (buffer / peak) * 0.95

    return buffer


# ---------------------------------------------------------------------------
# Diagnostic: normalize_and_check
# ---------------------------------------------------------------------------

def normalize_and_check(signal: np.ndarray) -> dict:
    """
    Inspect a rendered signal and return quality metrics.

    Returns
    -------
    dict with keys:
      "peak"       : float — maximum absolute sample value
      "is_silent"  : bool  — True if peak < 0.001 (effectively nothing rendered)
      "is_clipped" : bool  — True if any sample exceeds 0.99 (potential distortion)
      "rms"        : float — root-mean-square level (perceived loudness proxy)
    """
    peak = float(np.max(np.abs(signal)))
    rms  = float(np.sqrt(np.mean(signal ** 2)))
    return {
        "peak":       peak,
        "is_silent":  peak < 0.001,
        "is_clipped": bool(np.any(np.abs(signal) > 0.99)),
        "rms":        rms,
    }


# ---------------------------------------------------------------------------
# WAV export
# ---------------------------------------------------------------------------

def save_wav(signal: np.ndarray, path: str, sample_rate: int = 44100) -> None:
    """
    Save a float32 stereo array to a 16-bit PCM WAV file.

    signal : shape (2, N), float32, values in [-1, 1]
    path   : output file path (e.g. "out/night_lights.wav")

    scipy.io.wavfile.write expects shape (N, channels), so we transpose.
    int16 range is [-32768, 32767]; multiplying by 32767 maps [-1, 1] cleanly.
    """
    # Transpose from (2, N) → (N, 2) as required by scipy wavfile
    stereo_T = signal.T                              # shape (N, 2)

    # Convert float32 → int16: scale by 32767, clip to avoid overflow
    int16_data = np.clip(stereo_T * 32767.0, -32768, 32767).astype(np.int16)

    # Write WAV — scipy handles the RIFF header automatically
    wavfile.write(path, sample_rate, int16_data)


# ---------------------------------------------------------------------------
# Quick self-test (run with: python numpy_renderer.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class _TestNote:
        """Minimal NoteEvent stand-in for the self-test."""
        time: float
        pitch: int
        duration: float
        velocity: int
        instrument: str
        pan: float

    # Build a short chord sequence covering all three instrument families
    test_events = [
        _TestNote(time=0.0,  pitch=60, duration=1.5, velocity=90,  instrument="strings_bells", pan=-0.4),
        _TestNote(time=0.2,  pitch=64, duration=1.5, velocity=80,  instrument="soft_pad",      pan= 0.0),
        _TestNote(time=0.4,  pitch=67, duration=1.5, velocity=85,  instrument="blend",         pan= 0.4),
        _TestNote(time=1.0,  pitch=55, duration=1.0, velocity=100, instrument="strings_bells", pan=-0.6),
        _TestNote(time=1.5,  pitch=72, duration=0.8, velocity=70,  instrument="",              pan= 0.3),
    ]

    print("Rendering test sequence (3 s)...")
    audio = render_to_array(test_events, total_duration=3.0, reverb_amount=0.3)
    print(f"  Output shape : {audio.shape}  dtype: {audio.dtype}")

    metrics = normalize_and_check(audio)
    print(f"  Peak         : {metrics['peak']:.4f}")
    print(f"  RMS          : {metrics['rms']:.4f}")
    print(f"  Silent?      : {metrics['is_silent']}")
    print(f"  Clipped?     : {metrics['is_clipped']}")

    out_path = "out/self_test.wav"
    import os; os.makedirs("out", exist_ok=True)
    save_wav(audio, out_path)
    print(f"  Saved WAV    : {out_path}")
    print("Self-test passed.")
