"""
fluidsynth_renderer.py — Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

FluidSynth / pretty_midi rendering path.

Dependencies (optional — falls back gracefully if missing):
  - pretty_midi   (pip install pretty_midi)
  - FluidSynth    (system package: apt install fluidsynth / brew install fluid-synth)
  - A soundfont   (.sf2 file — see find_soundfont() for search paths)

If any of the above are missing, is_available() returns False and the caller
should fall back to the numpy synthesiser in numpy_synth.py.
"""

from __future__ import annotations

import os
import glob
import numpy as np
from pathlib import Path

# ---------------------------------------------------------------------------
# General MIDI program numbers (0-indexed, as used by pretty_midi)
# ---------------------------------------------------------------------------

INSTRUMENT_PROGRAMS: dict[str, int] = {
    "marimba": 12,
    "xylophone": 13,
    "strings": 48,
    "strings_bells": 48,
    "soft_pad": 89,
    "pad_warm": 89,
    "pad_choir": 91,
    "blend": 12,
    "none": 0,
}

# ---------------------------------------------------------------------------
# Soundfont discovery
# ---------------------------------------------------------------------------

def find_soundfont() -> str | None:
    """
    Search for a .sf2 soundfont in a prioritised list of locations.

    Search order:
      1. SOUNDFONT environment variable
      2. c:\\soundfonts\\GeneralUser.sf2
      3. c:\\soundfonts\\FluidR3_GM.sf2
      4. /usr/share/sounds/sf2/FluidR3_GM.sf2
      5. /usr/share/sounds/sf2/GeneralUser.sf2
      6. ~/soundfonts/GeneralUser.sf2
      7. Any .sf2 file in the current directory or its parent

    Returns the first path that exists, or None.
    """
    candidates: list[str] = []

    # 1. Environment variable
    env_sf = os.environ.get("SOUNDFONT")
    if env_sf:
        candidates.append(env_sf)

    # 2-3. Windows common location
    candidates.extend([
        r"c:\soundfonts\GeneralUser.sf2",
        r"c:\soundfonts\FluidR3_GM.sf2",
    ])

    # 4-5. Linux/macOS system location
    candidates.extend([
        "/usr/share/sounds/sf2/FluidR3_GM.sf2",
        "/usr/share/sounds/sf2/GeneralUser.sf2",
    ])

    # 6. User home
    candidates.append(str(Path.home() / "soundfonts" / "GeneralUser.sf2"))

    # Check explicit paths first
    for path in candidates:
        if os.path.isfile(path):
            return path

    # 7. Glob any .sf2 in cwd or parent
    for search_dir in (Path.cwd(), Path.cwd().parent):
        matches = sorted(search_dir.glob("*.sf2"))
        if matches:
            return str(matches[0])

    # Nothing found — guide the user
    print(
        "No soundfont found. Download GeneralUser GS or FluidR3_GM.sf2 and place in "
        r"c:\soundfonts\ or set SOUNDFONT env var."
    )
    return None


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """
    Return True only if pretty_midi is importable AND a soundfont can be found.

    Prints a user-friendly explanation of whatever is missing so the caller
    can decide to fall back to the numpy renderer.
    """
    missing: list[str] = []

    # Check pretty_midi
    try:
        import pretty_midi  # noqa: F401
    except ImportError:
        missing.append(
            "pretty_midi is not installed. Run: pip install pretty_midi"
        )

    # Check FluidSynth binding (pretty_midi shells out to fluidsynth)
    try:
        import pretty_midi  # noqa: F401, F811
        # Trigger the internal fluidsynth check that pretty_midi performs
        # by probing the synthesiser attribute without actually rendering.
        import ctypes.util
        lib = ctypes.util.find_library("fluidsynth")
        if lib is None:
            missing.append(
                "FluidSynth shared library not found. "
                "Install it with: apt install fluidsynth  OR  brew install fluid-synth  "
                "OR  choco install fluidsynth  (Windows)"
            )
    except Exception:
        pass  # pretty_midi already flagged above

    # Check soundfont (suppress the "not found" print if other things are missing)
    if not missing:
        sf = find_soundfont()
        if sf is None:
            missing.append(
                r"No soundfont (.sf2) found. "
                r"Place one in c:\soundfonts\ or set the SOUNDFONT environment variable."
            )

    if missing:
        print("[FluidSynth renderer] Not available:")
        for msg in missing:
            print(f"  • {msg}")
        print("  → Falling back to numpy synthesiser.")
        return False

    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pan_to_midi(pan: float) -> int:
    """
    Convert a float pan value in [-1, +1] to a MIDI CC10 value [0, 127].
    Centre (0.0) → 64.
    """
    return int(np.clip((pan + 1.0) / 2.0 * 127, 0, 127))


