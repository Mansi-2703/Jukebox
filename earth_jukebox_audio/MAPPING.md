# Earth Jukebox — Feature-to-Sound Mapping Reference

This document is the authoritative guide to how NASA satellite imagery features are
translated into musical parameters by the Earth Jukebox sound engine.

---

## 1. The Core Metaphor: A Record Played Across a Globe

Each NASA dataset is treated like a vinyl record. The image is scanned in vertical
**strips** from left to right. Left is the beginning of the track; right is the end.
As the needle moves rightward it both advances time and pans the stereo field from
left to right, grounding the listener in physical geography.

---

## 2. Spatial Axes

| Image axis | Musical meaning |
|------------|-----------------|
| **Left → Right** (columns) | Time progression + stereo pan (−1 → +1) |
| **Top → Bottom** (rows within a strip) | Averaged into a single strip descriptor |

Each vertical strip of pixels becomes one "frame" of audio, lasting a duration
proportional to the global `tempo_bpm` setting.

---

## 3. Visual Feature → Sound Mapping

### 3.1 Column Heights → Pitch

**Primary source:** `column_heights` — a per-strip floating-point value (0–1)
representing the dominant vertical feature (canopy height for GEDI, brightness
centroid for optical data, etc.).

**Fallback:** If `column_heights` is absent, `brightness` is used instead.

**Mapping:**

```
value ∈ [0, 1]  →  MIDI note ∈ scale[lowest_octave … highest_octave]
  0.0 → lowest note in the configured octave range (default: octave 3)
  1.0 → highest note in the configured octave range (default: octave 6)
```

The index into the scale array is computed as:

```python
idx = round(value * (len(scale_notes) - 1))
pitch = scale_notes[idx]
```

This guarantees that every emitted note is a member of the active scale — never an
out-of-key pitch.

---

### 3.2 Column Brightness → Velocity / Volume

Each strip's raw `brightness` value (0–1) is multiplied by 127 to produce a MIDI
velocity. A further scale factor is applied from the record config's
`mapping_weights["brightness_to_velocity"]`:

```
velocity = int(strip_brightness × global_brightness × 127
               × brightness_to_velocity_weight)
           clamped to [1, 127]
```

Bright regions produce loud notes; dark regions produce quiet or absent notes
(strips below the 0.05 silence threshold are skipped entirely).

---

### 3.3 Hue → Instrument Family

The dominant **hue** of each strip (HSV hue angle, 0–360°) selects an instrument
family, capturing the emotional colour of the data:

| Hue range | Colour impression | Instrument family | Rationale |
|-----------|-------------------|-------------------|-----------|
| 0–60°, 300–360° | Warm (red, orange, yellow, magenta) | `strings_bells` | City lights, fire, desert warmth — bright and resonant |
| 180–270° | Cool (cyan, blue, indigo) | `soft_pad` | Ocean, ice, deep forest — spacious and atmospheric |
| 61–179°, 271–299° | Neutral / transitional | `blend` | Mixed terrain — marimba or blended timbre |

The final MIDI program number is resolved by cross-referencing the instrument family
against the record config's `instrument` and `pad_instrument` fields:

```
strings_bells → GM program 48 (Strings) or 9 (Glockenspiel)
soft_pad      → GM program 89 (Pad 2: warm)
blend         → GM program 12 (Marimba)  ← default fallback
```

---

### 3.4 Saturation → Pad Layer Volume

`saturation` (0–1) drives the volume of any background **pad** layer.  When
`saturation_to_pad_volume` weight > 0 in the record config, a sustained chord note
is added one octave below the melody note, with velocity proportional to saturation.

High saturation = vivid colours = rich harmonic accompaniment.  
Low saturation = greyscale = bare melodic line, no pad.

---

### 3.5 Motion → Rhythm Density

`motion` (0–1) is derived from inter-frame optical flow or change detection between
consecutive data snapshots.

```
motion_factor = 0.3 + motion × 0.7       # range [0.3, 1.0]
active_strips = max(1, int(N × motion_factor))
```

Where *N* is `max_simultaneous_notes` from the record config.  
Still regions → fewer simultaneous notes → sparse, meditative texture.  
Dynamic regions → more simultaneous notes → busy, rhythmic texture.

The `motion_to_note_density` weight in the config scales this further:

```
effective_motion = motion × motion_to_note_density_weight
```

---

### 3.6 Edges → Accent / Attack

`edges` (0–1) measures the density of detected boundaries (shorelines, cloud edges,
city borders).  When `edges_to_accent` weight > 0:

- Notes in high-edge strips receive a velocity boost (+15, clamped to 127).
- Note attack is sharpened (shorter MIDI note-on ramp).
- This produces a crisp, percussive accent emphasising structural features in the data.

---

## 4. Scale Quantization

