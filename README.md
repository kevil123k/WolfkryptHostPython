# WolfkryptHost (Python)

A cross-platform screen mirroring host application for the Wolfkrypt Android app.

## Features

- 🔌 **USB AOA 2.0** - Direct USB connection to Android device
- 🔐 **Ed25519 Authentication** - Secure cryptographic pairing
- 🎥 **H.264 Video Decoding** - Low-latency video playback
- 🔊 **AAC Audio Decoding** - Synchronized audio output
- 🖥️ **Cross-Platform** - Windows, Linux, macOS

## Quick Start

### Prerequisites

- Python 3.11+
- Poetry (package manager)

### Installation

```bash
# Clone the repository
git clone https://github.com/kevil123k/WolfkryptHostPython.git
cd WolfkryptHostPython

# Install dependencies
pip install poetry
poetry install

# Run the application
poetry run wolfkrypt-host
```

### Build Standalone Executable

```bash
# Build .exe (Windows) or binary (Linux/macOS)
poetry run pyinstaller --onefile --windowed src/main.py --name WolfkryptHost

# Output: dist/WolfkryptHost.exe (or dist/WolfkryptHost)
```

## Project Structure

```
src/
├── core/           # USB protocol, authentication, packet parsing
│   ├── aoa.py      # AOA 2.0 USB host implementation
│   ├── auth.py     # Ed25519 authentication
│   └── protocol.py # Message serialization/parsing
├── media/          # Video/audio decoding (FFmpeg via PyAV)
│   ├── video.py    # H.264 decoder
│   └── audio.py    # AAC decoder
├── render/         # Output rendering
│   ├── window.py   # Video display window
│   └── audio_output.py  # Audio playback
├── ui/             # Qt6 user interface
│   └── main_window.py
└── main.py         # Application entry point
```

## Development

```bash
# Run tests
poetry run pytest

# Format code
poetry run black src tests
poetry run isort src tests

# Lint
poetry run flake8 src tests
```

## License

MIT License
