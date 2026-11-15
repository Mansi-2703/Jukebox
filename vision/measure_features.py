"""
Compute per-frame visual features for each record in data/frames/.

Features (all normalised 0-1 except hue):
    brightness  – mean V (or flash fraction for lightning), rescaled
                  per-record using the 5th/95th percentile, clipped 0-1
    hue         – dominant hue in degrees 0-360, computed only from
                  unmasked pixels (sat-weighted histogram)
    saturation  – mean S of unmasked pixels, /255
    edges       – fraction of pixels flagged by Canny
    motion      – mean absolute diff from previous frame, /255
    purple      – (night_lights only) fraction of non-dark pixels with
                  hue 250-310° and S > 0.3, rescaled 5th/95th percentile

Per-strip features (N_STRIPS vertical strips per frame):
    columns         – mean brightness (or flash fraction) per strip,
                      rescaled same as the frame brightness
    column_heights  – brightness/flash-weighted average row position,
                      0 = bottom, 1 = top  (0.5 if no signal)
    column_hues     – dominant hue per strip; frame hue if no signal

Usage:
    python vision/measure_features.py
    python vision/measure_features.py --save-crops
    python vision/measure_features.py --find-cuts
    python vision/measure_features.py --record night_lights --contact-sheet
    python vision/measure_features.py --record night_lights --timeline
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

# ── constants ────────────────────────────────────────────────────────
N_STRIPS = 32          # number of vertical strips per frame

# ── project paths ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRAMES_DIR = PROJECT_ROOT / "data" / "frames"
FEATURES_DIR = PROJECT_ROOT / "data" / "features"

RECORDS = ["night_lights", "lightning", "ocean", "gedi"]

# ── trim ranges ──────────────────────────────────────────────────────
# (start_seconds, end_seconds) per record.  None = no trim on that end.
# Frames outside the range are skipped; "t" counts from the first kept
# frame.  Frame number → seconds assumes 1 fps extraction.
TRIM: dict[str, tuple[float | None, float | None]] = {
    "lightning":    (70, None),
    "night_lights": (None, 22),
}

# ── crop rectangles ──────────────────────────────────────────────────
# Region to *keep* as fractions: (left, top, right, bottom).
#
# night_lights – keep only the LEFT copy of the world map; the video
#                shows it twice and a "2012" label sits between them
# lightning    – removes the date dial and "10 Day Roving Window"
#                label at bottom-right
# ocean        – trim the thin bright title bar at the very top
# gedi         – large black margins, colorbar at bottom, labels at top
CROP: dict[str, tuple[float, float, float, float]] = {
    "night_lights": (0.00, 0.02, 0.49, 0.98),
    "lightning":    (0.05, 0.03, 0.80, 0.97),
    "ocean":        (0.00, 0.01, 1.00, 1.00),
    "gedi":         (0.05, 0.03, 0.78, 0.88),
}


# ── pixel masks ──────────────────────────────────────────────────────
# Each function returns a boolean mask (True = *keep* this pixel).

def _mask_night_lights(hsv: np.ndarray) -> np.ndarray:
    """Keep pixels with V >= 15 (ignore near-black)."""
    return hsv[:, :, 2] >= 15


def _mask_lightning(hsv: np.ndarray) -> np.ndarray:
    """
    Flash mask: yellow pixels with hue 40-70°, saturation > 0.5,
    V > 0.6.  OpenCV hue is 0-179, so 40-70° → 20-35.
    """
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    return (h >= 20) & (h <= 35) & (s > 128) & (v > 153)


def _mask_ocean(hsv: np.ndarray) -> np.ndarray:
    """Ignore grey land (saturation < 0.25 → S < 64)."""
    return hsv[:, :, 1] >= 64


def _mask_gedi(hsv: np.ndarray) -> np.ndarray:
    """
    Ignore blue ocean (hue 180-260° → OpenCV 90-130) and black space
    (V < 30) so hue reflects forest colours.
    """
    h = hsv[:, :, 0]
    v = hsv[:, :, 2]
    not_black = v >= 30
    not_blue = ~((h >= 90) & (h <= 130))
    return not_black & not_blue


PIXEL_MASK = {
    "night_lights": _mask_night_lights,
    "lightning":    _mask_lightning,
    "ocean":        _mask_ocean,
    "gedi":         _mask_gedi,
}


# ── frame helpers ────────────────────────────────────────────────────

def _crop_frame(frame: np.ndarray, record: str) -> np.ndarray:
    """Return a cropped copy if *record* has a CROP entry, else original."""
    if record not in CROP:
        return frame
    l, t, r, b = CROP[record]
    h, w = frame.shape[:2]
    return frame[int(h * t) : int(h * b), int(w * l) : int(w * r)]


def _build_mask(hsv: np.ndarray, record: str) -> np.ndarray:
    """Build a 2-D boolean keep-mask for *record* (True = keep)."""
    if record in PIXEL_MASK:
        return PIXEL_MASK[record](hsv)
    return hsv[:, :, 2] >= 30


def _frame_in_trim(frame_idx: int, record: str, fps: float = 1.0) -> bool:
    """Return True if frame *frame_idx* (0-based) falls inside the TRIM range."""
    if record not in TRIM:
        return True
    start, end = TRIM[record]
    t = frame_idx / fps
    if start is not None and t < start:
        return False
    if end is not None and t >= end:
        return False
    return True


# ── scalar feature helpers ───────────────────────────────────────────

def _raw_brightness(hsv: np.ndarray, mask: np.ndarray, is_lightning: bool) -> float:
    """
    Lightning: fraction of flash (masked-in) pixels.
    Others:    mean V of masked pixels, /255.
    """
    if is_lightning:
        return float(mask.sum() / mask.size)
    v_masked = hsv[:, :, 2][mask]
    if v_masked.size == 0:
        return 0.0
    return float(v_masked.mean() / 255.0)


def _masked_saturation(hsv: np.ndarray, mask: np.ndarray) -> float:
    """Mean S of masked pixels, /255."""
    s_masked = hsv[:, :, 1][mask]
    if s_masked.size == 0:
        return 0.0
    return float(s_masked.mean() / 255.0)


def _masked_dominant_hue(hsv: np.ndarray, mask: np.ndarray) -> float:
    """
    Dominant hue (0-360°) from masked pixels only, weighted by
    saturation.  Returns 0.0 if no pixels pass the mask.
    """
    h_vals = hsv[:, :, 0][mask]
    s_vals = hsv[:, :, 1][mask]
    if h_vals.size == 0:
        return 0.0
    hist, _ = np.histogram(
        h_vals, bins=180, range=(0, 180),
        weights=s_vals.astype(np.float64),
    )
    return float(int(np.argmax(hist)) * 2)


def _purple_fraction(hsv: np.ndarray) -> float:
    """
    Fraction of non-dark pixels (V >= 15) that are purple:
    hue 250-310° (OpenCV 125-155) and saturation > 0.3 (S > 77).
    """
    v = hsv[:, :, 2]
    not_dark = v >= 15
    n_not_dark = not_dark.sum()
    if n_not_dark == 0:
        return 0.0
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    purple = not_dark & (h >= 125) & (h <= 155) & (s > 77)
    return float(purple.sum() / n_not_dark)


def _edge_fraction(gray: np.ndarray) -> float:
    """Fraction of pixels flagged by Canny, normalised 0-1."""
    edges = cv2.Canny(gray, threshold1=50, threshold2=150)
    return float((edges > 0).mean())


def _motion(gray: np.ndarray, prev_gray: np.ndarray | None) -> float:
    """Mean absolute difference from previous frame, normalised 0-1."""
    if prev_gray is None:
        return 0.0
    return float(cv2.absdiff(gray, prev_gray).mean() / 255.0)


# ── strip (column) helpers ───────────────────────────────────────────

def _strip_slices(width: int) -> list[tuple[int, int]]:
    """Return (start_col, end_col) for N_STRIPS equal-width strips."""
    edges = np.linspace(0, width, N_STRIPS + 1, dtype=int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(N_STRIPS)]


def _strip_brightness(
    hsv: np.ndarray, mask: np.ndarray,
    slices: list[tuple[int, int]], is_lightning: bool,
) -> list[float]:
    """Per-strip brightness (0-1)."""
    result: list[float] = []
    for s, e in slices:
        strip_mask = mask[:, s:e]
        if is_lightning:
            result.append(float(strip_mask.sum() / strip_mask.size))
        else:
            v_strip = hsv[:, s:e, 2][strip_mask]
            result.append(float(v_strip.mean() / 255.0) if v_strip.size else 0.0)
    return result


def _strip_heights(
    hsv: np.ndarray, mask: np.ndarray,
    slices: list[tuple[int, int]], is_lightning: bool,
) -> list[float]:
    """Weighted average row position per strip (0 = bottom, 1 = top)."""
    h = hsv.shape[0]
    row_idx = np.arange(h, dtype=np.float64)
    heights: list[float] = []
    for s, e in slices:
        strip_mask = mask[:, s:e]
        if is_lightning:
            weights = strip_mask.astype(np.float64)
        else:
            weights = hsv[:, s:e, 2].astype(np.float64) * strip_mask
        total = weights.sum()
        if total == 0:
            heights.append(0.5)
            continue
        row_weights = weights.sum(axis=1)
        mean_row = float(np.dot(row_idx, row_weights) / total)
        heights.append(1.0 - mean_row / (h - 1))
    return heights


def _strip_hues(
    hsv: np.ndarray, mask: np.ndarray,
    slices: list[tuple[int, int]], frame_hue: float,
) -> list[float]:
    """Dominant hue per strip from masked pixels; frame hue as fallback."""
    hues: list[float] = []
    for s, e in slices:
        strip_mask = mask[:, s:e]
        if strip_mask.sum() == 0:
            hues.append(frame_hue)
            continue
        hue = _masked_dominant_hue(hsv[:, s:e, :], strip_mask)
        hues.append(hue)
    return hues


# ── per-record normalisation ─────────────────────────────────────────

def _normalise_brightness(data: list[dict]) -> None:
    """
    Rescale 'brightness' and 'columns' using the 5th/95th percentile
    of frame brightness across the record, then clip to 0-1.
    """
    if not data:
        return
    raw = np.array([d["brightness"] for d in data])
    p5, p95 = float(np.percentile(raw, 5)), float(np.percentile(raw, 95))
    span = p95 - p5 if p95 > p5 else 1.0
    for d in data:
        d["brightness"] = float(np.clip((d["brightness"] - p5) / span, 0.0, 1.0))
        d["columns"] = [
            float(np.clip((v - p5) / span, 0.0, 1.0)) for v in d["columns"]
        ]


def _normalise_purple(data: list[dict]) -> None:
    """Rescale 'purple' with the same 5th/95th scheme, in-place."""
    purples = [d["purple"] for d in data if "purple" in d]
    if not purples:
        return
    arr = np.array(purples)
    p5, p95 = float(np.percentile(arr, 5)), float(np.percentile(arr, 95))
    span = p95 - p5 if p95 > p5 else 1.0
    for d in data:
        if "purple" in d:
            d["purple"] = float(np.clip((d["purple"] - p5) / span, 0.0, 1.0))


# ── per-record pipeline ─────────────────────────────────────────────

def measure_record(record: str) -> list[dict]:
    """
    Read every frame for *record* in order, compute features, and
    return a list of dicts (one per frame).
    """
    folder = FRAMES_DIR / record
    if not folder.is_dir():
        print(f"  ⚠  folder not found: {folder}")
        return []

    paths = sorted(folder.glob("frame_*.png"))
    if not paths:
        print(f"  ⚠  no frames in {folder}")
        return []

    is_lightning = (record == "lightning")
    is_night_lights = (record == "night_lights")
    results: list[dict] = []
    prev_gray: np.ndarray | None = None
    kept = 0

    for frame_idx, path in enumerate(paths):
        # ── trim check ──
        if not _frame_in_trim(frame_idx, record):
            continue

        bgr = cv2.imread(str(path))
        if bgr is None:
            print(f"  ⚠  could not read {path.name}")
            continue

        bgr = _crop_frame(bgr, record)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = _build_mask(hsv, record)
        slices = _strip_slices(bgr.shape[1])

        frame_hue = _masked_dominant_hue(hsv, mask)

        features: dict = {
            "frame":      path.name,
            "t":          kept,              # seconds from first kept frame
            "brightness": _raw_brightness(hsv, mask, is_lightning),
            "hue":        frame_hue,
            "saturation": _masked_saturation(hsv, mask),
            "edges":      _edge_fraction(gray),
            "motion":     _motion(gray, prev_gray),
            "columns":        _strip_brightness(hsv, mask, slices, is_lightning),
            "column_heights": _strip_heights(hsv, mask, slices, is_lightning),
            "column_hues":    _strip_hues(hsv, mask, slices, frame_hue),
        }

        # ── night_lights-only: purple fraction ──
        if is_night_lights:
            features["purple"] = _purple_fraction(hsv)

        results.append(features)
        prev_gray = gray
        kept += 1

    _normalise_brightness(results)
    if is_night_lights:
        _normalise_purple(results)

    return results


# ── cut detection ────────────────────────────────────────────────────

def _find_cuts(all_results: dict[str, list[dict]]) -> None:
    """
    For each record, print timestamps where motion exceeds
    mean + 3 × std — likely scene changes.
    """
    print("\n[cut detection]")
    for record in RECORDS:
        data = all_results.get(record, [])
        if len(data) < 2:
            continue
        motions = np.array([d["motion"] for d in data])
        mu = motions.mean()
        sigma = motions.std()
        threshold = mu + 3 * sigma
        cuts = [
            (d["t"], d["frame"], d["motion"])
            for d, m in zip(data, motions)
            if m > threshold
        ]
        print(f"\n  {record}:  threshold = {threshold:.4f}  "
              f"(mean={mu:.4f}, std={sigma:.4f})")
        if cuts:
            for t, frame, m in cuts:
                print(f"    t={t:>4d}s  {frame}  motion={m:.4f}")
        else:
            print(f"    (no cuts found)")


# ── crop preview ─────────────────────────────────────────────────────

def _save_crop_previews(records: list[str] | None = None) -> None:
    """
    Write THREE cropped sample frames per record (at 10%, 50%, 90%)
    to data/features/crop_check/.
    """
    out_dir = FEATURES_DIR / "crop_check"
    out_dir.mkdir(parents=True, exist_ok=True)

    if records is None:
        records = RECORDS

    samples = {"start": 0.10, "mid": 0.50, "end": 0.90}

    for record in records:
        folder = FRAMES_DIR / record
        all_paths = sorted(folder.glob("frame_*.png"))
        paths = [p for i, p in enumerate(all_paths) if _frame_in_trim(i, record)]
        if not paths:
            continue
        n = len(paths)
        for label, frac in samples.items():
            idx = min(int(n * frac), n - 1)
            bgr = cv2.imread(str(paths[idx]))
            if bgr is None:
                continue
            cropped = _crop_frame(bgr, record)
            out_path = out_dir / f"{record}_{label}.png"
            cv2.imwrite(str(out_path), cropped)
            print(f"  saved {out_path}")


# ── contact sheet ────────────────────────────────────────────────────

def _save_contact_sheet(record: str, data: list[dict]) -> None:
    """
    Save a grid of cropped frames sampled every 5 seconds,
    labelled with timestamp in seconds in the top-left corner,
    4 columns wide, each tile 320 px wide.
    Saved to data/features/crop_check/<record>_sheet.png.
    """
    out_dir = FEATURES_DIR / "crop_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record}_sheet.png"

    folder = FRAMES_DIR / record
    sample_items = [d for d in data if d["t"] % 5 == 0]
    if not sample_items:
        print(f"  ⚠  no frames found for contact sheet: {record}")
        return

    tiles: list[np.ndarray] = []
    tile_w = 320
    tile_h = 0

    for d in sample_items:
        img_path = folder / d["frame"]
        bgr = cv2.imread(str(img_path))
        if bgr is None:
            continue
        cropped = _crop_frame(bgr, record)
        h, w = cropped.shape[:2]
        tile_h = int(round(h * (tile_w / w)))
        tile = cv2.resize(cropped, (tile_w, tile_h), interpolation=cv2.INTER_AREA)

        # Label timestamp in seconds in top-left corner
        text = f"{d['t']}s"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.6
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
        cv2.rectangle(tile, (4, 4), (tw + 12, th + baseline + 10), (0, 0, 0), -1)
        cv2.putText(
            tile, text, (8, th + 7),
            font, scale, (255, 255, 255), thickness, cv2.LINE_AA
        )
        tiles.append(tile)

    if not tiles:
        return

    n_cols = 4
    n_rows = (len(tiles) + n_cols - 1) // n_cols

    # Pad with blank tiles if the last row isn't full
    pad_count = (n_cols * n_rows) - len(tiles)
    for _ in range(pad_count):
        tiles.append(np.zeros((tile_h, tile_w, 3), dtype=np.uint8))

    rows = []
    for r in range(n_rows):
        row_tiles = tiles[r * n_cols : (r + 1) * n_cols]
        rows.append(np.hstack(row_tiles))
    sheet = np.vstack(rows)

    cv2.imwrite(str(out_path), sheet)
    print(f"  saved contact sheet: {out_path} ({len(sample_items)} tiles, {n_rows}x{n_cols})")


# ── timeline ─────────────────────────────────────────────────────────

def _print_timeline(record: str, data: list[dict]) -> None:
    """Print one line every 5 seconds: t, brightness, motion, and (for night_lights) purple."""
    if not data:
        return
    total_frames = len(data)
    duration = total_frames
    print(f"\n[{record} timeline]")
    print(f"  Total frames: {total_frames}")
    print(f"  Total duration: {duration}s")

    is_nl = (record == "night_lights")
    if is_nl:
        header = f"  {'t':>5}  {'brightness':>10}  {'motion':>10}  {'purple':>10}"
    else:
        header = f"  {'t':>5}  {'brightness':>10}  {'motion':>10}"
    print(f"\n{header}")
    print(f"  {'-' * (len(header) - 2)}")

    for d in data:
        if d["t"] % 5 == 0:
            t_str = f"{d['t']}s"
            b_val = d["brightness"]
            m_val = d["motion"]
            if is_nl:
                p_val = d.get("purple", 0.0)
                print(f"  {t_str:>5}  {b_val:10.4f}  {m_val:10.4f}  {p_val:10.4f}")
            else:
                print(f"  {t_str:>5}  {b_val:10.4f}  {m_val:10.4f}")


# ── summary table ────────────────────────────────────────────────────

_SCALAR_KEYS = ["brightness", "hue", "saturation", "edges", "motion"]


def _print_summary(record: str, data: list[dict]) -> None:
    """Print a compact min / mean / max table for one record."""
    if not data:
        return

    header = f"{'feature':<16} {'min':>8} {'mean':>8} {'max':>8}"
    print(f"\n  {header}")
    print(f"  {'-' * len(header)}")

    # scalar keys (including purple if present)
    keys = list(_SCALAR_KEYS)
    if any("purple" in d for d in data):
        keys.append("purple")

    for key in keys:
        vals = [d[key] for d in data if key in d]
        if not vals:
            continue
        lo, avg, hi = min(vals), sum(vals) / len(vals), max(vals)
        if key == "hue":
            print(f"  {key:<16} {lo:8.1f} {avg:8.1f} {hi:8.1f}")
        else:
            print(f"  {key:<16} {lo:8.4f} {avg:8.4f} {hi:8.4f}")

    for key in ("columns", "column_heights", "column_hues"):
        all_vals = [v for d in data for v in d[key]]
        lo, avg, hi = min(all_vals), sum(all_vals) / len(all_vals), max(all_vals)
        if key == "column_hues":
            print(f"  {key:<16} {lo:8.1f} {avg:8.1f} {hi:8.1f}")
        else:
            print(f"  {key:<16} {lo:8.4f} {avg:8.4f} {hi:8.4f}")


# ── JSON output ──────────────────────────────────────────────────────

def _round(val, n=3):
    """Round a float, list of floats, or pass through ints."""
    if isinstance(val, float):
        return round(val, n)
    if isinstance(val, list):
        return [round(v, n) if isinstance(v, float) else v for v in val]
    return val


def _write_json(record: str, data: list[dict]) -> None:
    """
    Write data/features/<record>.json in the specified format:
    {"record": "...", "fps": 1, "n_strips": 32, "frames": [{...}, ...]}
    Floats are rounded to 3 decimals.  The internal "frame" key is dropped.
    """
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FEATURES_DIR / f"{record}.json"

    # Keys to keep in order (purple only for night_lights)
    base_keys = [
        "t", "brightness", "hue", "saturation", "edges", "motion",
        "columns", "column_heights", "column_hues",
    ]

    frames_out = []
    for d in data:
        fd: dict = {}
        for k in base_keys:
            fd[k] = _round(d[k])
        if "purple" in d:
            fd["purple"] = _round(d["purple"])
        frames_out.append(fd)

    payload = {
        "record": record,
        "fps": 1,
        "n_strips": N_STRIPS,
        "frames": frames_out,
    }

    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"  wrote {out_path}  ({size_kb:.1f} KB, {len(frames_out)} frames)")


# ── validation ───────────────────────────────────────────────────────

def _validate(record: str, data: list[dict]) -> bool:
    """
    Check every frame has all required keys, values are in range,
    and column lists have length N_STRIPS.  Prints PASS or FAIL
    (with the first problem found).
    """
    base_keys = {
        "t", "brightness", "hue", "saturation", "edges", "motion",
        "columns", "column_heights", "column_hues",
    }
    unit_keys = {"brightness", "saturation", "edges", "motion"}  # must be 0-1
    hue_keys = {"hue"}                                            # must be 0-360
    column_unit = {"columns", "column_heights"}                    # list, each 0-1
    column_hue = {"column_hues"}                                   # list, each 0-360

    required = set(base_keys)
    if record == "night_lights":
        required.add("purple")

    for i, d in enumerate(data):
        # ── missing keys ──
        missing = required - set(d.keys())
        if missing:
            print(f"  FAIL  frame {i}: missing keys {missing}")
            return False

        # ── scalar 0-1 range ──
        for k in unit_keys:
            v = d[k]
            if not (0.0 <= v <= 1.0):
                print(f"  FAIL  frame {i}: {k}={v} outside 0-1")
                return False

        # ── hue 0-360 ──
        for k in hue_keys:
            v = d[k]
            if not (0.0 <= v <= 360.0):
                print(f"  FAIL  frame {i}: {k}={v} outside 0-360")
                return False

        # ── purple 0-1 ──
        if "purple" in d:
            if not (0.0 <= d["purple"] <= 1.0):
                print(f"  FAIL  frame {i}: purple={d['purple']} outside 0-1")
                return False

        # ── column list lengths ──
        for k in ("columns", "column_heights", "column_hues"):
            if len(d[k]) != N_STRIPS:
                print(f"  FAIL  frame {i}: {k} has {len(d[k])} items, expected {N_STRIPS}")
                return False

        # ── column value ranges ──
        for k in column_unit:
            for j, v in enumerate(d[k]):
                if not (0.0 <= v <= 1.0):
                    print(f"  FAIL  frame {i}: {k}[{j}]={v} outside 0-1")
                    return False

        for k in column_hue:
            for j, v in enumerate(d[k]):
                if not (0.0 <= v <= 360.0):
                    print(f"  FAIL  frame {i}: {k}[{j}]={v} outside 0-360")
                    return False

    print(f"  PASS  ({len(data)} frames)")
    return True


# ── preview image ────────────────────────────────────────────────────

def _save_preview(record: str, data: list[dict]) -> None:
    """
    Save one preview image from the middle frame, cropped and resized
    to 800 px wide, as data/features/<record>_preview.jpg.
    """
    if not data:
        return
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    folder = FRAMES_DIR / record

    mid = data[len(data) // 2]
    img_path = folder / mid["frame"]
    bgr = cv2.imread(str(img_path))
    if bgr is None:
        print(f"  ⚠  could not read {img_path}")
        return

    cropped = _crop_frame(bgr, record)
    h, w = cropped.shape[:2]
    new_w = 800
    new_h = int(round(h * (new_w / w)))
    resized = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_AREA)

    out_path = FEATURES_DIR / f"{record}_preview.jpg"
    cv2.imwrite(str(out_path), resized)
    print(f"  saved preview: {out_path} ({new_w}x{new_h})")


# ── main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure visual features for Earth Jukebox frames.",
    )
    parser.add_argument(
        "--record",
        choices=RECORDS,
        default=None,
        help="Choose a specific record to run (default: all).",
    )
    parser.add_argument(
        "--save-crops", action="store_true",
        help="Write three cropped sample frames per record for visual QA.",
    )
    parser.add_argument(
        "--find-cuts", action="store_true",
        help="Print timestamps of likely scene cuts (motion > mean+3σ).",
    )
    parser.add_argument(
        "--contact-sheet", action="store_true",
        help="Save a 4-column contact sheet sampled every 5s to crop_check/<record>_sheet.png.",
    )
    parser.add_argument(
        "--timeline", action="store_true",
        help="Print feature timeline sampled every 5s.",
    )
    args = parser.parse_args()

    records_to_run = [args.record] if args.record else RECORDS

    if args.save_crops:
        print("[crop previews]")
        _save_crop_previews(records_to_run)
        print()

    all_results: dict[str, list[dict]] = {}

    for record in records_to_run:
        print(f"[{record}]")
        data = measure_record(record)
        all_results[record] = data
        print(f"  {len(data)} frames measured")
        _print_summary(record, data)

        # ── save JSON ──
        _write_json(record, data)

        # ── validate ──
        _validate(record, data)

        # ── save preview ──
        _save_preview(record, data)

        if args.timeline:
            _print_timeline(record, data)

        if args.contact_sheet:
            _save_contact_sheet(record, data)

    if args.find_cuts:
        _find_cuts(all_results)

    print("\nDone.")


if __name__ == "__main__":
    main()
