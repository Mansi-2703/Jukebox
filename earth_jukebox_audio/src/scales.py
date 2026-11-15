"""
scales.py — Earth Jukebox Audio Sound Engine
Musical scales and pitch/instrument quantization utilities.

This module maps continuous sensor values (0–1 floats) to discrete musical
pitches (MIDI note numbers) and to instrument families, so satellite imagery
can be turned into coherent music.
"""

import math
from typing import Tuple


# ---------------------------------------------------------------------------
# SCALES — MIDI note number collections
# ---------------------------------------------------------------------------
# Each scale is a sorted list of MIDI note numbers spanning octaves 3–6
# (roughly MIDI 48–93).  The render engine indexes into this list to pick
# pitches that always sound "in key" regardless of which raw value comes in.
#
# MIDI note numbers: middle C (C4) = 60.
# Formula for note N in octave O: (O + 1) * 12 + semitone
#   C=0, D=2, E=4, F=5, G=7, A=9, B=11
#   Eb=3, F#=6, Bb=10

def _build_scale(intervals: list, octaves: range) -> list:
    """
    Build a sorted MIDI note list from semitone *intervals* (relative to C)
    across the given *octaves*.
    """
    notes = []
    for octave in octaves:
        root = (octave + 1) * 12  # C in this octave
        for interval in intervals:
            notes.append(root + interval)
    return sorted(notes)


SCALES: dict = {
    # C major pentatonic: C D E G A — bright, open, works over any dataset
    "C_major_pentatonic": _build_scale([0, 2, 4, 7, 9], range(3, 7)),

    # C minor pentatonic: C Eb F G Bb — deeper, slightly melancholy
    "C_minor_pentatonic": _build_scale([0, 3, 5, 7, 10], range(3, 7)),

    # C major: C D E F G A B — full diatonic, more harmonic complexity
    "C_major": _build_scale([0, 2, 4, 5, 7, 9, 11], range(3, 7)),

    # C blues: C Eb F F# G Bb — expressive, raw, suits lightning / storm data
    "C_blues": _build_scale([0, 3, 5, 6, 7, 10], range(3, 7)),

    # A minor pentatonic: A C D E G — relative minor of C major pentatonic;
    # starts on A so the low end feels grounded, suits ocean / forest data
    "A_minor_pentatonic": _build_scale([9, 12, 14, 16, 19], range(2, 6)),
    # Note: intervals above are relative to C, so A=9, C(next)=12, D=14, E=16, G=19
}


# ---------------------------------------------------------------------------
# quantize — map a 0–1 value to a MIDI note number
# ---------------------------------------------------------------------------

def quantize(
    value: float,
    scale_name: str,
    octave_range: Tuple[int, int] = (3, 6),
) -> int:
    """
    Map *value* (0.0–1.0) to a MIDI note number from *scale_name*.

    The mapping is linear across the available notes:
      • value = 0.0 → lowest note in the scale within octave_range
      • value = 1.0 → highest note in the scale within octave_range

    Higher sensor readings (brighter, taller canopy, etc.) produce higher
    pitches, which is intuitive for a listener.

    Parameters
    ----------
    value       : float  — normalised sensor reading in [0, 1]
    scale_name  : str    — key into SCALES dict
    octave_range: (lo, hi) inclusive octave numbers to restrict pitch range

    Returns
    -------
    int  — MIDI note number
    """
    # Clamp value to [0, 1] defensively — loader should have caught extremes
    value = max(0.0, min(1.0, value))

    # Retrieve scale; fall back to C major pentatonic if name is unknown
    notes = SCALES.get(scale_name)
    if notes is None:
        notes = SCALES["C_major_pentatonic"]

    # Filter notes to those within the requested octave range.
    # An octave O covers MIDI notes (O+1)*12 .. (O+1)*12 + 11.
    lo_midi = (octave_range[0] + 1) * 12
    hi_midi = (octave_range[1] + 1) * 12 + 11
    filtered = [n for n in notes if lo_midi <= n <= hi_midi]

    if not filtered:
        # Safety net: if the range filter removed everything, use full scale
        filtered = notes

    # Map value linearly to an index in the filtered list.
    # index 0 → lowest note (value ≈ 0), index -1 → highest note (value ≈ 1)
    idx = int(value * (len(filtered) - 1))
    idx = max(0, min(len(filtered) - 1, idx))  # clamp index

    return filtered[idx]


