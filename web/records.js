// records.js — Earth Jukebox record catalogue

const RECORDS = [
  {
    id: "night_lights",
    title: "Night Lights",
    subtitle: "Anthropogenic Light Dynamics (2012–2023)",
    tag: "NL-5110",
    catalog: "NASA SVS-5110",
    instrument: "Suomi NPP / VIIRS Day-Night Band",
    soundRole: "Rhythm & Multi-epoch Harmonies",
    image: "data/features/night_lights_preview.jpg",
    video: "data/videos/night_lights.mp4",
    audio: "audio/night_lights.mp3",
    features: "data/features/night_lights.json",
    color: "#d97706",
    description:
      "Global nocturnal radiance composite observing urbanization and electrification trends from 2012 through 2023. Regional luminance expands across developmental corridors, followed by differential change indexing: positive growth yields tonal enrichment, while luminance decline introduces harmonic tension.",
    soundDescription:
      "Horizontal axis corresponds to temporal progression and binaural panning (left-to-right). Vertical latitude maps to pentatonic pitch register; radiant intensity drives amplitude and harmonic density. Urban growth regimes introduce enriched intervals, whereas localized decline modulates dissonance.",
    thenVsNow: true,
    thenVsNowAudio: "audio/then_vs_now.mp3",
  },
  {
    id: "lightning",
    title: "ISS Lightning",
    subtitle: "Atmospheric Optical Transient Mapping (2017–2023)",
    tag: "LT-5057",
    catalog: "NASA SVS-5057",
    instrument: "ISS Lightning Imaging Sensor (LIS)",
    soundRole: "Transient Percussion & Pulse Clusters",
    image: "data/features/lightning_preview.jpg",
    video: "data/videos/lightning.mp4",
    audio: "audio/lightning.mp3",
    features: "data/features/lightning.json",
    color: "#eab308",
    description:
      "Orbital observation of lightning discharge frequency captured from the International Space Station. Accumulated flash centroids delineate tropical convective chimney zones and mid-latitude storm tracks over a six-year observation window.",
    soundDescription:
      "Left-to-right sweep translates geographic orbital time and stereo spatialization. Altitude dictates note pitch, optical radiant output scales velocity, and flash density drives rhythmic subdivision and transient percussive events.",
    thenVsNow: false,
  },
  {
    id: "ocean",
    title: "Ocean Currents",
    subtitle: "Global Sea-Surface Kinetic Circulation (2007–2008)",
    tag: "OC-3827",
    catalog: "NASA SVS-3827",
    instrument: "ECCO2 High-Resolution Synthesis",
    soundRole: "Continuous Evolving Textural Pads",
    image: "data/features/ocean_preview.jpg",
    video: "data/videos/ocean.webm",
    audio: "audio/ocean.mp3",
    features: "data/features/ocean.json",
    color: "#0d9488",
    description:
      "High-resolution numerical simulation of planetary ocean currents and mesoscale eddies from the ECCO2 project. Streamlines delineate major boundary currents and sub-polar gyres, color-coded by thermal energy distribution.",
    soundDescription:
      "Left-to-right trajectory governs acoustic sweep and stereo width. Streamline velocity and thermal hue determine timbre: warm low-latitude currents voice higher overtone spectra, while deep arctic flows resolve into low, resonance-filtered ambient pads.",
    thenVsNow: false,
  },
  {
    id: "gedi",
    title: "GEDI Forest Canopy",
    subtitle: "Global Vegetation Structure & Biomass Profile",
    tag: "GH-5048",
    catalog: "NASA SVS-5048",
    instrument: "GEDI Spaceborne Lidar Waveform",
    soundRole: "Canopy Pitch Profiling & Timbre Waves",
    image: "data/features/gedi_preview.jpg",
    video: "data/videos/gedi.mp4",
    audio: "audio/gedi.mp3",
    features: "data/features/gedi.json",
    color: "#16a34a",
    description:
      "Spaceborne lidar altimetry survey profiling terrestrial vegetation height and carbon storage architecture. Continuous lidar waveforms resolve vertical structure across equatorial rainforests and boreal transition zones.",
    soundDescription:
      "Scanline latitude and height profiles translate directly to fundamental frequencies. Tall continuous canopies in equatorial basins trigger elevated harmonic motifs, transitioning to lower foundational fundamentals across sparse savannahs and shrublands.",
    thenVsNow: false,
  },
];

if (typeof module !== "undefined") module.exports = RECORDS;