def _get_program(instrument_name: str) -> int:
    """Look up General MIDI program number by instrument name (case-insensitive)."""
    return INSTRUMENT_PROGRAMS.get(instrument_name.lower(), 0)


def _family_key(note) -> str:
    """
    Return a grouping key for a NoteEvent so that notes with the same
    instrument land on the same MIDI channel (up to 4 families).
    """
    return getattr(note, "instrument", "none")


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_with_fluidsynth(
    note_events: list,
    total_duration: float,
    config: dict,
    sample_rate: int = 44100,
    soundfont_path: str | None = None,
) -> np.ndarray:
    """
    Render a list of NoteEvent-like objects via FluidSynth / pretty_midi.

    Parameters
    ----------
    note_events     : list of duck-typed NoteEvent objects with attributes:
                        .time       (float, seconds)
                        .duration   (float, seconds)
                        .pitch      (int, MIDI pitch 0-127)
                        .velocity   (int, 0-127)
                        .instrument (str, key into INSTRUMENT_PROGRAMS)
                        .pan        (float, -1 to +1)
    total_duration  : total clip length in seconds (used for MIDI length hint)
    config          : record config dict (may contain "instrument", "tempo_bpm")
    sample_rate     : audio sample rate in Hz
    soundfont_path  : explicit .sf2 path; auto-discovered if None

    Returns
    -------
    np.ndarray of shape (2, N), dtype float32, range approximately -1 .. +1
    """
    try:
        import pretty_midi
    except ImportError as exc:
        raise RuntimeError(
            "pretty_midi is required for FluidSynth rendering. "
            "Install it with: pip install pretty_midi"
        ) from exc

    # Resolve soundfont
    if soundfont_path is None:
        soundfont_path = find_soundfont()
    if soundfont_path is None:
        raise RuntimeError(
            "No soundfont found. See find_soundfont() for search paths."
        )

    # ------------------------------------------------------------------
    # 1. Build PrettyMIDI at 120 BPM (FluidSynth renders in real-time
    #    regardless of tempo; 120 BPM is a neutral default).
    # ------------------------------------------------------------------
    tempo_bpm = float(config.get("tempo_bpm", 120))
    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo_bpm)

    # ------------------------------------------------------------------
    # 2. Group NoteEvents by instrument family (up to 4 channels).
    #    General MIDI channel 10 (index 9) is drums — we avoid it.
    # ------------------------------------------------------------------
    family_to_instrument: dict[str, pretty_midi.Instrument] = {}
    pan_per_family: dict[str, list[int]] = {}

    # Determine channel assignment order (at most 4, skip ch 9 = drums)
    _safe_channels = [ch for ch in range(16) if ch != 9]

    for note in note_events:
        family = _family_key(note)

        if family not in family_to_instrument:
            if len(family_to_instrument) >= 4:
                # Cap at 4 channels — overflow goes to last channel
                family = list(family_to_instrument.keys())[-1]
            else:
                program = _get_program(family)
                channel = _safe_channels[len(family_to_instrument)]
                instrument = pretty_midi.Instrument(
                    program=program,
                    is_drum=False,
                    name=family,
                )
                family_to_instrument[family] = instrument
                pan_per_family[family] = []

        # ------------------------------------------------------------------
        # 3. Add a Note for each NoteEvent.
        # ------------------------------------------------------------------
        start = float(getattr(note, "time", 0.0))
        duration = float(getattr(note, "duration", 0.25))
        end = start + max(duration, 0.01)  # guard against zero-length notes
        pitch = int(np.clip(getattr(note, "pitch", 60), 0, 127))
        velocity = int(np.clip(getattr(note, "velocity", 80), 1, 127))

        midi_note = pretty_midi.Note(
            velocity=velocity,
            pitch=pitch,
            start=start,
            end=end,
        )
        family_to_instrument[family].notes.append(midi_note)

        # ------------------------------------------------------------------
        # 4. Collect pan values for this family (we'll apply the median pan
        #    to the whole instrument via CC10, since pretty_midi instruments
        #    have a single pan setting per channel).
        # ------------------------------------------------------------------
        pan_raw = float(getattr(note, "pan", 0.0))
        pan_per_family[family].append(_pan_to_midi(pan_raw))

    # Apply median pan per instrument via MIDI CC10 (pan controller)
    for family, instrument in family_to_instrument.items():
        pans = pan_per_family.get(family, [64])
        median_pan = int(np.median(pans)) if pans else 64
        # pretty_midi exposes pan via a control_changes list
        instrument.control_changes.append(
            pretty_midi.ControlChange(number=10, value=median_pan, time=0.0)
        )
        midi.instruments.append(instrument)

    # ------------------------------------------------------------------
    # 5. Render to audio using FluidSynth.
    # ------------------------------------------------------------------
    audio = midi.fluidsynth(fs=float(sample_rate), sf2_path=soundfont_path)
    # pretty_midi.fluidsynth() returns a float32 1-D array (mono) or
    # (N, 2) stereo depending on the fluidsynth build; normalise shape below.

    # ------------------------------------------------------------------
    # 6. Ensure stereo shape (2, N).
    # ------------------------------------------------------------------
    audio = np.asarray(audio, dtype=np.float32)

    if audio.ndim == 1:
        # Mono — duplicate to stereo
        audio = np.stack([audio, audio], axis=0)  # shape (2, N)
    elif audio.ndim == 2:
        if audio.shape[0] == 2:
            pass  # already (2, N)
        elif audio.shape[1] == 2:
            audio = audio.T  # (N, 2) → (2, N)
        else:
            # Unexpected shape — fall back to mono duplication
            audio = np.stack([audio.ravel(), audio.ravel()], axis=0)

    # ------------------------------------------------------------------
    # 7. Normalise to prevent clipping.
    # ------------------------------------------------------------------
    peak = np.max(np.abs(audio))
    if peak > 0.95:
        audio = audio / peak * 0.95

    return audio.astype(np.float32)


