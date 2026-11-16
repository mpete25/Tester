#!/bin/bash
# Twitch Highlight Finder – one-time setup (macOS, Apple Silicon and Intel; also works on Linux).
#
#   ./scripts/setup_macos.sh
#
# Installs FFmpeg (Homebrew), creates a Python virtual environment in .venv,
# installs the Python packages and downloads TwitchDownloaderCLI into ./bin.
set -euo pipefail
cd "$(dirname "$0")/.."

FALLBACK_VERSION="1.56.5"   # used if the latest release cannot be looked up
REPO="lay295/TwitchDownloader"
say()  { printf "\n\033[1;35m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[1;31mFEJL:\033[0m %s\n" "$1" >&2; exit 1; }

OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS-$ARCH" in
  Darwin-arm64)  ASSET="MacOSArm64" ;;
  Darwin-x86_64) ASSET="MacOS-x64" ;;
  Linux-x86_64)  ASSET="Linux-x64" ;;
  Linux-aarch64) ASSET="LinuxArm64" ;;
  *) fail "Ukendt platform: $OS $ARCH" ;;
esac
ASSET="${THF_CLI_ASSET:-$ASSET}"   # override, e.g. THF_CLI_ASSET=MacOS-x64 to use the Intel build via Rosetta
say "Platform: $OS $ARCH (TwitchDownloaderCLI-build: $ASSET)"

# --- Homebrew + FFmpeg -------------------------------------------------------
if [ "$OS" = "Darwin" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    fail 'Homebrew mangler. Installér det først og kør scriptet igen:
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  fi
  if ! command -v ffmpeg >/dev/null 2>&1; then
    say "Installerer FFmpeg"
    brew install ffmpeg
  fi
fi
command -v ffmpeg >/dev/null 2>&1 || fail "FFmpeg mangler. Installér det (macOS: brew install ffmpeg) og kør scriptet igen."
say "FFmpeg: $(ffmpeg -version | head -1)"

# --- Python 3.10+ ------------------------------------------------------------
find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}
PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ] && [ "$OS" = "Darwin" ]; then
  say "Installerer Python 3.12"
  brew install python@3.12
  PYTHON="$(find_python || true)"
fi
[ -n "$PYTHON" ] || fail "Python 3.10 eller nyere mangler (macOS: brew install python@3.12)."
say "Python: $("$PYTHON" --version) ($PYTHON)"

if [ ! -x .venv/bin/python ]; then
  say "Opretter virtuelt miljø i .venv"
  "$PYTHON" -m venv .venv
fi
say "Installerer Python-pakker"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# --- TwitchDownloaderCLI -------------------------------------------------------
mkdir -p bin
if [ -x bin/TwitchDownloaderCLI ] && [ "${1:-}" != "--update" ]; then
  say "TwitchDownloaderCLI findes allerede i ./bin (brug --update for at hente nyeste version)"
else
  say "Finder nyeste TwitchDownloaderCLI"
  URL="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null |
         grep -o "https://[^\"]*TwitchDownloaderCLI-[^\"]*-$ASSET\.zip" | head -1 || true)"
  if [ -z "$URL" ]; then
    URL="https://github.com/$REPO/releases/download/$FALLBACK_VERSION/TwitchDownloaderCLI-$FALLBACK_VERSION-$ASSET.zip"
    echo "Kunne ikke slå nyeste version op – bruger $FALLBACK_VERSION"
  fi
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  say "Henter $URL"
  curl -fL --retry 3 --progress-bar -o "$TMP/cli.zip" "$URL" || fail "Download mislykkedes.
Hent selv filen 'TwitchDownloaderCLI-<version>-$ASSET.zip' fra
  https://github.com/$REPO/releases/latest
pak den ud, og læg filen 'TwitchDownloaderCLI' i mappen: $(pwd)/bin"
  unzip -oq "$TMP/cli.zip" -d bin
  chmod +x bin/TwitchDownloaderCLI
fi
if [ "$OS" = "Darwin" ]; then
  # Remove the Gatekeeper quarantine flag so macOS lets the tool run.
  xattr -dr com.apple.quarantine bin 2>/dev/null || true
fi
[ -x bin/TwitchDownloaderCLI ] || fail "bin/TwitchDownloaderCLI blev ikke fundet efter udpakning."
say "TwitchDownloaderCLI: $(./bin/TwitchDownloaderCLI --version 2>&1 | head -1 || true)"

say "Færdig! Start programmet med:  ./run.sh"
