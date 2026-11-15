# 🌍 Earth Jukebox — Web Interface Guide

## Overview
The web interface allows users to upload their own videos and convert them into music in real-time using the "Bring Your Own Earth" mode.

## Files

- **`features.js`** — Extracts visual features from video frames (Person A's work)
- **`synth.js`** — Web Audio API synthesizer that converts features to audio (Person B's work)
- **`test_features.html`** — Complete pipeline: video → features → audio
- **`synth-demo.html`** — Standalone demo for testing synth.js with pre-computed JSON files
- **`index.html`** — Main jukebox page (Person C's work)

## How to Use test_features.html

### 1. Start a Local Server
```bash
cd c:\NASA\Jukebox\web
python -m http.server 8000
```

### 2. Open in Browser
Navigate to: `http://localhost:8000/test_features.html`

### 3. Upload a Video
- **Drag & drop** a video file onto the drop zone, OR
- **Click** the drop zone to browse for a file

Supported formats: MP4, WebM, MOV, or any format your browser can play

### 4. Wait for Feature Extraction
The page will:
- Display a progress bar (0-100%)
- Show the first frame's features as JSON
- Display a summary table with min/mean/max values for all features

### 5. Generate Audio
Once extraction is complete, a green section appears with three buttons:

#### ▶ Generate & Play Audio
- Initializes the Web Audio API
- Plays the extracted features as music at 1 frame per second
- Shows current playback progress
- Audio is generated in real-time (no file output)

#### ■ Stop Audio
- Stops audio playback immediately
- Cleans up all active audio nodes

#### 💾 Download JSON
- Downloads the extracted features as a JSON file
- Format matches the Python pipeline output (`data/features/*.json`)
- Can be used with `synth-demo.html` or the Python renderer

## How to Use synth-demo.html

### 1. Start Local Server (same as above)

### 2. Open in Browser
Navigate to: `http://localhost:8000/synth-demo.html`

### 3. Load a Pre-Computed Features JSON
Click "Choose File" and select one of:
- `data/features/night_lights.json`
- `data/features/lightning.json`
- `data/features/ocean.json`
- `data/features/gedi.json`

Or load a JSON file downloaded from `test_features.html`

### 4. Configure Options
Adjust synthesis parameters:
- **Oscillator Type**: Wave shape (sine/triangle/square/auto)
- **Scale**: Musical scale (C major pentatonic, etc.)
- **Brightness Threshold**: Minimum strip brightness to play
- **Motion Gain**: Rescale raw motion values (1-50×)
- **Max Voices**: Simultaneous note limit (1-8)
- **Master Volume**: Overall output level (0-1)

### 5. Play
Click **▶ Play** to start audio generation at 1 frame per second

The strips visualizer shows which columns are active (colored bars)

## Pipeline Flow

```
User Video (.mp4, .webm, etc.)
    ↓
[features.js] Extracts visual features frame-by-frame
    ↓
Feature Objects (brightness, hue, saturation, columns, etc.)
    ↓
[synth.js] Converts features to musical notes
    ↓
Web Audio API → Speakers (real-time audio)
```

## Feature Object Shape

Each frame produces:
```javascript
{
  brightness: 0-1,       // overall brightness
  hue: 0-360,            // dominant color hue (degrees)
  saturation: 0-1,       // color saturation
  edges: 0-1,            // edge detection intensity
  motion: 0-1,           // optical flow / change
  columns: [32],         // per-strip brightness (0-1)
  column_heights: [32],  // per-strip vertical position (0-1)
  column_hues: [32],     // per-strip hue (0-360)
  t: number              // timestamp in seconds
}
```

## Mapping Rules (synth.js)

1. **Hue → Pitch**: Color mapped linearly to musical scale
2. **Height → Pitch Offset**: Vertical position shifts notes up/down
3. **Brightness → Volume**: Louder = brighter
4. **Strip Position → Pan**: Left strip = left speaker, right strip = right speaker
5. **Motion → Rhythm**: High motion = short percussive notes, low motion = long sustained notes
6. **Saturation + Hue → Timbre**: Auto mode picks oscillator type from color

## Troubleshooting

### No Audio Output
- **Solution**: Make sure you clicked "Generate & Play Audio" (browser autoplay policy requires user gesture)
- Check your browser's volume/mute settings
- Open browser console (F12) to check for errors

### Video Won't Load
- **Solution**: Try a different video format (MP4 with H.264 codec is most compatible)
- Check the browser console for codec errors
- Some browsers don't support certain video formats

### Feature Extraction is Slow
- **Solution**: This is normal for long videos
- The video is downscaled to 640px width for performance
- Each frame takes ~10-50ms to process

### Audio Sounds Like Noise
- **Solution**: Raw video data might have very low brightness/saturation
- Try adjusting the "Brightness Threshold" slider to filter out dim strips
- Increase "Max Voices" to 2-3 for richer sound
- Switch to "triangle" or "sine" oscillator type for smoother timbre

## Technical Notes

### Browser Compatibility
- **Chrome/Edge**: Full support ✅
- **Firefox**: Full support ✅
- **Safari**: Mostly supported (older versions may have Web Audio issues)

### Performance
- Feature extraction: ~10-50ms per frame (depends on video resolution)
- Audio synthesis: Real-time with negligible CPU usage
- Memory: ~1-2 MB per minute of video for feature storage

### ES Modules
Both files use ES6 module syntax (`import`/`export`), which requires:
- A local HTTP server (not `file://` protocol)
- Modern browser with ES6 support

### Audio Context State
The AudioContext is created once per page and persists until page reload. If you encounter audio glitches after long sessions, refresh the page.

## Credits

- **Person A**: Visual feature extraction (`features.js`)
- **Person B**: Audio synthesis engine (`synth.js`)
- **Python Pipeline**: Original Earth Jukebox sound engine (reference implementation)

## License

Part of the Earth Jukebox project for NASA Space Apps Hackathon.