**Default scale:** C major pentatonic  
**Available scales:** C major pentatonic, C minor pentatonic, C major, C blues,
A minor pentatonic (see `src/scales.py`).

All emitted pitches are snapped to the nearest member of the active scale.  This
keeps every note consonant regardless of the raw data value.  The C major pentatonic
(C D E G A) was chosen as the default because it has no semitone intervals and
therefore produces no dissonant clashes even when many strips sound simultaneously.

Octave range defaults to octaves 3–6 (MIDI 48–93), giving a comfortable four-octave
melodic range centred in the mid-register.

---

## 5. Polyphony Cap

A maximum of `max_simultaneous_notes` strips (default: 4) are sounded per frame.
Selection is by descending brightness — the brightest strips win.  Strips below the
silence threshold (0.05) are excluded before selection.

This keeps the texture from becoming a wall of sound and mirrors the idea that only
the most *luminous* parts of the globe speak at any moment.

---

## 6. Reverb

Each record applies a gentle convolution reverb controlled by `reverb_amount` (0–1).

| Record | Reverb amount | Character |
|--------|---------------|-----------|
| Night Lights | 0.25 | Light hall — warmth without muddiness |
| Lightning | 0.10 | Almost dry — snappy, percussive |
| Ocean Currents | 0.50 | Deep cathedral — washes blend together |
| GEDI Forest | 0.30 | Medium room — natural, earthy |

---

## 7. Per-Record Personalities

### 🌃 Night Lights
- **Instrument:** Marimba (GM 12) — soft mallets, decaying tones  
- **Pad:** Strings (GM 48) underneath, activated by saturation  
- **Tempo:** 90 BPM — moderate, contemplative  
- **Character:** City clusters glow as dense mallet chords; rural darkness is silence;
  the string pad swells when colours are vivid (neon, sodium orange)
- **Special rule:** Chord pad note (one octave below melody) added when saturation > 0.4

### ⚡ Lightning
- **Instrument:** Xylophone (GM 13) — bright, attack-forward  
- **Pad:** None  
- **Tempo:** 120 BPM — fast, event-driven  
- **Character:** Notes only fire when brightness > 0.3 OR motion > 0.3; dead
  regions produce silence; a strike sounds as a sharp, dry click panned to its
  geographic column
- **Special rule:** Sparse trigger gate — many frames are completely silent

### 🌊 Ocean Currents
- **Instrument:** Pad Warm (GM 89) — slow attack, long decay  
- **Pad:** Pad Choir (GM 91) — additional harmonic layer  
- **Tempo:** 60 BPM — slow, breath-like  
- **Character:** Swelling tones that overlap like waves; saturation drives the full
  harmonic texture; fast-moving current regions are silenced (motion > 0.7) so only
  stable gyres produce sustained sound
- **Special rule:** Duration × 3; velocity reduced 30%; skipped when motion > 0.7

### 🌲 GEDI Forest
- **Instrument:** Marimba (GM 12) — melodic clarity  
- **Pad:** Pad Warm (GM 89) — earthy undertone  
- **Tempo:** 75 BPM — unhurried, organic  
- **Character:** Canopy height (`column_heights`) drives pitch directly — tall trees
  sound as high notes, bare ground as low notes; a soft pad layer activates over
  dense canopy (saturation > 0.5)
- **Special rule:** `column_heights` is the primary pitch source (not brightness fallback)

---

## 8. Then vs Now Mode

`then_vs_now.py` loads **two** features files from different years (e.g. 2012 and
2023) and renders them side by side into a single stereo track:

| Channel | Year | Pan |
|---------|------|-----|
| Left | "Then" (earlier year, e.g. 2012) | −1.0 |
| Right | "Now" (later year, e.g. 2023) | +1.0 |

**Change detection logic:**

| Condition | Musical response |
|-----------|-----------------|
| `now_height > then_height × 1.1` (growth) | Now side gains an extra harmony note → richer chord |
| `abs(now_height - then_height) > 0.4` (strong difference) | A quiet, slightly dissonant accent note (minor 2nd above) is added to the Now channel |
| Regions unchanged | Both channels play identical notes (mono-centre) |

This makes deforestation audible as a thinning of the right channel and sparse
dissonant accents where change is dramatic.

---

## 9. Glossary

| Term | Definition |
|------|-----------|
| `columns` | Per-strip brightness array (one value per vertical strip) |
| `column_heights` | Per-strip height/depth value (canopy, elevation, etc.) |
| `column_hues` | Per-strip dominant hue (degrees, 0–360) |
| `brightness` | Global mean brightness of the frame |
| `hue` | Global dominant hue of the frame |
| `saturation` | Global mean saturation of the frame |
| `edges` | Edge-detection density [0–1] |
| `motion` | Inter-frame change magnitude [0–1] |
| `t` | Frame timestamp in seconds from track start |
| `NoteEvent` | Dataclass: time, duration, pitch, velocity, pan, instrument, channel |
