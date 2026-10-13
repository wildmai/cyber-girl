#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python || ! -f config/mac/models.json ]]; then
  echo "请先运行 scripts/setup-mac.command 完成一次安装。"; exit 1
fi
export SAIBOGIRL_MODEL_CONFIG="$PWD/config/mac/models.json"
export SAIBOGIRL_MAX_DIALOGUES=1
export SAIBOGIRL_AUTOLOAD_SPEECH=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset SAIBOGIRL_PUBLIC_ORIGIN SAIBOGIRL_ALLOWED_HOSTS
.venv/bin/python -m scripts.launch_mac
