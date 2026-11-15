# 🌍 Earth Jukebox — The Planet's Record Collection

**NASA Space Apps Hackathon Project**

Earth Jukebox turns NASA satellite imagery and remote-sensing data into music.
Each dataset becomes a "record": pixel columns are scanned left-to-right like a
needle on vinyl, translating brightness, colour, motion, and structure into melody,
rhythm, harmony, and stereo position.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Directory Structure](#directory-structure)
3. [Installation](#installation)
4. [Data Format](#data-format)
5. [CLI Usage](#cli-usage)
6. [Output Files](#output-files)
7. [Backends](#backends)
8. [Records](#records)
9. [Self-Check](#self-check)
10. [Mapping Reference](#mapping-reference)

---

## Project Overview

The sound engine (`earth_jukebox_audio/`) is responsible for:

- Loading pre-extracted feature JSON files from `data/`
- Applying per-record musical config from `configs/`
- Rendering MIDI note events via `src/mapping.py` and `src/scales.py`
- Synthesising audio (WAV) and encoding to MP3 via `render.py`
- Supporting a **Then vs Now** comparison mode via `then_vs_now.py`

The engine does **not** process raw imagery — that is handled by a separate
image-analysis pipeline which deposits `features.json` files into `data/`.

---

## Directory Structure

```
earth_jukebox_audio/
├── data/               ← Drop features.json files here (not included)
├── configs/            ← Per-record musical config (JSON)
│   ├── night_lights.json
│   ├── lightning.json
│   ├── ocean_currents.json
│   └── gedi_forest.json
├── src/                ← Sound engine source modules
│   ├── __init__.py
│   ├── loader.py       ← Validates and normalises features.json
│   ├── scales.py       ← Scale definitions and pitch quantization
│   └── mapping.py      ← Feature → NoteEvent conversion
├── out/                ← Rendered WAV/MP3 output (auto-created)
├── render.py           ← Main CLI entry point
├── then_vs_now.py      ← Then-vs-Now comparison renderer
├── MAPPING.md          ← Full feature-to-sound mapping reference
├── requirements.txt    ← Python dependencies
└── README.md           ← This file
```

---

## Installation

### 1. Python dependencies

```bash
pip install -r requirements.txt
```

Requires Python 3.9+.

### 2. FluidSynth (optional — for high-quality MIDI synthesis)

FluidSynth is a software MIDI synthesiser that produces significantly better audio
quality than the built-in NumPy backend.  It is **optional**; the engine falls back
to NumPy synthesis automatically if FluidSynth is not found.

| Platform | Installation |
|----------|-------------|
| macOS | `brew install fluidsynth` |
| Ubuntu / Debian | `sudo apt install fluidsynth` |
| Windows | Download installer from [FluidSynth releases](https://github.com/FluidSynth/fluidsynth/releases) |

After installing FluidSynth, install the Python binding:

```bash
pip install pyfluidsynth
```

You will also need a General MIDI soundfont (`.sf2`).  A free one:

```bash
# Ubuntu
sudo apt install fluid-soundfont-gm
# Or download: https://member.keymusician.com/Member/FluidR3_GM/index.html
```

Pass the soundfont path with `--soundfont` (see CLI Usage below).

---

## Data Format

Place pre-extracted feature files in `data/`.  Each file must be named
`<record_name>.json` or `<record_name>_<year>.json` for Then vs Now mode.

### features.json schema

```jsonc
{
  "record": "night_lights",          // string — matches a configs/*.json record name
  "frames": [
    {
      "t": 0.0,                      // float — frame timestamp in seconds
      "columns": [0.1, 0.4, 0.8],   // float[] — per-strip brightness [0–1]
      "column_heights": [0.2, 0.5, 0.9], // float[] — per-strip height/depth [0–1]
      "column_hues": [210, 30, 60],  // float[] — per-strip dominant hue [0–360]
      "brightness": 0.5,             // float — global mean brightness [0–1]
      "hue": 120.0,                  // float — global dominant hue [0–360]
      "saturation": 0.6,             // float — global mean saturation [0–1]
      "edges": 0.3,                  // float — edge density [0–1]
      "motion": 0.2                  // float — inter-frame motion [0–1]
    }
    // ... more frames
  ]
}
```

**All per-strip arrays must be the same length.**  
Missing optional fields (`column_heights`, `column_hues`, `edges`, `motion`) are
defaulted to safe values by `src/loader.py`.

---

## CLI Usage

### Render a single record

```bash
python render.py --input data/night_lights.json --out out/night_lights.mp3
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | *(required)* | Path to features.json |
| `--out` | `out/<record>.mp3` | Output file path (WAV or MP3) |
| `--config` | `configs/<record>.json` | Override record config |
| `--backend` | `auto` | Synthesis backend: `auto`, `fluidsynth`, `numpy` |
| `--soundfont` | system default | Path to `.sf2` soundfont (FluidSynth only) |
| `--scale` | from config | Override scale (e.g. `C_minor_pentatonic`) |
| `--tempo` | from config | Override tempo in BPM |
| `--wav` | false | Also save a WAV alongside the MP3 |

### Render all records in data/

```bash
python render.py --all
```

Scans `data/` for all `*.json` files (excluding `*_20??` year-stamped files) and
renders each one using its matching config.

### Then vs Now comparison

```bash
python then_vs_now.py \
  --a data/night_lights_2012.json \
  --b data/night_lights_2023.json \
  --out out/then_vs_now.mp3
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--a` | *(required)* | "Then" features file (earlier year) |
| `--b` | *(required)* | "Now" features file (later year) |
| `--out` | `out/then_vs_now.mp3` | Output file |
| `--backend` | `auto` | Synthesis backend |
| `--soundfont` | system default | Soundfont for FluidSynth |

The left stereo channel plays the earlier ("Then") data; the right channel plays the
later ("Now") data.  Growth regions gain extra harmony; strongly changed regions gain
a quiet dissonant accent.  See [MAPPING.md](MAPPING.md#then-vs-now-mode) for full
details.

---

## Output Files

| Extension | Description |
|-----------|-------------|
| `.wav` | Uncompressed 44.1 kHz 16-bit stereo (always produced internally) |
| `.mp3` | 192 kbps stereo MP3 (produced by default via pydub + ffmpeg) |

If ffmpeg is not installed, the engine saves WAV only and prints a warning.

Output files are written to the `out/` directory (created automatically).

---

## Backends

| Backend | Quality | Requirement |
|---------|---------|-------------|
| `auto` | Best available | Tries FluidSynth first, falls back to NumPy |
| `fluidsynth` | High (sample-based) | FluidSynth + pyfluidsynth + a `.sf2` soundfont |
| `numpy` | Basic (additive synthesis) | NumPy only (always available) |

The NumPy backend uses simple sine/triangle oscillators and is suitable for
prototyping and demos.  For presentation-quality audio, install FluidSynth.

---

## Records

| Record | Dataset | Instrument | Tempo | Character |
|--------|---------|------------|-------|-----------|
| `night_lights` | VIIRS Night-time Lights | Marimba + Strings pad | 90 BPM | Soft mallet chords, density follows city brightness |
| `lightning` | LIS Lightning Imagery | Xylophone | 120 BPM | Sparse dry hits triggered by brightness/motion spikes |
| `ocean_currents` | OSCAR Ocean Currents | Warm Pad + Choir Pad | 60 BPM | Slow swelling tones, saturation drives harmonic richness |
| `gedi_forest` | GEDI Canopy Height | Marimba + Warm Pad | 75 BPM | Canopy height → pitch; tall trees = high notes |

---

## Self-Check

Verify that the installation and config files are correct without rendering audio:

```bash
python render.py --check
```

This will:

- Import all source modules and report any missing dependencies
- Validate all `configs/*.json` files against the expected schema
- Check that the FluidSynth backend is available (if installed)
- Print a summary of available records and scales

No audio files are written during `--check`.

---

## Mapping Reference

See [MAPPING.md](MAPPING.md) for the complete feature-to-sound mapping specification,
including scale quantization details, hue-to-instrument mapping, the Then vs Now
algorithm, and per-record personality descriptions.

---

## Licence

NASA Open Data.  Code: MIT.
