"""
render.py — Earth Jukebox: The Planet's Record Collection
NASA Space Apps Hackathon

Main CLI entrypoint for the sound engine.

Usage
-----
  python render.py --input data/night_lights.json --out out/night_lights.mp3
  python render.py --all
  python render.py --check
  python render.py --input data/night_lights.json --backend numpy
  python render.py --input data/night_lights.json --backend fluidsynth
  python render.py --input data/night_lights.json --backend auto   (default)

Pipeline (--input / --all):
  loader.load() → loader.load_config() → mapping.frame_to_notes() per frame
  → mapping.smooth_volume() → renderer → export.normalize()
  → export.save_wav() → export.save_mp3()
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys

# ---------------------------------------------------------------------------
# Path setup — allow running this script from any working directory.
# The src/ package lives next to render.py, so we insert its parent (the
# project root) at the front of sys.path.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from src import loader, mapping, export  # noqa: E402
from src.mapping import NoteEvent  # noqa: E402


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def _import_numpy_renderer():
    """Import numpy_renderer lazily so missing deps give a clear error."""
    try:
        from src import numpy_renderer  # type: ignore[import]
        return numpy_renderer
    except ImportError as exc:
        sys.exit(f"numpy_renderer not available: {exc}")


def _import_fluidsynth_renderer():
    """Import fluidsynth_renderer lazily."""
    try:
        from src import fluidsynth_renderer  # type: ignore[import]
        return fluidsynth_renderer
    except ImportError as exc:
        sys.exit(f"fluidsynth_renderer not available: {exc}")


def _choose_backend(backend_arg: str) -> tuple[str, object]:
    """
    Resolve the --backend flag to (backend_name, renderer_module).

    'auto' tries FluidSynth first; falls back to numpy if unavailable.
    Returns a string name for display ("fluidsynth" or "numpy") and the
    module object.
    """
    if backend_arg == "auto":
        # Try FluidSynth first; numpy is always available as the fallback.
        try:
            fs = _import_fluidsynth_renderer()
            if fs.is_available():
                return "fluidsynth", fs
        except SystemExit:
            pass
        return "numpy", _import_numpy_renderer()

    if backend_arg == "fluidsynth":
        fs = _import_fluidsynth_renderer()
        if not fs.is_available():
            print("Warning: FluidSynth requested but is_available() returned False. "
                  "Continuing anyway — rendering may fail.")
        return "fluidsynth", fs

    # backend_arg == "numpy"
    return "numpy", _import_numpy_renderer()


# ---------------------------------------------------------------------------
# Core render pipeline
# ---------------------------------------------------------------------------

def _render_file(
    input_path: str,
    out_path: str,
    backend_arg: str,
    max_frames: int | None = None,
    log_path: str | None = None,
) -> dict:
    """
    Run the full render pipeline for one input file.

    Returns a stats dict (used by --check and the success message).
    Raises ValueError / SystemExit on unrecoverable errors.
    """

    # Step 1 — Load validated data dict from features.json.
    data = loader.load(input_path)

    # Step 2 — Load the per-record config (scale, BPM, instrument …).
    config = loader.load_config(data["record"])
    scale_name: str = config.get("scale", "C_major_pentatonic")

    # Step 3 — Convert each frame to NoteEvents.
    all_events: list[NoteEvent] = []
    frames = data["frames"]
    if max_frames is not None and max_frames > 0:
        frames = frames[:max_frames]

    fps = float(data.get("fps", 1.0))
    frame_duration = 1.0 / fps if fps > 0 else 1.0

    # Track whether column_heights / column_hues were present in the raw data
    has_column_heights = any("column_heights" in f for f in frames)
    has_column_hues = any("column_hues" in f for f in frames)

    # Identify constant strips and zero strips across frames
    zero_strips, constant_strips = mapping.find_strip_properties(frames)

    for idx, frame in enumerate(frames):
        notes = mapping.frame_to_notes(
            frame,
            frame_duration=frame_duration,
            config=config,
            scale_name=scale_name,
            constant_strips=constant_strips,
            is_retrigger=(idx > 0),
        )
        all_events.extend(notes)

    # Step 5 — Smooth velocities across the whole track to avoid jarring jumps.
    if all_events:
        raw_velocities = [float(n.velocity) for n in all_events]
        smoothed = mapping.smooth_volume(raw_velocities)
        for event, v in zip(all_events, smoothed):
            event.velocity = int(max(1, min(127, round(v))))

    # Write debug notes log if requested
    if log_path:
        import json
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_data = {
            "record": data["record"],
            "scale": scale_name,
            "fps": fps,
            "n_frames": len(frames),
            "n_strips": len(frames[0]["columns"]) if frames else 0,
            "zero_strips": sorted(list(zero_strips)),
            "constant_strips": sorted(list(constant_strips)),
            "notes": [
                {
                    "time": round(float(n.time), 4),
                    "duration": round(float(n.duration), 4),
                    "pitch": int(n.pitch),
                    "velocity": int(n.velocity),
                    "pan": round(float(n.pan), 4),
                    "source_strip": int(getattr(n, "source_strip", -1)),
                    "column_height": round(float(getattr(n, "column_height", 0.5)), 4),
                    "instrument": str(getattr(n, "instrument", "")),
                }
                for n in all_events
            ],
            "frame_hues_used": [
                f.get("effective_strip_hues", []) for f in frames
            ],
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2)

    # Step 6 — Determine which backend to use.
    backend_name, renderer = _choose_backend(backend_arg)

    # Step 7 — Render NoteEvents to a numpy array.
    total_duration = float(len(frames)) * frame_duration

    if backend_name == "fluidsynth":
        signal = renderer.render_with_fluidsynth(all_events, total_duration, config)
    else:
        signal = renderer.render_to_array(all_events, total_duration)

    # Step 8 — Normalize to consistent loudness.
    signal = export.normalize(signal)

    # Step 9 — Save WAV (always succeeds if scipy is installed).
    wav_path = out_path.replace(".mp3", ".wav")
    export.save_wav(signal, wav_path)

    # Step 10 — Save MP3 (graceful fallback; WAV is already saved above).
    export.save_mp3(signal, out_path)

    # Compute audio stats for --check and the final print line.
    import numpy as np
    peak = float(np.max(np.abs(signal)))
    rms = float(np.sqrt(np.mean(signal ** 2)))
    is_silent = peak < 0.001
    is_clipped = peak >= 1.0

    return {
        "out_path": out_path,
        "duration": total_duration,
        "n_notes": len(all_events),
        "scale": scale_name,
        "backend": backend_name,
        "n_frames": len(frames),
        "has_column_heights": has_column_heights,
        "has_column_hues": has_column_hues,
        "peak": peak,
        "rms": rms,
        "is_silent": is_silent,
        "is_clipped": is_clipped,
        "filename": os.path.basename(input_path),
    }


# ---------------------------------------------------------------------------
# --check mode
# ---------------------------------------------------------------------------

def _run_check(data_dir: str, backend_arg: str) -> None:
    """
    Self-check: dry-run the pipeline on every .json in data/, then print a
    formatted table of stats.  Audio is actually rendered so we can report
    real peak/RMS figures.
    """
    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))

    if not json_files:
        print("data/ is empty. No .json files found.")
        print("Expected files:")
        for name in ("night_lights.json", "lightning.json",
                     "ocean_currents.json", "gedi_forest.json"):
            print(f"  data/{name}")
        return

    rows: list[dict] = []

    for path in json_files:
        # Route output to a temporary location so --check doesn't pollute out/.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            stats = _render_file(path, tmp_path, backend_arg)
            rows.append(stats)
        except Exception as exc:
            rows.append({
                "filename": os.path.basename(path),
                "error": str(exc),
            })
        finally:
            # Clean up temp files.
            for ext in (".mp3", ".wav"):
                candidate = tmp_path.replace(".mp3", ext)
                if os.path.exists(candidate):
                    try:
                        os.remove(candidate)
                    except OSError:
                        pass

    # --- Print formatted table -------------------------------------------
    # Column widths chosen to fit expected filenames and stat ranges.
    COL = {
        "filename":           20,
        "dur(s)":             7,
        "frames":             6,
        "notes":              6,
        "scale":              22,
        "backend":            11,
        "col_heights":        11,
        "col_hues":           9,
        "peak":               6,
        "RMS":                6,
        "silent":             7,
        "clipped":            8,
    }

    header = "  ".join(k.ljust(v) for k, v in COL.items())
    divider = "  ".join("-" * v for v in COL.values())
    print()
    print(header)
    print(divider)

    for row in rows:
        if "error" in row:
            err_line = row["filename"].ljust(COL["filename"]) + "  " + f"ERROR: {row['error']}"
            print(err_line)
            continue

        line = "  ".join([
            row["filename"].ljust(COL["filename"]),
            f"{row['duration']:.1f}".ljust(COL["dur(s)"]),
            str(row["n_frames"]).ljust(COL["frames"]),
            str(row["n_notes"]).ljust(COL["notes"]),
            row["scale"].ljust(COL["scale"]),
            row["backend"].ljust(COL["backend"]),
            ("yes" if row["has_column_heights"] else "default").ljust(COL["col_heights"]),
            ("yes" if row["has_column_hues"] else "default").ljust(COL["col_hues"]),
            f"{row['peak']:.3f}".ljust(COL["peak"]),
            f"{row['rms']:.3f}".ljust(COL["RMS"]),
            ("yes" if row["is_silent"] else "no").ljust(COL["silent"]),
            ("YES" if row["is_clipped"] else "no").ljust(COL["clipped"]),
        ])
        print(line)

    print()


# ---------------------------------------------------------------------------
# --all mode
# ---------------------------------------------------------------------------

def _run_all(data_dir: str, out_dir: str, backend_arg: str) -> None:
    """
    Render every .json file in data/, skipping files that fail validation.
    """
    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))

    if not json_files:
        print("data/ is empty — nothing to render.")
        return

    for path in json_files:
        stem = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, f"{stem}.mp3")

        try:
            stats = _render_file(path, out_path, backend_arg)
            print(
                f"Rendered: {stats['out_path']} "
                f"[{stats['duration']:.1f}s, {stats['n_notes']} notes, "
                f"scale={stats['scale']}, backend={stats['backend']}]"
            )
        except (ValueError, SystemExit) as exc:
            # Validation failures or renderer errors are non-fatal in --all mode.
            print(f"Skipping {os.path.basename(path)}: {exc}")
        except Exception as exc:
            print(f"Skipping {os.path.basename(path)}: unexpected error — {exc}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render.py",
        description="Earth Jukebox — render NASA satellite data to audio.",
    )

    # Mutually exclusive mode group.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--input", "-i",
        metavar="PATH",
        help="Path to a single features.json file to render.",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Render every .json file found in data/.",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Self-check: run pipeline on data/*.json and print stats.",
    )

    # Output path (only relevant for --input).
    parser.add_argument(
        "--out", "-o",
        metavar="PATH",
        default=None,
        help="Output file path (default: out/<input_stem>.mp3). Used with --input.",
    )

    # Backend selection.
    parser.add_argument(
        "--backend",
        choices=["auto", "numpy", "fluidsynth"],
        default="auto",
        help="Audio renderer to use. 'auto' tries FluidSynth then falls back to numpy.",
    )

    # Log path for note events
    parser.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help="Optional debug log JSON path to write all note events.",
    )

    # Max frames to render
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        metavar="N",
        help="Only render the first N frames.",
    )

    # Data and output directory overrides.
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        default=os.path.join(_PROJECT_ROOT, "data"),
        help="Directory containing features.json files (default: data/).",
    )
    parser.add_argument(
        "--out-dir",
        metavar="DIR",
        default=os.path.join(_PROJECT_ROOT, "out"),
        help="Output directory for rendered files (default: out/).",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Require at least one mode.
    if not args.input and not args.all and not args.check:
        parser.print_help()
        sys.exit(1)

    # --- --check mode --------------------------------------------------------
    if args.check:
        _run_check(args.data_dir, args.backend)
        return

    # --- --all mode ----------------------------------------------------------
    if args.all:
        _run_all(args.data_dir, args.out_dir, args.backend)
        return

    # --- --input mode --------------------------------------------------------
    # Determine output path: use --out if given, otherwise derive from input.
    if args.out:
        out_path = args.out
    else:
        stem = os.path.splitext(os.path.basename(args.input))[0]
        out_path = os.path.join(args.out_dir, f"{stem}.mp3")

    try:
        stats = _render_file(
            args.input,
            out_path,
            args.backend,
            max_frames=args.max_frames,
            log_path=args.log,
        )
    except ValueError as exc:
        sys.exit(f"Error: {exc}")

    # Step 11 — Print the summary line.
    print(
        f"Rendered: {stats['out_path']} "
        f"[{stats['duration']:.1f}s, {stats['n_notes']} notes, "
        f"scale={stats['scale']}, backend={stats['backend']}]"
    )


if __name__ == "__main__":
    main()
