"""
then_vs_now.py — Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

Compare two Night Lights features.json files (e.g. 2012 vs 2023) and output
a single stereo track where:
  LEFT  channel = 2012 audio   (file A)
  RIGHT channel = 2023 audio   (file B)

Growth areas (B brighter than A) receive richer harmony.
Strong differences receive a subtle dissonant accent.

CLI Usage:
    python then_vs_now.py --a data/night_lights_2012.json \\
                          --b data/night_lights_2023.json \\
                          --out out/then_vs_now.mp3
    python then_vs_now.py --a data/night_lights_2012.json \\
                          --b data/night_lights_2023.json \\
                          --out out/then_vs_now.mp3 \\
                          --backend numpy
"""

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Allow running the script from any working directory by ensuring the project
# root (the directory that contains this file) is on the import path.  This
# lets Python find the src/ package regardless of where the user invokes the
# script from.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

# --- Project-internal imports -----------------------------------------------
from src import loader
from src import mapping
from src.mapping import NoteEvent

# Renderer: prefer numpy_renderer (pure-Python, no native deps); fall back to
# fluidsynth_renderer when --backend fluidsynth is requested or if the numpy
# renderer is unavailable.
try:
    from src import numpy_renderer
    from src.numpy_renderer import mix_into
    _NUMPY_RENDERER_AVAILABLE = True
except ImportError:
    _NUMPY_RENDERER_AVAILABLE = False
    def mix_into(buffer: np.ndarray, signal: np.ndarray, start_sample: int) -> int:
        start = int(max(0, start_sample))
        buf_len = buffer.shape[-1]
        sig_len = signal.shape[-1]
        if start >= buf_len:
            return 0
        n = min(sig_len, buf_len - start)
        if n <= 0:
            return 0
        buffer[..., start : start + n] += signal[..., :n]
        return n

try:
    from src import fluidsynth_renderer
    _FLUID_RENDERER_AVAILABLE = True
except ImportError:
    _FLUID_RENDERER_AVAILABLE = False

# scales: quantize() maps a [0,1] value to a MIDI note number on a named scale.
try:
    from src.scales import quantize as scales_quantize
    from src.scales import scale_notes as get_scale_notes
    _SCALES_AVAILABLE = True
except ImportError:
    _SCALES_AVAILABLE = False

