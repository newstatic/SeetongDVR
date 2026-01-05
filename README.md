# Seetong DVR Web Viewer

A web-based video player for Seetong (天视通) DVR/NVR recordings stored in proprietary TPS format.

## Features

- **TPS Format Parser**: Reverse-engineered support for Seetong's proprietary storage format
- **Web-based Player**: Browser-based H.265/HEVC video playback using WebCodecs API
- **Precise Time Seeking**: Byte-position interpolation algorithm for accurate frame extraction
- **Custom OCR Models**: Trained Tesseract models for OSD timestamp recognition
- **Real-time Streaming**: WebSocket-based video streaming with playback controls

## Architecture

```
┌─────────────┐    WebSocket    ┌─────────────┐    File I/O    ┌─────────────┐
│   Browser   │ ◄────────────► │   Python    │ ◄────────────► │  TPS Files  │
│   (React)   │    H.265 NAL   │   Server    │    Raw Data    │  (SD Card)  │
└─────────────┘                └─────────────┘                └─────────────┘
```

## Requirements

- Python 3.9+
- Chrome 94+ with HEVC flag enabled (`chrome://flags/#enable-platform-hevc`)
- Node.js 18+ (for frontend development)

## Installation

### Backend

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Frontend

```bash
cd web
npm install
npm run build
```

## Usage

### Start the Server

```bash
python server.py --dvr-path /path/to/sd-card
```

The server will start at `http://localhost:8100`.

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dvr-path` | `/Volumes/NO NAME` | Path to DVR storage (SD card) |
| `--host` | `0.0.0.0` | Server bind address |
| `--port` | `8100` | Server port |

## TPS File Format

Seetong DVR uses a proprietary TPS format:

| File | Description |
|------|-------------|
| `TIndex00.tps` | Index file (~32MB) containing segment and frame indices |
| `TRec000000.tps` - `TRecNNNNNN.tps` | Video files (256MB each), H.265 Annex B format |

### Index Structure

- **Segment Index** (offset 0x500): Maps time ranges to TRec files
- **Frame Index** (offset 0x84C0): Maps timestamps to byte positions within files

## Precise Time Algorithm

The system uses byte-position linear interpolation for accurate seeking:

```
VPS_time = start_time + (vps_byte_offset / total_bytes) × duration
```

**Accuracy (100 samples tested):**
- 54.1% exact match (0s error)
- 78.6% within ±1s
- 87.8% within ±2s

## Project Structure

```
SeetongDVR/
├── dvr.py                      # Core TPS index parser
├── tps_storage_lib.py          # Storage library with precise timing
├── server.py                   # Web server (REST + WebSocket)
├── precise_frame_extractor_final.py  # Frame extraction with OCR
├── requirements.txt            # Python dependencies
├── tesseract_train/            # OCR training data and models
│   ├── dvr.traineddata         # Base OCR model
│   └── dvr_line_v2.traineddata # Line recognition model
└── web/                        # React frontend
    ├── src/
    │   ├── components/         # UI components
    │   ├── hooks/              # Custom React hooks
    │   └── stores/             # State management
    └── package.json
```

## API Reference

### REST Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/recordings/dates` | GET | List dates with recordings |
| `/api/v1/recordings` | GET | List recordings for a date |
| `/api/v1/config` | GET/POST | Server configuration |

### WebSocket Protocol

Connect to `ws://host:port/api/v1/stream`

**Commands:**
```json
{"action": "play", "channel": 1, "timestamp": 1766034449}
{"action": "pause"}
{"action": "seek", "timestamp": 1766041804}
{"action": "speed", "rate": 2.0}
```

## OCR Model Training

See [OCR_TRAINING_GUIDE.md](OCR_TRAINING_GUIDE.md) for instructions on training custom Tesseract models for OSD timestamp recognition.

## License

MIT License

## Acknowledgments

- Reverse engineering based on analysis of `tpsrecordLib.dll` from official Seetong tools
- Tesseract OCR for timestamp recognition
- WebCodecs API for browser-based H.265 decoding
