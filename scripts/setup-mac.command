#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "此安装程序用于 M 系列 Mac。"; exit 1
fi
if ! command -v brew >/dev/null; then
  echo "请先从 https://brew.sh 安装 Homebrew，再重新运行此文件。"; exit 1
fi
brew install python@3.11 ffmpeg sox
PYTHON="$(brew --prefix python@3.11)/bin/python3.11"
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r deploy/requirements-mac.txt
echo "准备本地模型。首次安装需要联网和约 20GB 可用磁盘，下载可重试。"
.venv/bin/python -m scripts.prepare_speech
.venv/bin/python -m scripts.prepare_llm
.venv/bin/python -m scripts.prepare_tts
.venv/bin/python -m scripts.prepare_avatar
.venv/bin/python -m scripts.configure_mac
chmod +x start-mac.command
echo "准备完成。以后双击 start-mac.command，即可离线启动。"
read -r -p "按回车关闭。"
