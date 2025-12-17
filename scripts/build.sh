#!/bin/bash
# Build standalone executable for current platform

set -e

echo "Building WolfkryptHost..."

# Ensure poetry is installed
if ! command -v poetry &> /dev/null; then
    pip install poetry
fi

# Install dependencies
poetry install

# Build with PyInstaller
poetry run pyinstaller \
    --onefile \
    --windowed \
    --name WolfkryptHost \
    --add-data "keys:keys" \
    src/main.py

echo "Build complete! Executable at: dist/WolfkryptHost"
