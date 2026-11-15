"""
loader.py — Earth Jukebox Audio Sound Engine
Loads and validates features.json files and record configs.

This module is intentionally well-commented so the developer can explain
every validation step clearly during a demo.
"""

import json
import os
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(path: str) -> dict:
    """
    Read a features.json file from *path*, validate it, apply defaults,
    and return the validated dict.

    Raises ValueError with a descriptive message on any validation failure.
    Calls sys.exit(1) if the data/ directory exists but contains no .json files.
    """
    # --- Check for empty data/ directory before even reading the file -------
    # This gives early, friendly feedback when the user has set up the project
    # structure but hasn't placed any data files yet.
    data_dir = os.path.join(os.path.dirname(path), "..", "data")
    data_dir = os.path.normpath(data_dir)
    if os.path.isdir(data_dir):
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
        if not json_files:
            print(
                "No features.json files found in data/. "
                "Expected files: night_lights.json, lightning.json, "
                "ocean_currents.json, gedi_forest.json "
                "(or any features .json)"
            )
            sys.exit(1)

    # --- Read the JSON file --------------------------------------------------
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ValueError(f"Features file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}")

    # --- Top-level structure validation -------------------------------------
    _validate_top_level(data, path)

    # --- Per-frame validation and default application -----------------------
    _validate_and_default_frames(data["frames"])

    return data


def load_config(record_name: str) -> dict:
    """
    Load configs/<record_name>.json relative to this file's location.

    If the config file is missing, return a sensible default configuration
    (C major pentatonic scale, 90 BPM, marimba instrument) so the engine
    always has something to work with.
    """
    # Build a path to configs/ sitting next to src/
    src_dir = os.path.dirname(os.path.abspath(__file__))
    configs_dir = os.path.join(src_dir, "..", "configs")
    config_path = os.path.normpath(
        os.path.join(configs_dir, f"{record_name}.json")
    )

    if not os.path.isfile(config_path):
        # Return a safe default so the engine can still run without a config.
        return _default_config(record_name)

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config {config_path}: {exc}")


def list_data_files(data_dir: str) -> list:
    """
    Return a list of all .json file paths in *data_dir*.

    Exits with a clear message if the directory exists but has no .json files,
    so the user knows exactly what to put there.
    """
    if not os.path.isdir(data_dir):
        raise ValueError(f"Data directory not found: {data_dir}")

    json_files = sorted(
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.endswith(".json")
    )

    if not json_files:
        print(
            "No features.json files found in data/. "
            "Expected files: night_lights.json, lightning.json, "
            "ocean_currents.json, gedi_forest.json "
            "(or any features .json)"
        )
        sys.exit(1)

    return json_files


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_top_level(data: dict, path: str) -> None:
    """
    Validate that the top-level structure contains the required fields.

    Required:
      - "record": non-empty string — identifies which record this data belongs to
      - "frames": non-empty list   — the sequence of visual snapshots to sonify
    """
    # Validate "record" field
    if "record" not in data:
        raise ValueError(f"{path}: missing required field 'record'")
    if not isinstance(data["record"], str) or not data["record"].strip():
        raise ValueError(
            f"{path}: 'record' must be a non-empty string, "
            f"got {data['record']!r}"
        )

    # Validate "frames" field
    if "frames" not in data:
        raise ValueError(f"{path}: missing required field 'frames'")
    if not isinstance(data["frames"], list) or len(data["frames"]) == 0:
        raise ValueError(
            f"{path}: 'frames' must be a non-empty list"
        )


