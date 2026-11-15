# Sound Mapping Cheat Sheet — Person B

## 1. Musical Scale

All pitches are locked to the **A-minor pentatonic** scale to guarantee every note sounds pleasant regardless of the data.

| Step | Note | Freq (A4=440) |
|------|------|--------------|
| 0 | A3 | 220.00 Hz |
| 1 | C4 | 261.63 Hz |
| 2 | D4 | 293.66 Hz |
| 3 | E4 | 329.63 Hz |
| 4 | G4 | 392.00 Hz |
| 5 | A4 | 440.00 Hz |
| 6 | C5 | 523.25 Hz |
| 7 | D5 | 587.33 Hz |
| 8 | E5 | 659.25 Hz |
| 9 | G5 | 783.99 Hz |
| 10 | A5 | 880.00 Hz |

**Mapping rule:** `note_index = round(column_height * 10)` where `column_height ∈ [0, 1]`  
(0 = bottom of image = lowest note A3; 1 = top of image = highest note A5)

---

## 2. Instrument Assignment by Hue

| Hue range (°) | Category | MIDI program (GM) | Sound character |
|---|---|---|---|
| 0–60, 300–360 | Warm (red/orange/yellow) | 48 — String Ensemble | Rich, sustained strings |
| 61–179 | Neutral (yellow-green) | 11 — Music Box / Vibraphone | Bell-like, gentle |
| 180–260 | Cool (blue/purple) | 89 — Pad 2 (warm) | Soft, evolving pads |
| 261–299 | Violet/magenta | 52 — Choir Aahs | Ethereal, choral |

**Record overrides:**

| Record | Primary instrument | Percussion |
|---|---|---|
| Night Lights | String Ensemble (48) | Closed Hi-hat on motion spikes |
| ISS Lightning | Marimba (13) for base | **Snare (38) + Crash (49) on each flash** |
| Ocean Currents | Pad 2 warm (89) | None — sustained only |
| GEDI Forest Height | Vibraphone (11) | None — melodic only |

---

## 3. Feature → Sound Mapping Rules

### Per-frame globals
| Feature | Maps to |
|---|---|
| `brightness` | Note velocity (volume): `velocity = int(brightness * 100) + 27` → range 27–127 |
| `motion` | Note duration & density: `duration = max(0.1, 0.5 - motion * 2)` s; high motion = short fast notes |
| `edges` | Reverb depth (CC 91): `reverb = int(edges * 127)` |

### Per-column (strip)
| Feature | Maps to |
|---|---|
| `columns[i]` | Whether note plays: play if `> 0.08` threshold |
| `column_heights[i]` | Pitch (pentatonic index above) |
| `column_hues[i]` | Instrument program (table above) |

### Stereo panning
Column index → stereo pan: `pan = (i / n_strips) * 2 - 1` → −1 (hard left) to +1 (hard right)  
Implemented via MIDI CC 10 (Pan): `pan_cc = int((pan + 1) / 2 * 127)`

---

## 4. Then-vs-Now Mode (Night Lights only)

- **Track A** (`night_lights.mp3`): 2012–2023 sequence, strings, normal playback.  
- **Track B** (`then_vs_now.mp3`): difference map — frames where `purple > 0.5` get a **raised 7th** (tritone above root) for dissonance; frames where `brightness` dropped get a **minor 2nd** semitone cluster.
- Both tracks play simultaneously at 60 % / 60 % volume (sum ≤ 100 %).

---

## 5. Tempo & Timing

- Base tempo: **60 BPM** (1 beat per second, matching `fps = 1`).
- Each frame = 1 beat = one "column sweep" event.
- Motion spikes (`motion > 0.05`) subdivide the beat into **eighth notes** for that frame.
- Night Lights loop: silence frames where `brightness == 0.0` (transition frames between years).

---

## 6. Volume Normalisation

Export each track at **−14 LUFS** (streaming standard) using pydub:
```python
from pydub import AudioSegment, effects
track = AudioSegment.from_wav("out.wav")
track = effects.normalize(track)          # peak normalize
track.export("out.mp3", format="mp3", bitrate="192k")
```

---

## 7. Per-Record Tuning Notes

### Night Lights
- Dominant hue ≈ 36° (amber) → String Ensemble throughout.
- Purple signal (`purple` field) present in frames 10–21 → switch to Choir (52) for those frames.
- Motion is very low (≈0.001) → long, sustained notes; sparse rhythm.

### ISS Lightning
- Hue ≈ 60° (yellow) throughout → Marimba base layer.
- Each frame with `brightness > 0.3` = percussion hit; use `motion` to pick drum intensity.
- High motion frames (> 0.03) → Crash cymbal (49); low motion → Hi-hat (42).

### Ocean Currents  
- Hue range 0–280° with real variation → let the pad instrument shift per column.
- `saturation` ≈ 0.8 throughout → keep reverb high (CC 91 = 90).
- `motion` ≈ 0.02–0.04 → gentle, flowing 8th-note rhythm.

### GEDI Forest Height
- Strong `column_heights` variation (0.1–0.9) → rich melodic contour.
- Use Vibraphone for a marimba-forest feel.
- `motion` is very low (static image) → long held notes, fade in/out slowly.
