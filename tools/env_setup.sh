#!/usr/bin/env bash
# Setup script for the cloud environment that runs a Morning Fold (SETUP.md step 8).
# Paste it verbatim into the environment's setup script field: its result is cached, so every run
# starts with the packages and the headless browser in place.
# RUN.md falls back to it when a run finds the environment unprepared.
# Engine maintainers: keep the package list in step with requirements.txt.
set -euo pipefail

python3 -m pip install --break-system-packages -q \
  numpy onnxruntime tokenizers requests feedparser trafilatura pymupdf faster-whisper "av==15.1.0" playwright

# Chromium for sources that block plain requests; --with-deps needs root, plain install works without.
python3 -m playwright install --with-deps chromium || python3 -m playwright install chromium

# The Whisper model (about 1.6 GB) is not preloaded: the cached setup must finish within about 5 minutes,
# so runs download it on first use. Change the 0 below to 1 only if this script still finishes in time.
if [ "${FOLD_PRELOAD_WHISPER:-0}" = "1" ]; then
  python3 -c "from faster_whisper import WhisperModel; WhisperModel('${FOLD_WHISPER_MODEL:-large-v3-turbo}', device='cpu', compute_type='int8')"
fi
echo "morning-fold environment ready"
