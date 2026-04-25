#!/usr/bin/env bash
# Auto-detects GPU and installs appropriate torch + whisperx.
# Usage: bash daemon/scripts/install-whisperx.sh
#
# whisperx depends on torch, but `uv pip install whisperx` pulls CUDA torch
# from PyPI regardless of GPU availability. This script installs torch from
# the right index first, then whisperx with --no-deps to preserve it.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Install torch from the correct index
if nvidia-smi &>/dev/null; then
    echo "NVIDIA GPU detected — installing CUDA-enabled PyTorch"
    uv pip install "torch~=2.8.0" "torchaudio~=2.8.0"
else
    echo "No NVIDIA GPU — installing CPU-only PyTorch"
    uv pip install "torch~=2.8.0" "torchaudio~=2.8.0" --index-url https://download.pytorch.org/whl/cpu
fi

# 2. Install whisperx without deps (to avoid pulling CUDA torch)
uv pip install whisperx --no-deps

# 3. Install whisperx's remaining dependencies (excluding torch/torchaudio)
uv pip install \
    "ctranslate2>=4.5.0" \
    "faster-whisper>=1.1.1" \
    "nltk>=3.9.1" \
    "numpy>=2.1.0" \
    "pandas>=2.2.3" \
    "pyannote-audio>=3.3.2,<4.0.0" \
    "huggingface-hub<1.0.0" \
    "transformers>=4.48.0" \
    "triton>=3.3.0"

echo "Done! WhisperX installed successfully."
echo "torch: $(python -c 'import torch; print(torch.__version__)')"
echo "CUDA:  $(python -c 'import torch; print(torch.cuda.is_available())')"
