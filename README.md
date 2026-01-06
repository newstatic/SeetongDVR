# Seetong DVR Web Player

Web-based player for Seetong (天视通) DVR/NVR recordings.

## Quick Install (macOS)

```bash
curl -fsSL https://github.com/newstatic/SeetongDVR/releases/latest/download/install.sh | bash
```

This will:
1. Download the latest release
2. Install to `~/.local/bin/seetong-dvr`
3. Start the player and open your browser

## Manual Download

Download the binary for your platform from [Releases](https://github.com/newstatic/SeetongDVR/releases):

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `seetong-dvr-darwin-arm64` |
| macOS (Intel) | `seetong-dvr-darwin-amd64` |

```bash
chmod +x seetong-dvr-darwin-*
./seetong-dvr-darwin-arm64
```

## Usage

1. Run the binary - browser opens automatically
2. Select your DVR storage path (SD card / USB drive)
3. Browse and play recordings

### Command Line Options

```
-port int      Server port (default 8000)
-path string   DVR base path (optional, can be set via Web UI)
-debug         Enable debug logging
-no-browser    Don't open browser automatically
```

## Features

- Single binary, no dependencies
- Browser-based H.265/HEVC playback (WebCodecs API)
- Audio playback (G.711 u-law)
- Timeline navigation with precise seeking
- Multi-channel support

## Requirements

- macOS 10.15+ (Catalina or later)
- Chrome 94+ / Edge 94+ / Safari 16.4+ with HEVC support

## TPS File Format

Seetong DVR uses proprietary TPS format:

| File | Description |
|------|-------------|
| `TIndex00.tps` | Index file containing segment and frame indices |
| `TRec000000.tps` ~ `TRecNNNNNN.tps` | Video files (256MB each), H.265 Annex B |

## License

MIT License
