"""
mapping.py — Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

Converts a validated frame dict (produced by loader.py) into a list of
NoteEvent objects ready for the synthesizer / renderer.

Design rationale
----------------
Each satellite "frame" is a snapshot of a spatial strip of the Earth
(columns).  We treat the strip array left-to-right as a time-to-stereo
axis: leftmost strip → hard-left pan, rightmost strip → hard-right pan.
Column brightness drives velocity (louder = brighter region), column
height drives pitch (taller feature = higher note), and hue selects the
instrument family (warm tones → strings/bells, cool tones → pads).
Motion controls note *density*: busier frames get more simultaneous
notes; still frames stay sparse and meditative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

import numpy as np

# ---------------------------------------------------------------------------
# Optional import of the scales module (written by the scales worker).
# If it isn't available yet we provide local fallbacks so this file is
# immediately runnable for testing.
# ---------------------------------------------------------------------------
try:
    from .scales import quantize, hue_to_instrument  # type: ignore[import]
    _SCALES_AVAILABLE = True
except ImportError:
    _SCALES_AVAILABLE = False

    # --- Fallback: minimal C-major-pentatonic quantizer -------------------
    _FALLBACK_SCALE_NOTES = [
        48, 50, 52, 55, 57,   # octave 3: C D E G A
        60, 62, 64, 67, 69,   # octave 4
        72, 74, 76, 79, 81,   # octave 5
        84, 86, 88, 91, 93,   # octave 6
    ]

    def quantize(value: float, scale_name: str, octave_range: tuple = (3, 6)) -> int:  # type: ignore[override]
        """Fallback quantizer: maps 0–1 linearly to the pentatonic ladder."""
        notes = _FALLBACK_SCALE_NOTES
        idx = int(round(value * (len(notes) - 1)))
        idx = max(0, min(idx, len(notes) - 1))
        return notes[idx]

    def hue_to_instrument(hue: float) -> str:  # type: ignore[override]
        """Fallback hue mapper using the same warm/cool logic."""
        if hue <= 60 or hue >= 300:
            return "strings_bells"
        if 180 <= hue <= 270:
            return "soft_pad"
        return "blend"


# ---------------------------------------------------------------------------
# NoteEvent dataclass
# ---------------------------------------------------------------------------

@dataclass
class NoteEvent:
    """
    A single musical note emitted from one frame's strip.

    Attributes
    ----------
    time       : seconds from track start (set by frame["t"])
    duration   : note hold time in seconds
    pitch      : MIDI note number (0–127)
    velocity   : MIDI velocity (0–127); encodes loudness
    pan        : stereo position, -1.0 = hard-left, +1.0 = hard-right
    instrument : instrument family string (e.g. "strings_bells")
    channel    : MIDI channel (0–15); lets the renderer multiplex voices
    """
    time: float
    duration: float
    pitch: int
    velocity: int
    pan: float
    instrument: str
    channel: int


# ---------------------------------------------------------------------------
# Main conversion function
# ---------------------------------------------------------------------------

def frame_to_notes(
    frame: dict,
    frame_duration: float,
    config: dict,
    scale_name: str,
) -> List[NoteEvent]:
    """
    Convert one validated frame dict into a list of NoteEvents.

    Parameters
    ----------
    frame          : validated frame dict (loader.py schema).
    frame_duration : wall-clock length of this frame in seconds (e.g. 1 / fps).
    config         : record config dict (e.g. loaded from configs/night_lights.json).
    scale_name     : scale key understood by scales.quantize().

    Returns
    -------
    List of NoteEvent objects, possibly empty (e.g. lightning silence threshold).
    """

    # ------------------------------------------------------------------
    # Step 1 — Extract all fields from the frame.
    #
    # loader.py guarantees these keys with defaults, so we use .get()
    # only as a safety net for running this module standalone.
    # ------------------------------------------------------------------
    columns: list        = frame.get("columns", [])          # per-strip brightness values (0–1)
    column_heights: list = frame.get("column_heights", [])   # per-strip "height" feature (0–1)
    column_hues: list    = frame.get("column_hues", [])      # per-strip hue (0–360°)
    brightness: float    = float(frame.get("brightness", 0.5))  # overall frame brightness
    hue: float           = float(frame.get("hue", 180.0))       # dominant frame hue
    saturation: float    = float(frame.get("saturation", 0.5))  # overall colour saturation
    edges: float         = float(frame.get("edges", 0.0))       # edge-detection intensity
    motion: float        = float(frame.get("motion", 0.0))      # optical-flow / change metric
    t: float             = float(frame.get("t", 0.0))           # frame timestamp in seconds

    # Guard against empty or mismatched arrays (defensive)
    n_strips = len(columns)
    if n_strips == 0:
        return []

    # Pad shorter arrays to match columns length so indexing is always safe
    column_heights = _pad_to(column_heights, n_strips, default=0.5)
    column_hues    = _pad_to(column_hues,    n_strips, default=hue)

    # ------------------------------------------------------------------
    # Step 2 — Select top-N strips by per-strip brightness.
    #
    # N comes from config["max_simultaneous_notes"] (default 4).
    # We also enforce a silence threshold: strips dimmer than 0.05 are
    # treated as "off" and excluded entirely to avoid mud.
    # ------------------------------------------------------------------
    max_notes: int = int(config.get("max_simultaneous_notes", 4))
    silence_threshold: float = 0.05  # strips below this brightness are silent

    # Build (strip_brightness, original_index) pairs and sort descending
    indexed = sorted(
        ((columns[i], i) for i in range(n_strips) if columns[i] > silence_threshold),
        key=lambda x: x[0],
        reverse=True,
    )
    # Keep at most max_notes candidates before motion-density re-filter
    top_candidates = indexed[:max_notes]

    # ------------------------------------------------------------------
    # Step 3 — Record-level gate: lightning only fires on bright/active frames.
    #
    # Doing this before the per-strip loop avoids allocating NoteEvents
    # that will immediately be discarded.
    # ------------------------------------------------------------------
    record: str = config.get("record", "")

    if record == "lightning":
        # Lightning: silence everything unless a brightness OR motion spike
        # occurs.  This creates the characteristic "sudden burst" texture.
        if brightness <= 0.3 and motion <= 0.3:
            return []

    # ------------------------------------------------------------------
    # Step 5 (applied before the loop) — Motion-based note density.
    #
    # More motion → more simultaneous notes → busier, more energetic
    # texture.  Still frames stay sparse and calm.
    #   motion_factor ∈ [0.3, 1.0]
    #   actual_n     ∈ [1, max_notes]
    # ------------------------------------------------------------------
    motion_factor: float = 0.3 + motion * 0.7
    actual_n: int = max(1, int(max_notes * motion_factor))
    selected = top_candidates[:actual_n]  # final strip selection

    # ------------------------------------------------------------------
    # Step 3 continued — Build NoteEvents for each selected strip.
    # ------------------------------------------------------------------
    notes: List[NoteEvent] = []

    for strip_brightness, i in selected:

        # Pan: map strip position linearly from -1 (left) to +1 (right).
        # Stereo position encodes spatial location; left-to-right mirrors
        # a "time axis" across the frame width.
        if n_strips > 1:
            pan: float = (i / (n_strips - 1)) * 2.0 - 1.0
        else:
            pan = 0.0  # single-strip frame → centre

        # Pitch: quantize the strip's height feature to the chosen scale.
        # Higher column_height → higher register → taller/denser features
        # sound higher-pitched.
        height: float = float(column_heights[i])
        pitch: int = quantize(height, scale_name)

        # Velocity: combine overall frame brightness with the strip's own
        # brightness.  This keeps loud frames louder without clipping quiet
        # strips entirely.  Clamp to valid MIDI range [1, 127].
        raw_velocity: float = strip_brightness * brightness * 127.0
        velocity: int = int(max(1.0, min(127.0, raw_velocity)))

        # Duration: higher features ring slightly longer (a taller canopy,
        # a brighter city-light cluster) — produces a natural sustain
        # relationship between height and note length.
        duration: float = frame_duration * (0.3 + height * 0.5)

        # Instrument family: hue selects the timbre.
        # Warm hues (orange/red/magenta) → strings/bells
        # Cool hues (cyan/blue/indigo)   → soft pads
        # Neutral/green                  → blend (marimba default)
        instrument: str = hue_to_instrument(float(column_hues[i]))

        # Channel assignment: spread voices across MIDI channels 0–15 by
        # instrument family so the renderer can apply per-channel effects.
        channel: int = _instrument_to_channel(instrument)

        # Assemble the note (time is the frame timestamp; the render loop
        # will add any global track offset on top of this)
        note = NoteEvent(
            time=t,
            duration=duration,
            pitch=pitch,
            velocity=velocity,
            pan=pan,
            instrument=instrument,
            channel=channel,
        )

        # ------------------------------------------------------------------
        # Step 4 — Record-specific adjustments.
        # Each record has a distinct acoustic personality that reflects the
        # character of the underlying NASA dataset.
        # ------------------------------------------------------------------

        if record == "night_lights":
            # Night Lights: city clusters → add a sustaining chord-pad note
            # one octave below when the frame is richly saturated (colorful
            # city glow).  This fills the low register and gives depth.
            if saturation > 0.4:
                pad_pitch: int = max(0, pitch - 12)  # one octave down
                pad_note = NoteEvent(
                    time=t,
                    duration=duration * 1.5,  # pad rings longer than melody
                    pitch=pad_pitch,
                    velocity=max(1, int(velocity * 0.6)),  # softer
                    pan=pan,
                    instrument="soft_pad",
                    channel=_instrument_to_channel("soft_pad"),
                )
                notes.append(pad_note)

        elif record == "ocean_currents":
            # Ocean Currents: slow, overlapping swells.
            # High-motion areas (turbulent current, eddies) are skipped to
            # keep the texture smooth — turbulence → silence / space.
            if motion > 0.7:
                continue  # skip this strip in high-motion frames
            # Extend duration × 3: ocean features evolve slowly
            note = NoteEvent(
                time=t,
                duration=duration * 3.0,
                pitch=pitch,
                velocity=max(1, int(velocity * 0.7)),  # 30 % quieter
                pan=pan,
                instrument=instrument,
                channel=channel,
            )

        elif record == "gedi_forest":
            # GEDI Forest: LiDAR canopy-height data → melodic line.
            # column_heights (canopy height 0–1) maps directly to pitch,
            # preserving the melodic contour of the forest profile.
            # Pitch already computed above from column_heights — that is
            # the correct mapping, so no change needed for melody note.
            # Additionally, add a soft sustain pad when saturation is high
            # (dense, lush canopy → richer harmonic bed).
            if saturation > 0.5:
                pad_note = NoteEvent(
                    time=t,
                    duration=duration * 2.0,
                    pitch=max(0, pitch - 7),    # a fifth below for warmth
                    velocity=max(1, int(velocity * 0.5)),
                    pan=pan,
                    instrument="soft_pad",
                    channel=_instrument_to_channel("soft_pad"),
                )
                notes.append(pad_note)

        # (lightning gate is already handled above — no per-strip adjustment needed)

        notes.append(note)

    return notes


# ---------------------------------------------------------------------------
# pan_to_stereo — equal-power panning helper
# ---------------------------------------------------------------------------

def pan_to_stereo(pan: float, signal: np.ndarray, sample_rate: int) -> np.ndarray:
    """
    Apply equal-power stereo panning to a mono audio signal.

    Equal-power panning keeps perceived loudness constant as the source
    moves across the stereo field (unlike linear panning, which dips in
    the centre).

    Parameters
    ----------
    pan         : stereo position in [-1, +1] (-1 = hard-left, +1 = hard-right)
    signal      : 1-D numpy array of mono audio samples (float32 or float64)
    sample_rate : sample rate in Hz (unused here, kept for API symmetry)

    Returns
    -------
    2-D numpy array of shape (len(signal), 2) — columns are [left, right].
    """
    # Normalise pan to [0, 1] then to the first quadrant [0, π/2]
    angle: float = ((pan + 1.0) / 2.0) * (math.pi / 2.0)

    # Equal-power gains: cos² + sin² = 1  → no loudness dip at centre
    left_gain: float  = math.cos(angle)
    right_gain: float = math.sin(angle)

    left_channel  = signal * left_gain
    right_channel = signal * right_gain

    # Stack into (N, 2) stereo array
    stereo = np.stack([left_channel, right_channel], axis=-1)
    return stereo.astype(signal.dtype)


# ---------------------------------------------------------------------------
# smooth_volume — moving-average velocity smoother
# ---------------------------------------------------------------------------

def smooth_volume(velocities: List[float], window: int = 3) -> List[float]:
    """
    Apply a simple centred moving-average to a sequence of velocity values.

    Without smoothing, sudden brightness changes (e.g. a cloud passing
    over a city) produce jarring volume jumps.  Averaging over a small
    window of frames blends transitions naturally.

    Parameters
    ----------
    velocities : list of raw velocity floats (typically 0–127).
    window     : number of frames to average (default 3; odd recommended).

    Returns
    -------
    List of smoothed floats, same length as input.
    """
    if len(velocities) == 0:
        return []

    half = window // 2
    smoothed: List[float] = []

    for idx in range(len(velocities)):
        # Clamp window boundaries to valid indices (edge frames use
        # whatever neighbours are available — no zero-padding)
        lo = max(0, idx - half)
        hi = min(len(velocities), idx + half + 1)
        avg = sum(velocities[lo:hi]) / (hi - lo)
        smoothed.append(avg)

    return smoothed


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _pad_to(lst: list, length: int, default) -> list:
    """Return lst extended with *default* values up to *length*."""
    if len(lst) >= length:
        return lst[:length]
    return list(lst) + [default] * (length - len(lst))


def _instrument_to_channel(instrument: str) -> int:
    """
    Map instrument family to a stable MIDI channel.

    Channel 9 is reserved for drums (GM standard) so we skip it.
    Having predictable per-family channels lets the renderer apply
    reverb / filter settings per-channel without extra bookkeeping.
    """
    _MAP = {
        "strings_bells": 0,
        "soft_pad":       1,
        "blend":          2,
    }
    return _MAP.get(instrument, 2)  # default to channel 2 (blend)