def _validate_and_default_frames(frames: list) -> None:
    """
    Iterate over every frame, validate field ranges, apply defaults, and
    verify that 't' values are strictly increasing.

    Defaults applied here (so downstream code never has to handle missing keys):
      - edges, saturation, motion → 0.0 if absent
      - column_heights            → copy of columns values
      - column_hues               → frame hue repeated for each strip
    """
    prev_t: float | None = None  # used to check strictly-increasing timestamps

    for frame_idx, frame in enumerate(frames):
        label = f"Frame {frame_idx}"  # human-readable prefix for error messages

        # --- Validate and check "t" (timestamp) ----------------------------
        # "t" must be strictly increasing so the render loop can walk frames
        # in time order without surprises.
        if "t" in frame:
            t_val = frame["t"]
            if not isinstance(t_val, (int, float)):
                raise ValueError(f"{label}: 't' must be a number, got {t_val!r}")
            if prev_t is not None and t_val <= prev_t:
                raise ValueError(
                    f"{label}: 't'={t_val} is not strictly greater than "
                    f"previous 't'={prev_t} — timestamps must be increasing"
                )
            prev_t = float(t_val)

        # --- Validate 0–1 scalar fields ------------------------------------
        # brightness, saturation, edges, motion all represent normalized sensor
        # readings where 0 = minimum and 1 = maximum intensity.
        for field in ("brightness", "saturation", "edges", "motion"):
            if field in frame:
                _check_range_01(frame[field], field, label)
            else:
                # Apply default: missing edges/saturation/motion → 0
                # brightness has no default because it's an optional hint field
                if field in ("edges", "saturation", "motion"):
                    frame[field] = 0.0

        # --- Validate hue (0–360) -------------------------------------------
        # Hue is a colour-wheel angle. 0 and 360 are both red.
        if "hue" in frame:
            _check_range(frame["hue"], "hue", 0, 360, label)
        else:
            frame["hue"] = 0.0  # default: red-ish, treated as warm

        # --- Validate columns (list of 0–1 floats) --------------------------
        # Each column represents one vertical strip of the satellite image,
        # with its brightness as a float in [0, 1].
        if "columns" in frame:
            cols = frame["columns"]
            if not isinstance(cols, list):
                raise ValueError(f"{label}: 'columns' must be a list")
            for col_idx, val in enumerate(cols):
                if not isinstance(val, (int, float)):
                    raise ValueError(
                        f"{label}: columns[{col_idx}] must be a number, got {val!r}"
                    )
                if not (0.0 <= val <= 1.0):
                    raise ValueError(
                        f"{label}: columns[{col_idx}]={val} is out of range [0, 1]"
                    )
            n_cols = len(cols)
        else:
            n_cols = 0

        # --- Validate column_heights (optional, same length as columns) -----
        # column_heights override columns as the pitch-mapping source when
        # present (e.g. GEDI canopy-height data). Each value in [0, 1].
        if "column_heights" in frame:
            ch = frame["column_heights"]
            if not isinstance(ch, list):
                raise ValueError(f"{label}: 'column_heights' must be a list")
            if n_cols > 0 and len(ch) != n_cols:
                raise ValueError(
                    f"{label}: 'column_heights' has {len(ch)} items but "
                    f"'columns' has {n_cols} — lengths must match"
                )
            for col_idx, val in enumerate(ch):
                if not isinstance(val, (int, float)):
                    raise ValueError(
                        f"{label}: column_heights[{col_idx}] must be a number, "
                        f"got {val!r}"
                    )
                if not (0.0 <= val <= 1.0):
                    raise ValueError(
                        f"{label}: column_heights[{col_idx}]={val} is out of range [0, 1]"
                    )
        else:
            # Default: use columns values as heights when column_heights absent.
            # This means brightness drives both volume AND pitch if no height
            # channel is provided.
            frame["column_heights"] = list(frame.get("columns", []))

        # --- Validate column_hues (optional, same length as columns) --------
        # Per-strip hue overrides the frame-level hue for instrument selection.
        # Values in [0, 360].
        if "column_hues" in frame:
            chu = frame["column_hues"]
            if not isinstance(chu, list):
                raise ValueError(f"{label}: 'column_hues' must be a list")
            if n_cols > 0 and len(chu) != n_cols:
                raise ValueError(
                    f"{label}: 'column_hues' has {len(chu)} items but "
                    f"'columns' has {n_cols} — lengths must match"
                )
            for col_idx, val in enumerate(chu):
                if not isinstance(val, (int, float)):
                    raise ValueError(
                        f"{label}: column_hues[{col_idx}] must be a number, "
                        f"got {val!r}"
                    )
                if not (0.0 <= val <= 360.0):
                    raise ValueError(
                        f"{label}: column_hues[{col_idx}]={val} is out of range [0, 360]"
                    )
        else:
            # Default: use the frame-level hue for every strip.
            # This gives a consistent instrument colour when no per-strip hue
            # data is available.
            frame["column_hues"] = [frame["hue"]] * n_cols

        # --- Cross-check that all three column arrays agree in length -------
        # The render loop zips them together, so a mismatch would silently
        # truncate data. Catch it here with a clear message.
        lengths = {
            "columns": len(frame.get("columns", [])),
            "column_heights": len(frame.get("column_heights", [])),
            "column_hues": len(frame.get("column_hues", [])),
        }
        unique_lengths = set(lengths.values())
        if len(unique_lengths) > 1:
            details = ", ".join(f"{k}={v}" for k, v in lengths.items())
            raise ValueError(
                f"{label}: columns/column_heights/column_hues must all have "
                f"the same length. Got: {details}"
            )


def _check_range_01(value: Any, field: str, label: str) -> None:
    """Assert that *value* is a number in [0.0, 1.0]."""
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"{label}: {field}={value!r} must be a number"
        )
    if not (0.0 <= value <= 1.0):
        raise ValueError(
            f"{label}: {field}={value} is out of range [0, 1]"
        )


def _check_range(value: Any, field: str, lo: float, hi: float, label: str) -> None:
    """Assert that *value* is a number in [lo, hi]."""
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"{label}: {field}={value!r} must be a number"
        )
    if not (lo <= value <= hi):
        raise ValueError(
            f"{label}: {field}={value} is out of range [{lo}, {hi}]"
        )


def _default_config(record_name: str) -> dict:
    """
    Return a minimal but functional config when no config file is found.

    C major pentatonic at 90 BPM with marimba is a safe, pleasing default
    that works for any satellite dataset without sounding wrong.
    """
    return {
        "record": record_name,
        "scale": "C_major_pentatonic",
        "tempo_bpm": 90,
        "instrument": "marimba",         # MIDI program 12
        "pad_instrument": "strings",     # MIDI program 48
        "mapping_weights": {
            "brightness_to_velocity": 1.0,
            "motion_to_note_density": 1.0,
            "edges_to_accent": 0.5,
            "saturation_to_pad_volume": 0.8,
        },
        "max_simultaneous_notes": 4,
        "reverb_amount": 0.25,
        "description": "Default config — C major pentatonic, marimba, 90 BPM",
    }
