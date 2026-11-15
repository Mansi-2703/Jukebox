"""
sound/generate.py — Earth Jukebox Sound Engine
Person B: Read features.json and render audio.

Usage:
    python sound/generate.py --input data/features/night_lights.json --out out/night_lights.mp3
    python sound/generate.py --input data/features/night_lights.json --log out/night_lights_notes.json
    python sound/generate.py --input data/features/night_lights.json --max-frames 20
    python sound/generate.py --all
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure earth_jukebox_audio is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AUDIO_PKG = os.path.join(_REPO_ROOT, "earth_jukebox_audio")
if _AUDIO_PKG not in sys.path:
    sys.path.insert(0, _AUDIO_PKG)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from render import main as render_main, _render_file, _build_parser


def resolve_input_path(path: str) -> str:
    """Resolve input path, checking both cwd and repo root if needed."""
    if os.path.exists(path):
        return path
    candidate = os.path.join(_REPO_ROOT, path)
    if os.path.exists(candidate):
        return candidate
    # Try data/features if data/ was passed
    if "data/" in path and "features" not in path:
        features_path = path.replace("data/", "data/features/")
        if os.path.exists(features_path):
            return features_path
        candidate_features = os.path.join(_REPO_ROOT, features_path)
        if os.path.exists(candidate_features):
            return candidate_features
    return path


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.input and not args.all and not args.check:
        parser.print_help()
        sys.exit(1)

    if args.input:
        args.input = resolve_input_path(args.input)

    # Re-dispatch with resolved path
    if args.check or args.all:
        render_main()
        return

    # Derive output path if omitted
    if args.out:
        out_path = args.out
    else:
        stem = os.path.splitext(os.path.basename(args.input))[0]
        out_path = os.path.join(_REPO_ROOT, "out", f"{stem}.mp3")

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

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

    print(
        f"Rendered: {stats['out_path']} "
        f"[{stats['duration']:.1f}s, {stats['n_notes']} notes, "
        f"scale={stats['scale']}, backend={stats['backend']}]"
    )


if __name__ == "__main__":
    main()
