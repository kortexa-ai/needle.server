#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")"

# The upstream runner and weights, pinned to one Hugging Face revision so an
# upstream push cannot change what this service runs.
HF_REPO="Cactus-Compute/needle3"
HF_REVISION="9da75122d4ca11aa4a667281c9c8ba38a7eed679"
WEIGHTS_SHA256="c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38"

OS="$(uname -s)"
ARCH="$(uname -m)"
echo "Detected OS: $OS"
echo "Detected Arch: $ARCH"

case "$OS-$ARCH" in
    Darwin-arm64)
        PLATFORM="macos-arm64"
        RUNNER_SHA256="4e308fc8da852fadeb19f47360ae29f17976863e335234fa129a7b3e8e172fba" ;;
    Linux-x86_64)
        PLATFORM="linux-x86_64"
        RUNNER_SHA256="e0510560249e945279801ec4386466a1f813caa9fe9f0137cf5fdd3859e03d57" ;;
    Linux-aarch64|Linux-arm64)
        PLATFORM="linux-arm64"
        RUNNER_SHA256="5dd87478b4719a9a38a6659a70c451e96e84356132c6613eb64aee160c2c19ab" ;;
    *)
        echo "Unsupported platform: $OS $ARCH" >&2
        exit 1 ;;
esac

if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed."
    echo ""
    echo "To install uv, run one of the following:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "  brew install uv"
    echo ""
    echo "For more options, visit: https://docs.astral.sh/uv/installation/"
    exit 1
fi

sha256_of() {
    if command -v sha256sum &> /dev/null; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# fetch <path in the HF repo> <destination> <expected sha256>
fetch() {
    local source="$1" dest="$2" expected="$3"
    if [[ -f "$dest" && "$(sha256_of "$dest")" == "$expected" ]]; then
        echo "Up to date: $dest"
        return
    fi
    echo "Fetching $source..."
    curl -fL --retry 3 -o "$dest.part" "https://huggingface.co/$HF_REPO/resolve/$HF_REVISION/$source"
    local actual
    actual="$(sha256_of "$dest.part")"
    if [[ "$actual" != "$expected" ]]; then
        rm -f "$dest.part"
        echo "Checksum mismatch for $source: expected $expected, got $actual" >&2
        exit 1
    fi
    mv "$dest.part" "$dest"
}

echo "Installing Python dependencies..."
uv sync

mkdir -p engine
fetch "$PLATFORM/needle" engine/needle "$RUNNER_SHA256"
chmod +x engine/needle
fetch "needle3.cact" engine/needle3.cact "$WEIGHTS_SHA256"

echo ""
echo "Setup complete. Start the server with ./run.sh"
