"""
export.py — Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

Handles post-render signal processing and audio file output.

Pipeline position (called from render.py after the renderer):
  renderer → export.normalize() → export.save_wav() → export.save_mp3()

Design notes
------------
* All signals are (2, N) float32 arrays: row 0 = left, row 1 = right.
  This matches the convention used by numpy_renderer and fluidsynth_renderer
  so callers never need to reshape before exporting.
* WAV is always written first; MP3 falls back gracefully if pydub/ffmpeg
  are absent, keeping the WAV as a usable fallback.
* normalize() uses peak normalisation (not RMS/LUFS) for simplicity —
  good enough for a hackathon demo where files are not broadcast-distributed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

import numpy as np
import scipy.io.wavfile  # type: ignore[import]

if TYPE_CHECKING:
    # pydub is optional; this import is only for type checking so the module
    # can still be imported when pydub is not installed.
    from pydub import AudioSegment  # type: ignore[import]


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def normalize(signal: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    """
    Scale *signal* so that its absolute peak equals *target_peak*.

    If the signal is essentially silent (peak < 0.001) we skip normalisation
    rather than amplifying noise to full scale.  This prevents a silent
    dataset from producing a wall of amplified quantisation noise.

    Comment: consistent loudness across records — every rendered track will
    hit the same perceived level regardless of how sparse or dense the
    source data was.

    Parameters
    ----------
    signal      : (2, N) float32 array — stereo audio.
    target_peak : desired absolute peak value in [0, 1].  Default 0.9 leaves
                  a little headroom before digital clipping at 1.0.

    Returns
    -------
    Normalised (2, N) float32 array.
    """
    # Find the peak absolute sample across both channels.
    peak = float(np.max(np.abs(signal)))

    # Skip normalisation for silent signals to avoid amplifying noise.
    if peak < 0.001:
        return signal

    # Scale every sample by the ratio needed to bring the peak to target_peak.
    scale_factor = target_peak / peak
    return (signal * scale_factor).astype(np.float32)


# ---------------------------------------------------------------------------
# save_wav
# ---------------------------------------------------------------------------

def save_wav(signal: np.ndarray, path: str, sample_rate: int = 44100) -> None:
    """
    Write *signal* to a 16-bit stereo WAV file at *path*.

    The (2, N) float32 input is transposed to (N, 2) int16 for scipy,
    which expects samples-first channel layout.

    Parameters
    ----------
    signal      : (2, N) float32 stereo array.
    path        : destination file path (created with parents as needed).
    sample_rate : samples per second; default 44100 Hz (CD quality).
    """
    # Ensure the output directory exists before writing.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # Transpose from (2, N) → (N, 2) so scipy sees [left, right] per sample row.
    stereo_NxC = signal.T  # shape: (N, 2), dtype: float32

    # Convert float32 [-1, 1] to int16 [-32768, 32767].
    # Clamp first so values just outside [-1, 1] don't wrap around.
    clamped = np.clip(stereo_NxC, -1.0, 1.0)
    int16_data = (clamped * 32767).astype(np.int16)

    # Write WAV using scipy — straightforward and dependency-light.
    scipy.io.wavfile.write(path, sample_rate, int16_data)


# ---------------------------------------------------------------------------
# array_to_audiosegment  (helper)
# ---------------------------------------------------------------------------

def array_to_audiosegment(signal: np.ndarray, sample_rate: int) -> "AudioSegment":
    """
    Convert a (2, N) float32 stereo numpy array to a pydub AudioSegment.

    pydub expects raw PCM bytes in interleaved stereo (L, R, L, R, …) with
    16-bit signed integer samples.  We produce exactly that here.

    Parameters
    ----------
    signal      : (2, N) float32 stereo array.
    sample_rate : samples per second.

    Returns
    -------
    pydub AudioSegment — stereo, 16-bit, at *sample_rate*.
    """
    from pydub import AudioSegment  # type: ignore[import]

    # Transpose to (N, 2) and clamp to the float32 [-1, 1] safe range.
    stereo_NxC = np.clip(signal.T, -1.0, 1.0)

    # Convert to int16 PCM — the native pydub raw-sample format.
    int16_data = (stereo_NxC * 32767).astype(np.int16)

    # Flatten to a 1-D interleaved byte sequence: L0, R0, L1, R1, ...
    raw_bytes = int16_data.flatten().tobytes()

    # Build an AudioSegment from the raw PCM bytes.
    # sample_width=2 → 16-bit (2 bytes per sample per channel).
    return AudioSegment(
        data=raw_bytes,
        sample_width=2,       # bytes per sample per channel
        frame_rate=sample_rate,
        channels=2,           # stereo
    )


# ---------------------------------------------------------------------------
# save_mp3
# ---------------------------------------------------------------------------

def save_mp3(
    signal: np.ndarray,
    path: str,
    sample_rate: int = 44100,
    bitrate: str = "192k",
) -> None:
    """
    Export *signal* as an MP3 file at *path*.

    Strategy (graceful fallback chain):
      1. Try pydub + ffmpeg — the preferred path; produces high-quality MP3
         without spawning a subprocess from Python directly.
      2. If pydub is unavailable or ffmpeg is missing from pydub's view, fall
         back to calling ffmpeg via subprocess directly on a temporary WAV.
      3. If both fail, print a warning and return without raising — the caller
         (render.py) has already written the WAV, so the user gets audio even
         without an MP3 encoder.

    Parameters
    ----------
    signal      : (2, N) float32 stereo array.
    path        : destination .mp3 file path.
    sample_rate : samples per second; default 44100 Hz.
    bitrate     : MP3 bitrate string accepted by ffmpeg/pydub, e.g. "192k".
    """
    # Ensure the output directory exists before writing.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # --- Attempt 1: pydub ------------------------------------------------
    try:
        segment = array_to_audiosegment(signal, sample_rate)
        # Export directly to the target path as MP3.
        # pydub calls ffmpeg internally; it raises CouldntEncodeError if
        # ffmpeg is not on PATH, which we catch below.
        segment.export(path, format="mp3", bitrate=bitrate)
        return  # success — nothing more to do
    except ImportError:
        # pydub not installed — fall through to subprocess approach.
        pass
    except Exception:
        # Covers pydub's CouldntEncodeError (ffmpeg missing) and similar.
        pass

    # --- Attempt 2: subprocess ffmpeg on a temporary WAV -----------------
    # Write a temporary WAV file and pass it to ffmpeg directly.  This works
    # even if pydub itself is absent, as long as ffmpeg is on the system PATH.
    tmp_wav_path: str | None = None
    try:
        # Create a named temporary file; we close it before passing to ffmpeg
        # so Windows doesn't lock it.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav_path = tmp.name

        # Write signal to the temporary WAV.
        save_wav(signal, tmp_wav_path, sample_rate)

        # Build the ffmpeg command.  -y overwrites without prompting.
        cmd = [
            "ffmpeg", "-y",
            "-i", tmp_wav_path,
            "-b:a", bitrate,
            path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return  # success
        # Non-zero return code means ffmpeg itself failed — fall through.
    except FileNotFoundError:
        # ffmpeg binary not found on PATH at all.
        pass
    except Exception:
        pass
    finally:
        # Always clean up the temp WAV, regardless of success or failure.
        if tmp_wav_path and os.path.exists(tmp_wav_path):
            try:
                os.remove(tmp_wav_path)
            except OSError:
                pass

    # --- Attempt 3: graceful degradation ---------------------------------
    # Neither approach worked.  The WAV has already been saved by the caller
    # (render.py step 9), so the user is not left with nothing.
    print(
        "MP3 export failed — WAV saved. "
        "Install pydub and ffmpeg to get MP3."
    )