from src import export


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE: int = 44100          # samples per second for all audio buffers
FRAME_DURATION: float = 1.0       # default seconds per frame (60 BPM ≈ 1 s/beat)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the Then vs Now comparator."""
    parser = argparse.ArgumentParser(
        description=(
            "Earth Jukebox — Then vs Now: compare two Night Lights JSON files "
            "and render a stereo track (left=A, right=B)."
        )
    )
    parser.add_argument(
        "--a", required=True, metavar="PATH",
        help="Path to the earlier features.json (e.g. data/night_lights_2012.json)"
    )
    parser.add_argument(
        "--b", required=True, metavar="PATH",
        help="Path to the later features.json (e.g. data/night_lights_2023.json)"
    )
    parser.add_argument(
        "--out", required=True, metavar="PATH",
        help="Output file path; .mp3 or .wav (e.g. out/then_vs_now.mp3)"
    )
    parser.add_argument(
        "--backend", choices=["auto", "numpy", "fluidsynth"], default="auto",
        help=(
            "Audio synthesis backend.  'auto' (default) uses numpy if available, "
            "then fluidsynth.  'numpy' forces the pure-Python renderer.  "
            "'fluidsynth' requires a FluidSynth installation."
        )
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_pair(data_a: dict, data_b: dict) -> None:
    """
    Cross-validate two loaded features.json dicts for compatibility.

    Raises ValueError with a clear message on any mismatch so the user
    knows exactly what to fix before we start processing.
    """
    # Both files must carry the same record name so we know they represent
    # the same NASA dataset at different times.
    if data_a["record"] != data_b["record"]:
        raise ValueError(
            f"Record name mismatch: file A is '{data_a['record']}' "
            f"but file B is '{data_b['record']}'. "
            "Both files must refer to the same record (e.g. 'night_lights')."
        )

    # Both files must have the same number of spatial strips per frame so
    # strip-level difference arithmetic is well-defined.
    frames_a = data_a["frames"]
    frames_b = data_b["frames"]

    # Pick the first frame of each file for the strip-count check.
    n_strips_a = len(frames_a[0].get("columns", []))
    n_strips_b = len(frames_b[0].get("columns", []))

    if n_strips_a != n_strips_b:
        raise ValueError(
            f"Strip count mismatch: file A has {n_strips_a} strips per frame "
            f"but file B has {n_strips_b}. "
            "Both files must have the same number of columns."
        )


def _align_frames(data_a: dict, data_b: dict) -> tuple:
    """
    Return the frame lists from both files, truncated to the shorter length.

    If the frame counts differ a warning is printed; the SHORTER dataset
    determines how many frames are processed so both channels stay in sync.
    """
    frames_a = data_a["frames"]
    frames_b = data_b["frames"]
    n_a, n_b = len(frames_a), len(frames_b)

    if n_a != n_b:
        # Warn the user so they are aware frames will be dropped.
        min_frames = min(n_a, n_b)
        print(
            f"Warning: files have different frame counts "
            f"(A={n_a}, B={n_b}). Using first {min_frames} frames."
        )
        frames_a = frames_a[:min_frames]
        frames_b = frames_b[:min_frames]

    return frames_a, frames_b


# ---------------------------------------------------------------------------
# Scale helpers (with fallback when src/scales.py is not yet available)
# ---------------------------------------------------------------------------

# Fallback C-major-pentatonic MIDI note list spanning four octaves (C3–C7).
_FALLBACK_SCALE = [
    48, 50, 52, 55, 57,   # C3 D3 E3 G3 A3
    60, 62, 64, 67, 69,   # C4 D4 E4 G4 A4
    72, 74, 76, 79, 81,   # C5 D5 E5 G5 A5
    84, 86, 88, 91, 93,   # C6 D6 E6 G6 A6
]


def _get_scale_list(scale_name: str) -> list:
    """
    Return the ordered list of MIDI note numbers for *scale_name*.

    Delegates to src.scales.scale_notes() when available; otherwise returns
    the built-in C-major-pentatonic fallback so the script is always runnable.
    """
    if _SCALES_AVAILABLE:
        try:
            return get_scale_notes(scale_name)
        except Exception:
            pass
    return list(_FALLBACK_SCALE)


def _scale_note_at_index(scale: list, base_index: int, offset: int) -> int:
    """
    Return the MIDI note number at *base_index + offset* in *scale*, clamped
    to the valid list range so we never go out of bounds.
    """
    clamped = max(0, min(len(scale) - 1, base_index + offset))
    return scale[clamped]


def _pitch_to_scale_index(scale: list, pitch: int) -> int:
    """
    Find the index in *scale* whose MIDI note number is closest to *pitch*.
    Used to anchor dissonant-accent pitches to the nearest scale tone.
    """
    return min(range(len(scale)), key=lambda i: abs(scale[i] - pitch))


def _quantize_to_scale(value: float, scale_name: str) -> tuple:
    """
    Map *value* ∈ [0, 1] to a (midi_pitch, scale_index) pair.

    Returns both the MIDI note number AND its index in the scale list so the
    caller can compute harmony intervals by index arithmetic without having
    to search the scale list a second time.
    """
    scale = _get_scale_list(scale_name)
    idx = int(round(value * (len(scale) - 1)))
    idx = max(0, min(idx, len(scale) - 1))
    return scale[idx], idx


# ---------------------------------------------------------------------------
# Note-event processing for a single frame + its counterpart
# ---------------------------------------------------------------------------

def _process_frame_pair(
    frame_a: dict,
    frame_b: dict,
    config: dict,
    scale_name: str,
    frame_duration: float,
    dissonant_toggle: list,       # mutable single-element list used as state
) -> tuple:
    """
    Process one (A, B) frame pair into two lists of NoteEvents.

    Returns
    -------
    notes_a : list[NoteEvent]  — events for the LEFT (A) channel
    notes_b : list[NoteEvent]  — events for the RIGHT (B) channel
    """
    # --- Step 3 & 4: Convert each frame to base NoteEvents ------------------
    # mapping.frame_to_notes() applies the standard brightness→velocity,
    # height→pitch, hue→instrument, motion→density pipeline.
    notes_a: list = mapping.frame_to_notes(frame_a, frame_duration, config, scale_name)
    notes_b: list = mapping.frame_to_notes(frame_b, frame_duration, config, scale_name)

    # Retrieve per-strip brightness arrays from both frames.
    cols_a = frame_a.get("columns", [])
    cols_b = frame_b.get("columns", [])
    n_strips = len(cols_a)

    # Obtain the frame timestamp (seconds from track start) for new events.
    t_a: float = float(frame_a.get("t", 0.0))
    t_b: float = float(frame_b.get("t", 0.0))
    # Use frame A's timestamp for events on both channels; they are in sync.
    t_now: float = t_a

    # Retrieve the scale note list once for the whole frame.
    scale: list = _get_scale_list(scale_name)

    # Representative instrument + channel from base notes (use first available,
    # fall back to sensible defaults if the frame produced no base notes).
    _default_instrument = "blend"
    _default_channel_a  = 0
    _default_channel_b  = 1

    if notes_a:
        ref_instrument_a = notes_a[0].instrument
        ref_channel_a    = notes_a[0].channel
    else:
        ref_instrument_a = _default_instrument
        ref_channel_a    = _default_channel_a

    if notes_b:
        ref_instrument_b = notes_b[0].instrument
        ref_channel_b    = notes_b[0].channel
    else:
        ref_instrument_b = _default_instrument
        ref_channel_b    = _default_channel_b

    # --- Step 5: Compute strip-level difference metrics ---------------------
    for i in range(n_strips):
        a_brightness: float = float(cols_a[i]) if i < len(cols_a) else 0.0
        b_brightness: float = float(cols_b[i]) if i < len(cols_b) else 0.0

        # growth[i]: how much brighter strip i got from A → B (never negative)
        growth: float = max(0.0, b_brightness - a_brightness)

        # diff_strength[i]: magnitude of the change in either direction
        diff_strength: float = abs(b_brightness - a_brightness)

        # Pan for this strip (mirrors the main mapping convention).
        if n_strips > 1:
            pan: float = (i / (n_strips - 1)) * 2.0 - 1.0
        else:
            pan = 0.0

        # Base scale index for this strip, derived from its brightness as a
        # rough pitch proxy (height data would be better but brightness is
        # always present in both files).
        col_height_a = frame_a.get("column_heights", cols_a)
        col_height_b = frame_b.get("column_heights", cols_b)
        height_a = float(col_height_a[i]) if i < len(col_height_a) else a_brightness
        height_b = float(col_height_b[i]) if i < len(col_height_b) else b_brightness

        _, base_idx_a = _quantize_to_scale(height_a, scale_name)
        _, base_idx_b = _quantize_to_scale(height_b, scale_name)

        # -----------------------------------------------------------------
        # Step 6: Harmony notes for GROWTH areas
        # When a strip grew brighter (B > A by more than 0.2), we enrich
        # both channels with extra harmonic tones:
        #   A channel gets a tone a THIRD above  (scale index + 2)
        #   B channel gets a tone a FIFTH above  (scale index + 4)
        # This richer harmony marks areas that became brighter over time.
        # -----------------------------------------------------------------
        if growth > 0.2:
            # Harmony velocity proportional to how much the strip grew.
            harmony_vel: int = int(max(1, min(127, int(growth * 90))))

            # --- A channel: third above (index + 2) ---
            third_pitch = _scale_note_at_index(scale, base_idx_a, 2)
            harmony_note_a = NoteEvent(
                time=t_now,
                duration=frame_duration * 0.4,  # brief harmonic shimmer
                pitch=third_pitch,
                velocity=harmony_vel,
                pan=pan,
                instrument=ref_instrument_a,
                channel=ref_channel_a,
            )
            notes_a.append(harmony_note_a)

            # --- B channel: fifth above (index + 4) ---
            fifth_pitch = _scale_note_at_index(scale, base_idx_b, 4)
            harmony_note_b = NoteEvent(
                time=t_now,
                duration=frame_duration * 0.4,
                pitch=fifth_pitch,
                velocity=harmony_vel,
                pan=pan,
                instrument=ref_instrument_b,
                channel=ref_channel_b,
            )
            notes_b.append(harmony_note_b)

        # -----------------------------------------------------------------
        # Step 7: Dissonant accent for STRONG DIFFERENCES
        # When the absolute brightness difference exceeds 0.4 we add a very
        # quiet note (velocity 25) on BOTH channels.  The pitch is the
        # nearest scale note ± a minor second (+1) or tritone (+6),
        # alternating between the two to avoid monotony.
        # Duration is kept very short (0.1 s) so it is audible but not
        # jarring — a subtle "tension marker" rather than an ugly clash.
        # -----------------------------------------------------------------
        if diff_strength > 0.4:
            # Alternate between +1 semitone (minor second) and +6 (tritone).
            semitone_offset = 1 if dissonant_toggle[0] % 2 == 0 else 6
            dissonant_toggle[0] += 1

            # Anchor: nearest scale note to the A-channel pitch at this strip.
            nearest_idx = _pitch_to_scale_index(scale, _scale_note_at_index(scale, base_idx_a, 0))
            nearest_midi = scale[nearest_idx]
            # Dissonant pitch = nearest scale note + semitone offset (raw MIDI,
            # intentionally outside the scale — that is the point).
            dissonant_pitch: int = min(127, nearest_midi + semitone_offset)

            dissonant_a = NoteEvent(
                time=t_now,
                duration=0.1,          # very short accent
                pitch=dissonant_pitch,
                velocity=25,           # quiet — present but not intrusive
                pan=pan,
                instrument=ref_instrument_a,
                channel=ref_channel_a,
            )
            dissonant_b = NoteEvent(
                time=t_now,
                duration=0.1,
                pitch=dissonant_pitch,
                velocity=25,
                pan=pan,
                instrument=ref_instrument_b,
                channel=ref_channel_b,
            )
            notes_a.append(dissonant_a)
            notes_b.append(dissonant_b)

    return notes_a, notes_b


# ---------------------------------------------------------------------------
# Renderer selection
# ---------------------------------------------------------------------------

def _pick_renderer(backend: str):
    """
    Return the renderer module to use based on the --backend flag.

    'auto'       → numpy if available, then fluidsynth, then error
    'numpy'      → numpy only (error if unavailable)
    'fluidsynth' → fluidsynth only (error if unavailable)
    """
    if backend == "numpy":
        if not _NUMPY_RENDERER_AVAILABLE:
            print("Error: numpy_renderer is not available. Install numpy and check src/numpy_renderer.py.")
            sys.exit(1)
        return numpy_renderer

    if backend == "fluidsynth":
        if not _FLUID_RENDERER_AVAILABLE:
            print("Error: fluidsynth_renderer is not available. Install FluidSynth and check src/fluidsynth_renderer.py.")
            sys.exit(1)
        return fluidsynth_renderer

    # 'auto': try numpy first, then fluidsynth.
    if _NUMPY_RENDERER_AVAILABLE:
        return numpy_renderer
    if _FLUID_RENDERER_AVAILABLE:
        return fluidsynth_renderer

    print(
        "Error: no audio renderer is available. "
        "Ensure src/numpy_renderer.py exists and numpy is installed."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Signal combination
# ---------------------------------------------------------------------------

def _to_stereo(signal: np.ndarray) -> np.ndarray:
    """
    Ensure *signal* is a 2-D stereo array of shape (2, N).

    The renderer may return:
      - (N,)   — mono samples
      - (2, N) — already stereo
      - (N, 2) — transposed stereo

    We normalise all three cases so downstream code always sees (2, N).
    """
    if signal.ndim == 1:
        # Mono → duplicate into both channels.
        return np.stack([signal, signal], axis=0)

    if signal.shape[0] == 2:
        # Already (2, N) — good.
        return signal

    if signal.shape[1] == 2:
        # (N, 2) → transpose to (2, N).
        return signal.T

    # Unexpected shape; treat the first row/column as a mono signal.
    flat = signal.ravel()
    return np.stack([flat, flat], axis=0)


def _combine_stereo(signal_a: np.ndarray, signal_b: np.ndarray) -> np.ndarray:
    """
    Combine two signals into one stereo track:
      out[0] = A's LEFT  channel  (the 2012 audio)
      out[1] = B's RIGHT channel  (the 2023 audio)

    If the two signals have different lengths we pad the shorter one with
    silence so the combined array covers the full duration of both.
    """
    # Normalise to (2, N) stereo.
    stereo_a = _to_stereo(signal_a.astype(np.float32))
    stereo_b = _to_stereo(signal_b.astype(np.float32))

    len_a = stereo_a.shape[1]
    len_b = stereo_b.shape[1]
    out_len = max(len_a, len_b)

    # Direct channel assignment (Left = A, Right = B)
    combined = np.zeros((2, out_len), dtype=np.float32)
    combined[0, :len_a] = stereo_a[0]
    combined[1, :len_b] = stereo_b[1]

    return combined


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Entry point: parse args, run the full then-vs-now pipeline, save output.
    """
    args = _parse_args()

    # Resolve output directory early so we fail fast on a bad path before
    # spending time processing audio.
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    # --- Step 1: Load both features.json files ------------------------------
    # loader.load() validates schema, applies defaults, and raises ValueError
    # with a clear message on any problem.
    print(f"Loading A: {args.a}")
    data_a = loader.load(args.a)

    print(f"Loading B: {args.b}")
    data_b = loader.load(args.b)

    # --- Validation: same record, same strip count --------------------------
    try:
        _validate_pair(data_a, data_b)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # --- Frame alignment: truncate to shorter dataset if needed -------------
    frames_a, frames_b = _align_frames(data_a, data_b)
    n_frames = len(frames_a)

    # --- Step 2: Load the record config -------------------------------------
    # We always load from the record name in file A (validated to match B).
    record_name: str = data_a["record"]
    config: dict = loader.load_config(record_name)
    scale_name: str = config.get("scale", "C_major_pentatonic")
    tempo_bpm: float = float(config.get("tempo_bpm", 90))

    # Derive per-frame duration from tempo so the audio timing matches the BPM.
    frame_duration: float = 60.0 / tempo_bpm

    print(
        f"Record : {record_name}  |  Scale: {scale_name}  |  "
        f"BPM: {tempo_bpm}  |  Frames: {n_frames}  |  Backend: {args.backend}"
    )

    # --- Steps 3–7: Build NoteEvent lists for A (left) and B (right) -------
    # For each frame pair we:
    #   • run the standard frame→notes mapping for A and B,
    #   • compute strip-level brightness differences,
    #   • inject harmony notes where B grew brighter than A (step 6),
    #   • inject dissonant accents where the difference is large (step 7).
    all_notes_a: list = []
    all_notes_b: list = []

    # dissonant_toggle is a mutable counter shared across frame iterations so
    # we can alternate between +1 (minor second) and +6 (tritone) offsets.
    dissonant_toggle = [0]

    for frame_idx in range(n_frames):
        notes_a, notes_b = _process_frame_pair(
            frames_a[frame_idx],
            frames_b[frame_idx],
            config,
            scale_name,
            frame_duration,
            dissonant_toggle,
        )
        all_notes_a.extend(notes_a)
        all_notes_b.extend(notes_b)

    n_notes_total = len(all_notes_a) + len(all_notes_b)
    print(f"Generated {len(all_notes_a)} note-events for A (left), "
          f"{len(all_notes_b)} for B (right).  Total: {n_notes_total}")

    # --- Select renderer ----------------------------------------------------
    renderer = _pick_renderer(args.backend)
    backend_name: str = renderer.__name__.split(".")[-1]
    print(f"Rendering with: {backend_name}")

    # --- Steps 8 & 9: Render A and B events to audio signals ----------------
    total_duration = n_frames * frame_duration
    if backend_name == "fluidsynth":
        signal_a = renderer.render_with_fluidsynth(all_notes_a, total_duration, config)
        signal_b = renderer.render_with_fluidsynth(all_notes_b, total_duration, config)
    else:
        signal_a = renderer.render_to_array(all_notes_a, total_duration, sample_rate=SAMPLE_RATE)
        signal_b = renderer.render_to_array(all_notes_b, total_duration, sample_rate=SAMPLE_RATE)

    # --- Step 10: Combine into one stereo track (left=A, right=B) -----------
    combined: np.ndarray = _combine_stereo(signal_a, signal_b)

    # --- Step 11: Normalize combined signal ---------------------------------
    # export.normalize() scales the peak to 0.9 to avoid clipping while
    # preserving relative loudness between the two channels.
    combined = export.normalize(combined, target_peak=0.9)

    # --- Step 12: Save WAV + MP3 --------------------------------------------
    # Derive WAV path alongside the requested output path so we always
    # save a lossless copy in addition to the MP3 (or WAV-only if the
    # user asked for .wav directly).
    base_path = os.path.splitext(args.out)[0]
    wav_path = base_path + ".wav"
    mp3_path = base_path + ".mp3"

    export.save_wav(combined, wav_path, sample_rate=SAMPLE_RATE)
    print(f"Saved WAV : {wav_path}")

    # Only attempt MP3 export when the output path ends in .mp3; for .wav
    # requests we stop after saving the WAV (export.save_mp3 will warn if
    # pydub/ffmpeg are missing but will not raise).
    export.save_mp3(combined, mp3_path, sample_rate=SAMPLE_RATE)
    print(f"Saved MP3 : {mp3_path}")

    # --- Summary -----------------------------------------------------------
    duration_secs = combined.shape[1] / SAMPLE_RATE
    print(
        f"\nRendered: {args.out} "
        f"[{duration_secs:.1f}s, {n_notes_total} notes, "
        f"scale={scale_name}, backend={backend_name}]"
    )


# ---------------------------------------------------------------------------
# Entry point guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
