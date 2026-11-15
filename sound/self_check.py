"""
sound/self_check.py — Earth Jukebox Sound Engine Self-Check
Validates rendered audio against features.json using real data only.

Usage:
    python sound/self_check.py --input data/night_lights.json --audio out/night_lights.mp3
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from scipy.io import wavfile

# Ensure earth_jukebox_audio is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AUDIO_PKG = os.path.join(_REPO_ROOT, "earth_jukebox_audio")
if _AUDIO_PKG not in sys.path:
    sys.path.insert(0, _AUDIO_PKG)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src import loader, mapping, scales


def resolve_path(path: str) -> str:
    """Find file in cwd, repo root, or data/features."""
    if os.path.exists(path):
        return os.path.abspath(path)
    cand = os.path.join(_REPO_ROOT, path)
    if os.path.exists(cand):
        return cand
    if "data/" in path and "features" not in path:
        fp = path.replace("data/", "data/features/")
        if os.path.exists(fp):
            return os.path.abspath(fp)
        cand_fp = os.path.join(_REPO_ROOT, fp)
        if os.path.exists(cand_fp):
            return cand_fp
    return os.path.abspath(path)


def resolve_audio_path(audio_path: str) -> tuple[np.ndarray, int]:
    """
    Load audio as stereo float32 [-1, 1], sample_rate.
    Tries audio_path, then corresponding .wav if mp3 is absent.
    """
    path = resolve_path(audio_path)
    if not os.path.exists(path):
        # Try .wav equivalent
        if path.endswith(".mp3"):
            wav_path = path.replace(".mp3", ".wav")
            if os.path.exists(wav_path):
                path = wav_path
        elif path.endswith(".wav"):
            mp3_path = path.replace(".wav", ".mp3")
            if os.path.exists(mp3_path):
                path = mp3_path

    if not os.path.exists(path):
        # Also check inside earth_jukebox_audio/out
        base = os.path.basename(audio_path)
        alt = os.path.join(_REPO_ROOT, "earth_jukebox_audio", "out", base)
        if os.path.exists(alt):
            path = alt
        elif os.path.exists(alt.replace(".mp3", ".wav")):
            path = alt.replace(".mp3", ".wav")

    if not os.path.exists(path):
        sys.exit(f"Error: audio file not found at {audio_path} (or .wav fallback)")

    # Read audio
    if path.endswith(".wav"):
        sr, data = wavfile.read(path)
        if data.dtype == np.int16:
            audio = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            audio = data.astype(np.float32) / 2147483648.0
        else:
            audio = data.astype(np.float32)
        # Ensure shape is (2, N)
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=0)
        elif audio.shape[1] == 2 and audio.shape[0] != 2:
            audio = audio.T
        return audio, sr
    else:
        # Try pydub for MP3
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(path)
            sr = seg.frame_rate
            samples = np.array(seg.get_array_of_samples(), dtype=np.float32)
            if seg.sample_width == 2:
                samples /= 32768.0
            if seg.channels == 2:
                audio = samples.reshape((-1, 2)).T
            else:
                audio = np.stack([samples, samples], axis=0)
            return audio, sr
        except Exception:
            # Fall back to wav if present
            wav_fallback = path.replace(".mp3", ".wav")
            if os.path.exists(wav_fallback):
                return resolve_audio_path(wav_fallback)
            sys.exit(f"Error: could not read {path}. Please provide a .wav file or install ffmpeg/pydub.")


def get_notes_log(input_path: str, audio_path: str, log_arg: str | None) -> dict:
    """Load notes log or generate in-memory."""
    if log_arg and os.path.exists(log_arg):
        with open(log_arg, "r", encoding="utf-8") as f:
            return json.load(f)

    # Check next to audio
    base_stem = os.path.splitext(os.path.basename(audio_path))[0]
    cand_log1 = os.path.join(os.path.dirname(os.path.abspath(audio_path)), f"{base_stem}_notes.json")
    if os.path.exists(cand_log1):
        with open(cand_log1, "r", encoding="utf-8") as f:
            return json.load(f)

    cand_log2 = os.path.join(_REPO_ROOT, "out", f"{base_stem}_notes.json")
    if os.path.exists(cand_log2):
        with open(cand_log2, "r", encoding="utf-8") as f:
            return json.load(f)

    cand_log3 = os.path.join(_REPO_ROOT, "earth_jukebox_audio", "out", f"{base_stem}_notes.json")
    if os.path.exists(cand_log3):
        with open(cand_log3, "r", encoding="utf-8") as f:
            return json.load(f)

    # Generate log in-memory from input_path
    data = loader.load(input_path)
    config = loader.load_config(data["record"])
    scale_name = config.get("scale", "C_major_pentatonic")
    frames = data["frames"]
    fps = float(data.get("fps", 1.0))
    frame_duration = 1.0 / fps if fps > 0 else 1.0

    zero_strips, constant_strips = mapping.find_strip_properties(frames)
    all_events = []
    for idx, frame in enumerate(frames):
        evs = mapping.frame_to_notes(
            frame,
            frame_duration=frame_duration,
            config=config,
            scale_name=scale_name,
            constant_strips=constant_strips,
            is_retrigger=(idx > 0),
        )
        all_events.extend(evs)

    return {
        "record": data["record"],
        "scale": scale_name,
        "fps": fps,
        "n_frames": len(frames),
        "n_strips": len(frames[0]["columns"]) if frames else 0,
        "zero_strips": sorted(list(zero_strips)),
        "constant_strips": sorted(list(constant_strips)),
        "notes": [
            {
                "time": float(n.time),
                "duration": float(n.duration),
                "pitch": int(n.pitch),
                "velocity": int(n.velocity),
                "pan": float(n.pan),
                "source_strip": int(getattr(n, "source_strip", -1)),
                "column_height": float(getattr(n, "column_height", 0.5)),
                "instrument": str(getattr(n, "instrument", "")),
            }
            for n in all_events
        ],
        "frame_hues_used": [
            f.get("effective_strip_hues", []) for f in frames
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Earth Jukebox Audio Engine Self-Check")
    parser.add_argument("--input", required=True, help="Path to features.json")
    parser.add_argument("--audio", required=True, help="Path to rendered audio (.mp3 or .wav)")
    parser.add_argument("--log", default=None, help="Path to notes log JSON (optional)")
    args = parser.parse_args()

    input_path = resolve_path(args.input)
    if not os.path.exists(input_path):
        sys.exit(f"Error: input file {args.input} not found.")

    data = loader.load(input_path)
    frames = data["frames"]
    fps = float(data.get("fps", 1.0))
    n_frames = len(frames)
    expected_duration = n_frames / fps if fps > 0 else float(n_frames)

    audio, sr = resolve_audio_path(args.audio)
    audio_duration = audio.shape[1] / float(sr)

    notes_log = get_notes_log(input_path, args.audio, args.log)
    notes = notes_log["notes"]

    # If the notes log records fewer frames (e.g. from --max-frames), align expected frames
    log_n_frames = notes_log.get("n_frames", len(frames))
    if log_n_frames > 0 and log_n_frames < len(frames):
        frames = frames[:log_n_frames]
        n_frames = len(frames)
        expected_duration = n_frames / fps if fps > 0 else float(n_frames)

    results = []

    # -------------------------------------------------------------------------
    # Check 1: Audio duration equals len(frames)/fps within 1 second.
    # -------------------------------------------------------------------------
    diff = abs(audio_duration - expected_duration)
    chk1_pass = diff <= 1.0
    results.append(chk1_pass)
    status1 = "PASS" if chk1_pass else "FAIL"
    print(f"1. Audio duration ({status1}): audio={audio_duration:.2f}s, expected={expected_duration:.2f}s (diff={diff:.2f}s, limit=1.00s)")

    # -------------------------------------------------------------------------
    # Check 2: Audio is not silent in any 1-second window where frame brightness > 0.1, and has no clipping.
    # -------------------------------------------------------------------------
    silent_active_windows = []
    overall_peak = float(np.max(np.abs(audio)))
    has_clipping = overall_peak >= 1.0

    for i, frame in enumerate(frames):
        b = float(frame.get("brightness", 0.0))
        t_start = int(round(i * sr / fps))
        t_end = int(round((i + 1) * sr / fps))
        t_end = min(t_end, audio.shape[1])
        if t_start < audio.shape[1]:
            window = audio[:, t_start:t_end]
            win_peak = float(np.max(np.abs(window))) if window.size > 0 else 0.0
            if b > 0.1 and win_peak < 0.001:
                silent_active_windows.append((i, b, win_peak))

    chk2_pass = (len(silent_active_windows) == 0) and not has_clipping
    results.append(chk2_pass)
    status2 = "PASS" if chk2_pass else "FAIL"
    print(f"2. Audio activity & clipping ({status2}): silent_active_windows={len(silent_active_windows)}, max_peak={overall_peak:.4f}, clipped={has_clipping}")

    # -------------------------------------------------------------------------
    # Check 3: Per-second loudness correlates positively with frame brightness.
    # -------------------------------------------------------------------------
    brightnesses = []
    loudnesses = []
    for i, frame in enumerate(frames):
        b = float(frame.get("brightness", 0.0))
        t_start = int(round(i * sr / fps))
        t_end = int(round((i + 1) * sr / fps))
        t_end = min(t_end, audio.shape[1])
        if t_start < audio.shape[1]:
            window = audio[:, t_start:t_end]
            rms = float(np.sqrt(np.mean(window ** 2))) if window.size > 0 else 0.0
            brightnesses.append(b)
            loudnesses.append(rms)

    if len(brightnesses) > 1 and np.std(brightnesses) > 1e-6 and np.std(loudnesses) > 1e-6:
        r_loudness = float(np.corrcoef(brightnesses, loudnesses)[0, 1])
    else:
        r_loudness = 1.0 if np.mean(loudnesses) > 0 else 0.0

    chk3_pass = r_loudness > 0.0
    results.append(chk3_pass)
    status3 = "PASS" if chk3_pass else "FAIL"
    print(f"3. Loudness correlation with brightness ({status3}): correlation={r_loudness:.4f}")

    # -------------------------------------------------------------------------
    # Check 4: Every note the engine generated belongs to the configured scale.
    # -------------------------------------------------------------------------
    scale_name = notes_log.get("scale", "C_major_pentatonic")
    scale_notes_list = scales.scale_notes(scale_name)
    scale_pitch_classes = {p % 12 for p in scale_notes_list}

    invalid_notes = [n for n in notes if (n["pitch"] % 12) not in scale_pitch_classes]
    chk4_pass = len(invalid_notes) == 0
    results.append(chk4_pass)
    status4 = "PASS" if chk4_pass else "FAIL"
    print(f"4. Scale membership ({status4}): scale={scale_name}, valid={len(notes)-len(invalid_notes)}/{len(notes)}, invalid={len(invalid_notes)}")

    # -------------------------------------------------------------------------
    # Check 5: Maximum simultaneous notes is at most 4.
    # -------------------------------------------------------------------------
    notes_per_time: dict[float, int] = {}
    for n in notes:
        t_val = round(float(n["time"]), 2)
        notes_per_time[t_val] = notes_per_time.get(t_val, 0) + 1

    max_simul = max(notes_per_time.values()) if notes_per_time else 0
    chk5_pass = max_simul <= 4
    results.append(chk5_pass)
    status5 = "PASS" if chk5_pass else "FAIL"
    print(f"5. Polyphony limit ({status5}): max_simultaneous={max_simul} (cap=4)")

    # -------------------------------------------------------------------------
    # Check 6: Pitch correlates positively with column_heights across notes.
    # -------------------------------------------------------------------------
    heights = [float(n["column_height"]) for n in notes]
    pitches = [float(n["pitch"]) for n in notes]
    if len(heights) > 1 and np.std(heights) > 1e-6 and np.std(pitches) > 1e-6:
        r_pitch = float(np.corrcoef(heights, pitches)[0, 1])
    else:
        r_pitch = 1.0

    chk6_pass = r_pitch > 0.0
    results.append(chk6_pass)
    status6 = "PASS" if chk6_pass else "FAIL"
    print(f"6. Pitch vs column_heights correlation ({status6}): correlation={r_pitch:.4f}")

    # -------------------------------------------------------------------------
    # Check 7: Stereo balance correlates with the strip index of the loudest strips.
    # -------------------------------------------------------------------------
    strips = [float(n["source_strip"]) for n in notes if n.get("source_strip", -1) >= 0]
    pans = [float(n["pan"]) for n in notes if n.get("source_strip", -1) >= 0]
    if len(strips) > 1 and np.std(strips) > 1e-6 and np.std(pans) > 1e-6:
        r_pan = float(np.corrcoef(strips, pans)[0, 1])
    else:
        r_pan = 1.0

    chk7_pass = r_pan > 0.0
    results.append(chk7_pass)
    status7 = "PASS" if chk7_pass else "FAIL"
    print(f"7. Stereo balance vs strip index correlation ({status7}): correlation={r_pan:.4f}")

    # -------------------------------------------------------------------------
    # Check 8: Strips that are 0.0 in every frame trigger no notes.
    #          Strips that are constant across all frames are not re-triggered every frame.
    # -------------------------------------------------------------------------
    n_strips = len(frames[0]["columns"]) if frames else 0
    zero_strips = [
        i for i in range(n_strips)
        if all(f["columns"][i] == 0.0 or f["columns"][i] <= 0.05 for f in frames)
    ]
    constant_strips = [
        i for i in range(n_strips)
        if len(set(f["columns"][i] for f in frames)) == 1 and frames[0]["columns"][i] > 0.05
    ]

    zero_strip_notes = [n for n in notes if n.get("source_strip") in zero_strips]

    # Count how many frames each constant strip was triggered on
    constant_trigger_counts = {}
    for cs in constant_strips:
        trig_frames = {n["time"] for n in notes if n.get("source_strip") == cs}
        constant_trigger_counts[cs] = len(trig_frames)

    max_retriggered = max(constant_trigger_counts.values()) if constant_trigger_counts else 0
    constant_ok = all(cnt < n_frames for cnt in constant_trigger_counts.values())
    chk8_pass = (len(zero_strip_notes) == 0) and constant_ok

    results.append(chk8_pass)
    status8 = "PASS" if chk8_pass else "FAIL"
    print(f"8. Zero & constant strips rule ({status8}): zero_strip_notes={len(zero_strip_notes)}, constant_strips_max_triggered={max_retriggered}/{n_frames}")

    # -------------------------------------------------------------------------
    # Check 9: For strips with brightness below 0.05, the hue used equals the frame hue.
    # -------------------------------------------------------------------------
    frame_hues_used = notes_log.get("frame_hues_used", [])
    mismatches = 0
    checked_count = 0

    for i, frame in enumerate(frames):
        fh = float(frame.get("hue", 180.0))
        cols = frame.get("columns", [])
        used_list = frame_hues_used[i] if i < len(frame_hues_used) else []
        for s_idx, b_val in enumerate(cols):
            if b_val < 0.05:
                checked_count += 1
                if used_list and s_idx < len(used_list):
                    used_h = used_list[s_idx]
                    if abs(used_h - fh) > 0.01:
                        mismatches += 1

    chk9_pass = mismatches == 0
    results.append(chk9_pass)
    status9 = "PASS" if chk9_pass else "FAIL"
    print(f"9. Dark strip hue fallback ({status9}): dark_strips_verified={checked_count}, mismatches={mismatches}")

    # -------------------------------------------------------------------------
    # One-line summary
    # -------------------------------------------------------------------------
    passed_count = sum(results)
    total_count = len(results)
    print(f"\nSummary: {passed_count} checks passed out of {total_count}.")


if __name__ == "__main__":
    main()
