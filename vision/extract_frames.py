"""
Extract ~1 frame/sec from each NASA Earth-observation video and save as
width-1280 PNGs.  Uses ffmpeg exclusively (no OpenCV decode) so the
HEVC 7680×2160 night-lights clip is handled without issues.

Usage:
    python vision/extract_frames.py                # 1 fps (default)
    python vision/extract_frames.py --fps 0.5      # one frame every 2 s
    python vision/extract_frames.py --ffmpeg C:\\ffmpeg\\bin\\ffmpeg.exe
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ── configuration ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIDEOS_DIR = PROJECT_ROOT / "data" / "videos"
FRAMES_DIR = PROJECT_ROOT / "data" / "frames"

RECORDS: dict[str, str] = {
    "night_lights": "night_lights.mp4",
    "lightning":    "lightning.mp4",
    "ocean":        "ocean.webm",
    "gedi":         "gedi.mp4",
}


# ── helpers ──────────────────────────────────────────────────────────
def _find_ffmpeg(override: str | None) -> str:
    """Return a working ffmpeg path, or exit with a helpful message."""
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        sys.exit(f"ERROR: --ffmpeg path not found: {p}")

    found = shutil.which("ffmpeg")
    if found:
        return found

    sys.exit(
        "ERROR: ffmpeg not found on PATH.\n"
        "  Install it from https://www.gyan.dev/ffmpeg/builds/  (Windows)\n"
        "  or pass --ffmpeg /path/to/ffmpeg.exe"
    )


def _frame_dir(record: str) -> Path:
    return FRAMES_DIR / record


def _already_extracted(record: str) -> bool:
    """Return True if the output directory already contains at least one PNG."""
    d = _frame_dir(record)
    return d.is_dir() and any(d.glob("frame_*.png"))


def extract_frames(
    record: str,
    video_file: str,
    fps: float,
    ffmpeg: str,
) -> int:
    """
    Call ffmpeg to extract frames from *video_file* at the requested
    *fps*, scale to 1280 px wide (keeping aspect ratio), and write
    sequentially-numbered PNGs into data/frames/<record>/.

    Returns the number of frames written.
    """
    src = VIDEOS_DIR / video_file
    if not src.exists():
        print(f"  ⚠  video not found: {src}")
        return 0

    out_dir = _frame_dir(record)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_pattern = str(out_dir / "frame_%04d.png")

    # -vf chain: select at desired fps, then scale width→1280 (height=-2
    # keeps aspect ratio and ensures an even pixel count for encoders).
    vf = f"fps={fps},scale=1280:-2"

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "warning",
        "-i", str(src),
        "-vf", vf,
        "-fps_mode", "vfr",
        out_pattern,
    ]

    subprocess.run(cmd, check=True)

    count = len(list(out_dir.glob("frame_*.png")))
    return count


# ── main ─────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract frames from Earth-observation videos."
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=1.0,
        help="Frames per second to extract (default: 1).",
    )
    parser.add_argument(
        "--ffmpeg",
        type=str,
        default=None,
        help="Path to ffmpeg binary (default: search PATH).",
    )
    args = parser.parse_args()

    ffmpeg = _find_ffmpeg(args.ffmpeg)
    print(f"Using ffmpeg: {ffmpeg}\n")

    for record, filename in RECORDS.items():
        print(f"[{record}]")

        if _already_extracted(record):
            n = len(list(_frame_dir(record).glob("frame_*.png")))
            print(f"  ✓ already extracted ({n} frames) — skipping")
            continue

        n = extract_frames(record, filename, args.fps, ffmpeg)
        print(f"  → {n} frames extracted")

    print("\nDone.")


if __name__ == "__main__":
    main()
