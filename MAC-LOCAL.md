# Mac mini 16GB 本地运行

保留 MuseTalk 1.5，未接入 Duix 或 LiveTalking。
本地大脑是 llama.cpp 加载 `HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive` 的 Q4_K_M GGUF，文件约 5.63GB。
语音使用 Silero VAD v5、Whisper base 和 Qwen3-TTS 0.6B。

1. 从私有 GitHub 仓库克隆项目，或将发布包解压到 Mac 的本地磁盘，准备约 20GB 可用空间。
2. 安装 [Homebrew](https://brew.sh)，然后在终端运行 `bash scripts/setup-mac.command`。
3. 首次准备会联网安装依赖和下载权重，并校验 Qwen 文件的 SHA256。
4. 以后双击 `start-mac.command`，自动启动服务并打开 `http://127.0.0.1:7860`。
5. 关闭启动终端或按 Ctrl+C 停止服务。

已安装 Homebrew 的 Mac 可以按以下步骤拉取并运行。
`gh auth login` 需要登录有权访问私有仓库的 GitHub 账号。

```bash
brew install gh
gh auth login
gh repo clone 189-sketch/cyber-girl
cd cyber-girl
bash scripts/setup-mac.command
./start-mac.command
```

Git 仓库不包含模型权重、密钥、日志或个人配置。
首次安装会准备 Silero VAD、Whisper、Qwen、MuseTalk 的模型文件，并下载 Apple Silicon 原生 llama.cpp 运行时。
配置和下载结果保留在本机，再次执行安装脚本会复用已经校验的语音及对话权重。

安装完成后，可执行以下检查。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check app tests scripts deploy
```

浏览器中允许麦克风后，先发送短句检查文字、语音和口型播放。
MuseTalk 太慢时可以在“模型与连接”中关闭数字人，再测试纯语音链路。

首次准备后，本地模式无需 API key，不会自动下载模型。
Mac 原生运行，llama.cpp 自动选择 Metal，语音和 MuseTalk 当前使用 CPU。
不要使用 Docker 运行这个 Mac 配置，因为它无法使用宿主机的 Metal 加速。

16GB 配置采用 4096 token 上下文和模型分阶段释放，完成文字生成后停止 llama-server，再生成语音与口型。
MuseTalk 每段完成后也会释放运行时，避免与下一轮 LLM 同时常驻。
运行余量纳入预算，但估算不代表实际内存保证，仍应在活动监视器检查内存压力。
默认只允许一页对话，以限制内存峰值。

MuseTalk 目前按句生成视频，CPU 推理等待较久，不等于低延迟实时数字人。
当前开发环境没有连接目标 Mac，Metal、麦克风和人物口型尚未在该 Mac 完成实际验收。
已有 Windows CPU 实测也表明 MuseTalk 会明显增加等待时间，可以在设置中暂时关闭数字人，只使用本地文字与语音。

配置文件为 `config/mac/models.json`，人设和人物图片分别保存在同目录及其 `profiles` 子目录。
默认人设仍为温柔、甜美、幽默的女友“小满”，可以在偏好设置修改。
日志为 `logs/app.log` 与 `logs/llama-server.log`。
选择云端数字人或在线对话模型时，需额外配置相应密钥，所选云端服务仍需联网。

模型来源及固定版本见 `scripts/prepare_llm.py`。
仅下载文本 GGUF，不下载 BF16 原始权重或视觉投影模型。