# ---------------------------------------------------------------------------
# MIDI file export (bonus — useful for debugging in a DAW)
# ---------------------------------------------------------------------------

def save_midi(
    note_events: list,
    total_duration: float,
    path: str,
    config: dict,
) -> None:
    """
    Save a .mid file built from note_events for inspection / debugging.

    Useful for debugging — load in a DAW to see note layout.

    Parameters
    ----------
    note_events    : same duck-typed list as render_with_fluidsynth()
    total_duration : total clip length in seconds
    path           : output .mid file path (created / overwritten)
    config         : record config dict
    """
    try:
        import pretty_midi
    except ImportError as exc:
        raise RuntimeError(
            "pretty_midi is required to save MIDI files. "
            "Install it with: pip install pretty_midi"
        ) from exc

    tempo_bpm = float(config.get("tempo_bpm", 120))
    midi = pretty_midi.PrettyMIDI(initial_tempo=tempo_bpm)

    # Group by family, same logic as render_with_fluidsynth
    family_to_instrument: dict[str, pretty_midi.Instrument] = {}
    _safe_channels = [ch for ch in range(16) if ch != 9]

    for note in note_events:
        family = _family_key(note)

        if family not in family_to_instrument:
            if len(family_to_instrument) >= 4:
                family = list(family_to_instrument.keys())[-1]
            else:
                program = _get_program(family)
                instrument = pretty_midi.Instrument(
                    program=program,
                    is_drum=False,
                    name=family,
                )
                # Apply median pan — set at time 0
                instrument.control_changes.append(
                    pretty_midi.ControlChange(number=10, value=64, time=0.0)
                )
                family_to_instrument[family] = instrument

        start = float(getattr(note, "time", 0.0))
        duration = float(getattr(note, "duration", 0.25))
        end = start + max(duration, 0.01)
        pitch = int(np.clip(getattr(note, "pitch", 60), 0, 127))
        velocity = int(np.clip(getattr(note, "velocity", 80), 1, 127))

        midi_note = pretty_midi.Note(
            velocity=velocity,
            pitch=pitch,
            start=start,
            end=end,
        )
        family_to_instrument[family].notes.append(midi_note)

    for instrument in family_to_instrument.values():
        midi.instruments.append(instrument)

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    midi.write(path)
    print(f"[save_midi] Written: {path}  ({len(note_events)} notes, "
          f"{len(family_to_instrument)} MIDI channels)")


# ---------------------------------------------------------------------------
# Quick self-check (python -m src.fluidsynth_renderer)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== FluidSynth renderer self-check ===")
    print(f"Soundfont: {find_soundfont()}")
    print(f"Available: {is_available()}")

    if is_available():
        # Synthetic test: a simple pentatonic arpeggio
        class _FakeNote:
            def __init__(self, time, pitch, instrument="marimba", pan=0.0):
                self.time = time
                self.pitch = pitch
                self.duration = 0.4
                self.velocity = 90
                self.instrument = instrument
                self.pan = pan

        notes = [
            _FakeNote(i * 0.5, 60 + p, pan=(i / 8) * 2 - 1)
            for i, p in enumerate([0, 2, 4, 7, 9, 12, 9, 7])
        ]
        audio = render_with_fluidsynth(notes, total_duration=4.5, config={})
        print(f"Rendered shape: {audio.shape}, dtype: {audio.dtype}, "
              f"peak: {np.max(np.abs(audio)):.3f}")

        midi_path = "out/self_check.mid"
        os.makedirs("out", exist_ok=True)
        save_midi(notes, 4.5, midi_path, config={})
        print("Self-check passed.")
    else:
        print("Self-check skipped (FluidSynth not available).")
