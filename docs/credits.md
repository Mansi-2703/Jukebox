# Credits & Data Sources

## NASA Scientific Visualization Studio (SVS)

All visualisations are courtesy of NASA's Scientific Visualization Studio.  
SVS homepage: https://svs.gsfc.nasa.gov/

| Record | Dataset | SVS Link | License |
|---|---|---|---|
| NL-5110 | Night Lights | Black Marble — Earth at Night 2012–2023 | https://svs.gsfc.nasa.gov/5110 | NASA Open Data |
| LT-5057 | ISS Lightning | ISS Lightning Observations 2017–2023 (LIS) | https://svs.gsfc.nasa.gov/5057 | NASA Open Data |
| OC-3827 | Ocean Currents | Ocean Surface Currents 2007–2008 (ECCO2) | https://svs.gsfc.nasa.gov/3827 | NASA Open Data |
| GH-5048 | GEDI Forest Height | Global Ecosystem Dynamics Investigation (GEDI) | https://svs.gsfc.nasa.gov/5048 | NASA Open Data |

## Instruments

Sound generation uses **FluidSynth** with the **GeneralUser GS** soundfont:
- GeneralUser GS © 2013 S. Christian Collins — free for non-commercial use  
  https://www.schristiancollins.com/generaluser.php

## Libraries & Tools

- **Python** 3.11 — data processing  
- **OpenCV / NumPy** — frame analysis (Person A)  
- **pretty_midi** — MIDI composition (Person B)  
- **FluidSynth + pydub** — audio rendering & normalisation (Person B)  
- **Web Audio API** — in-browser probe tone synthesis (Person C)  
- **ffmpeg** — video frame extraction (Person A)  

## Team

Built for **NASA Space Apps Challenge 2026**.  
Person A — Data & Vision  
Person B — Sound Engine  
Person C — Jukebox UI, Accessibility & Story  

## Licence

Code: MIT Licence  
NASA imagery: US Government public domain (17 U.S.C. § 105)  
Soundfont: GeneralUser GS non-commercial licence