# ---------------------------------------------------------------------------
# hue_to_instrument — map a hue angle to an instrument family name
# ---------------------------------------------------------------------------

def hue_to_instrument(hue: float) -> str:
    """
    Map a HSV hue angle (0–360) to a broad instrument family string.

    Rationale: colour temperature mirrors sonic warmth.
    • Warm colours (reds, oranges, yellows: 0–60° and 300–360°) →
      "strings_bells" — bright, resonant timbres that feel energetic and warm.
    • Cool colours (cyan, blue, indigo: 180–270°) →
      "soft_pad" — sustained, smooth textures that feel calm and deep.
    • Transitional colours (yellow-green, green, violet) →
      "blend" — a neutral marimba/mallet that sits between both worlds.

    This mapping means night-light data (orange glow) produces bells,
    ocean data (deep blue) produces pads, and forest data (green) blends both.

    Parameters
    ----------
    hue : float — HSV hue in [0, 360]

    Returns
    -------
    str — one of "strings_bells", "soft_pad", "blend"
    """
    # Normalise hue to [0, 360) just in case a floating-point 360 slips in
    hue = hue % 360.0

    if hue <= 60.0 or hue >= 300.0:
        # Reds, oranges, yellows, and their wrap-around equivalents
        return "strings_bells"
    elif 180.0 <= hue <= 270.0:
        # Cyans, blues, indigos
        return "soft_pad"
    else:
        # Greens, yellow-greens, purples, magentas
        return "blend"


# ---------------------------------------------------------------------------
# instrument_to_midi_program — map instrument family to GM program number
# ---------------------------------------------------------------------------

def instrument_to_midi_program(instrument_str: str, record_config: dict) -> int:
    """
    Map an instrument family string to a General MIDI program number (0-indexed).

    General MIDI program numbers are 0-indexed in most libraries (e.g. pretty_midi),
    though hardware/notation tools sometimes display them 1-indexed (1–128).

    Mapping:
      "strings_bells" → 48  (Strings ensemble) or 9  (Glockenspiel/Bells)
      "soft_pad"      → 89  (Pad 2: warm)
      "blend"         → 12  (Marimba) — neutral melodic mallet

    Priority:
    1. Well-known instrument_str values use the table above.
    2. If record_config contains an "instrument" key with a named program,
       that value takes precedence (lets per-record configs override defaults).
    3. Unknown strings fall back to 12 (marimba) — always musical.

    Parameters
    ----------
    instrument_str  : str  — from hue_to_instrument() or frame mapping
    record_config   : dict — loaded config (may contain "instrument" key)

    Returns
    -------
    int — GM program number (0-indexed)
    """
    # Named-string → program number table.
    # These are General MIDI (GM1) program numbers, 0-indexed:
    #   12 = Marimba, 48 = String Ensemble 1, 9 = Glockenspiel, 89 = Pad 2 (warm)
    _TABLE = {
        "strings_bells": 48,   # String Ensemble 1 — rich, warm, resonant
        "bells":          9,   # Glockenspiel — bright, metallic
        "soft_pad":       89,  # Pad 2 (warm) — slow attack, sustained tone
        "blend":          12,  # Marimba — neutral, works in any context
        "marimba":        12,
        "pad_warm":       89,
        "pad_choir":      91,  # Pad 4 (choir)
        "xylophone":      13,
        "strings":        48,
    }

    # 1. Check if record_config overrides the instrument choice.
    #    This lets configs/lightning.json force "xylophone" regardless of hue.
    config_instrument = record_config.get("instrument", "")
    if config_instrument and config_instrument in _TABLE:
        return _TABLE[config_instrument]

    # 2. Use the hue-derived instrument family from the table.
    if instrument_str in _TABLE:
        return _TABLE[instrument_str]

    # 3. Unknown string — fall back to marimba (always musical, never jarring).
    return 12
