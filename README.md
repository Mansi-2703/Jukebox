# Earth Jukebox: Planetary Observation Sonification Archive

**NASA Space Apps 2026** — Planetary Earth Observation Data translated into acoustic spectrograms.  
Each NASA visualization functions as a high-density sonification record. A precision coordinate needle sweeps chronologically from left to right, mapping spectral radiance and spatial features to an A-minor pentatonic acoustic continuum in real time.

---

## Sonification Architecture & Mapping Rules

| Dimension | Observation Telemetry | Acoustic Translation |
|---|---|---|
| Horizontal (X-Axis) | Temporal progression / scanline time | Binaural stereo spatialization (Left → Right ear) |
| Vertical (Y-Axis) | Latitude / altitude coordinates | Pitch register locked to A-minor pentatonic (220 Hz – 880 Hz) |
| Spectral Hue | Thermal & spectral bands | Timbral profile (Warm = strings/horns, Cool = resonant pads) |
| Radiant Luminance | Pixel intensity / canopy reflectance | Velocity & dynamic amplitude |
| Differential Motion | Inter-frame temporal divergence | Transient density & rhythmic subdivision |

---

## Observation Records

| Catalog ID | Dataset | Primary Sensor | Acoustic Role |
|---|---|---|---|
| NL-5110 | Night Lights (2012–2023) | Suomi NPP / VIIRS Day-Night Band | Rhythm & multi-epoch harmonies |
| LT-5057 | ISS Lightning (2017–2023) | ISS Lightning Imaging Sensor (LIS) | Transient percussion & pulse clusters |
| OC-3827 | Ocean Surface Currents (2007–2008) | ECCO2 High-Resolution Synthesis | Continuous evolving textural pads |
| GH-5048 | GEDI Forest Canopy Height | GEDI Spaceborne Lidar Waveform | Canopy pitch profiling & timbre waves |

---

## Mission Features

- **Then vs Now Epoch Comparison** (Night Lights): Concurrently renders 2012 baseline against 2023 epoch data. Where electrification and urban centers expanded, the acoustic texture resolves into richer harmonic overtones; regions experiencing light reduction introduce localized dissonant intervals.
- **Spectral Coordinate Probe**: Touch or hover any geographic coordinate on the spectrogram stage to audit localized vertical column telemetry through a dedicated Web Audio synthesis oscillator. Accessible via mouse, touch, or directional keyboard navigation.
- **Bring Your Own Earth (Dynamic Live Sonification)**: Drop or browse any NASA MP4/WebM Earth observation video into the console. The browser downscales each frame to 640×360, extracts optical radiance, dominant hue, 32 spatial column strips, and motion, and translates them into live polyphonic Web Audio synthesis in real time with an interactive HUD view switcher (`OVERLAY`, `VIDEO`, `SPECTROGRAM`).
- **Scientific Dossiers & Accessibility**: Plain-text acoustic and scientific descriptive telemetry tailored for screen readers, high-contrast HUD design, semantic ARIA live regions, and full keyboard console shortcuts.

---

## System Deployment

Static HTML5/CSS3/ES6 architecture with Web Audio API synthesis — no build pipeline or Node bundler required.

```bash
# Serve locally from the web/ directory
python -m http.server 8080
# Open console in browser: http://localhost:8080
```

---

## Repository Architecture

```
earth-jukebox/
├── README.md
├── data/
│   ├── features/           # Observation telemetry JSON & visual previews (Person A)
│   │   ├── night_lights.json / _preview.jpg
│   │   ├── lightning.json    / _preview.jpg
│   │   ├── ocean.json        / _preview.jpg
│   │   └── gedi.json         / _preview.jpg
│   └── videos/             # Raw NASA SVS video archives (Person A)
├── sound/                  # Compositional engine & mapping cheat sheet (Person B)
│   ├── cheat_sheet.md      # Mathematical tuning & instrument assignment matrix
│   ├── generate.py         # Primary MIDI & audio generation pipeline
│   └── then_vs_now.py      # Differential epoch mix engine
├── audio/                  # Master acoustic exports (.mp3 / .wav) (Person B → C)
├── web/                    # Mission Control Sonification Console (Person C)
│   ├── index.html          # Semantic HTML5 console
│   ├── style.css           # Mission-grade responsive workstation stylesheet
│   ├── app.js              # Audio graph, needle synchronization, & BYO orchestrator
│   ├── features.js         # Client-side video frame optical feature extractor (Person A)
│   ├── synth.js            # Real-time Web Audio polyphonic synthesizer (Person B)
│   └── records.js          # Catalog manifest & metadata schema
└── docs/
    ├── descriptions.md     # Narrative telemetry & accessibility transcripts
    └── credits.md          # NASA SVS attribution & scientific licenses
```

## Console Command Shortcuts

| Key | Operation |
|---|---|
| `Space` | Toggle Playback / Pause |
| `←` `→` | Seek Temporal Position (±5 s) |
| `↑` `↓` | Adjust Master Attenuation (±10 %) |
| `M` | Toggle Audio Mute |
| `P` | Toggle Spectral Coordinate Probe Mode |
| `T` | Toggle Epoch Comparison (Night Lights) |
| `1` – `4` | Mount Observation Records 01–04 |
| `?` / `Esc` | Display / Dismiss Command Reference |

---

## Attribution & Data Sources

Visualizations courtesy of [NASA Scientific Visualization Studio (SVS)](https://svs.gsfc.nasa.gov/).  
Acoustic translation engine powered by Web Audio API & FluidSynth soundfont synthesis.
