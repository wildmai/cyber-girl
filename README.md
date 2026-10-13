# cyber-girl · 小满

深色人物背景的中文 AI 陪伴应用，界面参考用户提供的图片。
本地语音模式链路为 **Silero VAD v5 → Whisper → MiniMax 国内站 API → Qwen3-TTS → MuseTalk 1.5 / Vidu S2**。
实时模式链路为 **Silero VAD v5 → Whisper → MiniMax 国内站 API → Vidu S2 云端语音与画面**。
语音识别与合成在本机执行，对话模型可选择在线 API 或本地 llama.cpp。
数字人可选择本地 MuseTalk 口型，或使用 Vidu S2 实时云端数字人。
新增 llama.cpp 本地后端，可加载指定 Qwen3.5-9B Q4_K_M；Mac mini 16GB 安装说明见 [MAC-LOCAL.md](MAC-LOCAL.md)。
从 GitHub 克隆后，运行 Mac 首次安装脚本会自动准备模型；权重、密钥和个人配置不进入 Git 仓库。

Linux Docker CPU 部署与压缩包启动步骤见 [LINUX-DEPLOY.md](LINUX-DEPLOY.md)。
该部署包默认使用实时数字人模式，已附带 Silero VAD v5 与 Whisper base 权重。

## 启动

建议 Python 3.11。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:MINIMAX_API_KEY = '在你本机填写密钥'
.\scripts\start.ps1
```

打开 <http://127.0.0.1:7860>，或双击 `start.bat`。
已有虚拟环境与环境变量时，可以直接启动。
密钥只由后端读取，不写进配置文件、不传给浏览器、不记录到日志。
服务启动时准备 MiniMax 客户端，第一次发送消息时验证 API key 与模型权限。
只装基础依赖即可进行文字对话，不必等待本地语音模型就绪。

## 对话人设

打开右上角“偏好设置”，在“对话人设”中修改名字、身份、性格与说话风格，点击“保存人设”。
默认人设为女友“小满”，温柔、甜美，有有趣的灵魂和自然幽默的表达风格。
人设最多 6000 字，保存在本机 `config/persona.json`，重启后保留，所有对话模式共用。
保存或恢复默认人设会停止当前回复并清空当前对话上下文，从下一轮开始生效，浏览器聊天记录保留。
人设作为系统提示词随每轮对话发送至当前对话服务，不影响音色与数字人模型选择。
实时模式在同一次对话请求中生成台词与独立的表演提示，结合当前人设、最近对话和语义转折决定微笑、关切目光、点头等小幅度表现。
动作提示通过 Vidu `text_action`（101）发送，台词通过 `text_tts`（100）发送，共用本轮消息 ID，动作说明不会出现在字幕、语音或聊天记录中。
Vidu 会话也使用当前人设，修改人设后结束旧数字人会话，下次发送或开始语音时自动重连。
静默期只使用中性的眨眼、呼吸和放松姿态，避免在严肃话题后随机嬉笑。
视频表现由 Vidu 模型生成，动作与语音分别渲染，接口没有逐帧表情或时间轴控制，不能保证精确到某个字的同步效果。

## Windows 桌面版

双击桌面上的“小满”快捷方式，或项目内的 `start-desktop.bat`。
程序会在独立桌面窗口中运行，自动启动本地服务并加载当前模式所需的语音模型。
第一次加载本地模型可能稍慢，窗口会显示启动画面。
重复双击会唤起已有窗口，关闭窗口会结束本次桌面服务与数字人会话。
桌面服务使用 `127.0.0.1:7861`，与原网页服务的 `7860` 端口分开。
人设、人物图片和模型配置继续使用项目内的 `config/`，桌面聊天记录与播放偏好保存在桌面应用自己的用户数据目录。
启动时重新读取 Windows 用户环境变量中的服务密钥，无需重启电脑，不会把密钥打包进程序。

开发与重新打包需要 Node.js 22.12 或更高版本。

```powershell
cd desktop
npm ci
npm run build
cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-desktop.ps1
```

可执行文件在 `output/desktop/Xiaoman-win32-x64/Xiaoman.exe`。
这是复用当前项目环境的桌面版，运行时无需 Node.js，但仍需要项目的 `.venv`、本地模型和配置目录。
不要单独移动 EXE；项目移动后重新运行快捷方式安装脚本即可。
启动日志见 `logs/desktop.log` 与 `logs/desktop-server.log`。
底层推理与云端服务的网络要求和网页版一致。

## MiniMax 国内站

默认使用 `MiniMax-M2.7` 和国内官方接口。
可在模型侧栏切换为 `MiniMax-M2.7-highspeed`。
切换 API 模型不需要 GPU，也不要求语音权重已安装。

```text
MINIMAX_API_KEY=本机环境变量中的密钥
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
```

`MINIMAX_BASE_URL` 不设置时默认国内站。
国际站可改为 `https://api.minimax.io/v1`，需要对应区域的密钥。
仅接受这两个官方 HTTPS 域名，拒绝自定义代理地址和重定向。
实现采用 [MiniMax 官方 Chat Completions API](https://platform.minimax.io/docs/api-reference/text-chat-openai) 的 `/v1/chat/completions`。
请求使用 `stream=true` 和 `reasoning_split=true`。
思考内容不会进入字幕、聊天记录或语音合成。
API 错误、权限不足、额度限制、超时和中断会显示明确提示，不返回假回答。

## 本地语音环境

安装推理依赖后，可加载三个语音模型。
建议独立虚拟环境，避免 Qwen3-TTS 的 Transformers 版本影响其他项目。
`qwen-tts==0.1.1` 要求 `transformers==4.57.3`。
NumPy 固定为 `1.26.4`，兼容 Whisper、Numba 与 PyTorch 的当前组合。
先安装与驱动兼容且版本一致的 CUDA 版 torch 和 torchaudio，再安装其余依赖。
以下为 CUDA 12.4 / PyTorch 2.6 示例。

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\python.exe -m pip install -r requirements-inference.txt
```

`config/models.json` 的 `device` 默认为 `auto`，优先使用 CUDA，未检测到时使用 CPU。
可显式设置为 `cuda:0` 或 `cpu`，但 CPU 速度较慢，且不属于 15 GiB 显存验收。
应用不会把 CPU 版 PyTorch 误报成 CUDA 推理。
权重需要提前准备，正常启动与运行不会自动下载。

```text
models/
  silero-v5/silero_vad.jit
  whisper/base.pt
  whisper/small.pt                          # 可选
  Qwen3-TTS-12Hz-0.6B-CustomVoice/
    config.json
    model.safetensors
    speech_tokenizer/
      config.json
      model.safetensors
      ...
    ...                                    # 完整官方 snapshot 的其他文件
  Qwen3-TTS-12Hz-1.7B-CustomVoice/           # 可选
```

- VAD 使用 [Silero v5.1](https://github.com/snakers4/silero-vad/tree/v5.1) 发布版本中的 v5 JIT。
- STT 使用 [OpenAI Whisper](https://github.com/openai/whisper) 的多语言 PT 权重。
- TTS 使用 [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 的 CustomVoice 完整 snapshot，包括 `speech_tokenizer` 子目录。

当前目录已放置 Silero v5、本机原有的 Whisper base 和 Qwen3-TTS 0.6B 完整权重。
来源与版本记录在 `config/model-sources.json`。
可显式运行以下命令准备 Qwen3-TTS 0.6B，约下载 2.33 GiB 公共权重。
此准备脚本需要联网，不参与正常应用运行。

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_tts
```

已有权重可以在模型侧栏的“添加其他本地模型”登记绝对路径，无需复制或重启。
支持兼容的 Silero v5 JIT、Whisper PT 和 Qwen3-TTS CustomVoice。
不接受网络共享路径。
VAD 版本无法通过文件名验证，请确保权重来源为 v5。

## 显存预算与热切换

默认本地组合为 Silero v5 + Whisper base + Qwen3-TTS 0.6B。
语音配置预估为 **5.6 GiB**，包括 1.5 GiB 运行余量。
默认开启 MuseTalk 1.5 后合计预估 **9.4 GiB**，包含口型模型与 batch=2 推理的 3.8 GiB 预估。
MiniMax API 不占本地显存。
这是一项配置预估，不是实测保证。
模型规格、语音长度、驱动与其他 GPU 程序都会改变实际占用。

本地计算通过共享锁串行进入 GPU，语音模型保持加载。
MiniMax 网络生成不占用 GPU 锁，因此 TTS 可以在后续文本仍在生成时合成已完成的句子。
文本流式返回，TTS 按句生成 WAV，MuseTalk 根据这段真实语音生成人物口型。
开启口型时播放带 AAC 音频的 H.264 MP4，音画使用同一时间轴，不重复播放原 WAV。
MuseTalk 按句生成和播放。
Vidu 接收按句合成后实时速率发送的 PCM，通过 RTC 返回实时画面与声音，但 TTS 本身仍需要先完成一句合成。

切换过程为：

1. 取消当前回复并丢弃旧代次输出。
2. 等待正在执行的本地推理结束。
3. 卸载被替换的模型并清理 CUDA 缓存。
4. 加载新模型，检查 GPU 整卡实际占用。
5. 成功后保存选择，失败时清理部分新模型并尝试恢复旧模型。

预估超出 15 GiB 的本地组合在加载前被拒绝。
GPU 实际占用通过 `nvidia-smi` 读取，包含其他进程，不能解释为仅本应用占用。
无可用监测时显示“暂无实测数据”。
加载与每段口型生成后检查无法保证生成过程中的显存峰值，需要在目标显卡上实测。

## MuseTalk 1.5 人物口型

安装 FFmpeg，并确认 `ffmpeg` 在 PATH 中。
GPU 环境需要先安装匹配的 PyTorch、torchaudio 和 torchvision，例如 2.6.0 / 2.6.0 / 0.21.0 的 CUDA 12.4 版本。
以下权重准备步骤需要联网，正常运行不会联网下载模型。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-avatar.txt
.\.venv\Scripts\python.exe scripts/prepare_avatar.py
```

国内下载受限时可显式指定 `--endpoint https://hf-mirror.com`。
脚本固定模型仓库 revision，校验每个文件的 Git SHA / LFS SHA-256，支持大文件断点续传。
ResNet 的上游旧 TAR 权重在校验后转换为本地 `resnet18-safe.pth`，推理仅使用 `weights_only=True` 读取。
所需文件保存在 `models/MuseTalk/`，总计约 4 GB。
MuseTalk 只在首次生成口型时加载，关闭口型开关会释放模型内存。
语音和口型共用 GPU 推理锁，生成过程按批检查取消状态。
模型侧栏提供数字人模型选择、开关、错误原因和合计显存预估。
CPU 可以验证功能，但生成视频可能需要数分钟，不承诺实时速度。
CPU 对 UNet 线性层使用动态 INT8 量化以减少 16 GB 内存机器的换页，卷积与其他模型保留 FP32；CUDA 使用 FP16。
CPU 量化结果可能与 GPU FP16 结果存在细微差异。

可用本地短 WAV 单独验证，无需调用对话 API：

```powershell
.\.venv\Scripts\python.exe scripts/check_avatar.py E:/audio/short.wav
```

当前适配单张人物照片，用 OpenCV Haar 检测最大正脸，替代上游 DWPose/MMCV 人脸关键点预处理。
口型 UNet 1.5、VAE、Whisper 音频特征、人脸解析及融合均使用真实模型。
裁剪精度与上游关键点版本存在差异，侧脸、手部遮挡等情况可能降低效果。
MuseTalk 主要生成嘴部与下脸区域，不生成眨眼、全身动作或表情驱动。
建议提供清晰正面成年人物照片。
口型生成失败时提示原因并继续播放原声音，视频解码失败也会回退至声音。
临时视频只通过本机 UUID 地址访问，保留最近 24 段且最长约一小时，在下一次生成时清理。
取消的任务会删除未完成的音视频文件。

## 操作

- 直接打字并按 Enter，即可通过 MiniMax 进行文字对话。
- 本地语音模式需要先在模型侧栏加载三个本地语音模型；实时模式只加载 Silero VAD 和 Whisper。
- 点击声球或麦克风并允许浏览器麦克风权限，开始语音对话。
- AudioWorklet 将实际设备采样率重采样为 16 kHz 单声道 PCM16，每帧 512 samples。
- Silero 检测约 650 ms 静音后提交 Whisper，单段最长约 20 秒。
- 回应期间暂停麦克风输入，防止扬声器回声触发新对话。
- 回复文字先缓存，声音开始播放后字幕随播放进度逐步显示；纯文字对话仍直接显示文字。
- CPU 合成首段可能需要几十秒，请等待自动播放，停止回复会取消该次语音输出。
- 右侧停止按钮或 Escape 停止当前回复，再继续说话。
- 右上角依次为模型与连接、聊天记录和偏好设置。
- 音量和字幕即时更新，预设音色从下一句生效。
- 人物照片保存在本机 `config/avatar-portrait.png`，可用于口型生成；不发送到外部服务。
- 聊天记录保存于当前浏览器 localStorage，关闭保存会清除持久化记录。
- 清空对话会同时清空当前服务端会话上下文。
- 一个服务支持多人独立对话，默认最多 8 个对话页面、3 路实时数字人。
- 各用户的人设、对话、语音检测和数字人连接独立；Whisper 等大模型权重共享。
- 画面连接超过 45 秒或 10 秒没有有效对话时自动挂断，超过并发上限时明确提示忙碌。

停止回复会关闭当前 MiniMax 流并立即停止浏览器播放。
Whisper 与 Qwen3-TTS 的单次本地推理不能在内部强制抢占，切换需等调用返回。
MuseTalk 在推理批次之间检查取消，旧回复视频不会进入新回复的播放队列。
聊天存档不会在刷新后自动送入 MiniMax；当前连接内保留最近六轮上下文。
日志在 `logs/app.log`，记录加载、切换、耗时和错误，不记录对话正文、音频或密钥。

## Vidu S2 实时数字人

支持本地语音模式与实时模式，两者均保留现有 Whisper 与对话模型。
本地语音模式采用 [Vidu S2 组件版](https://platform.vidu.cn/vidu-stream/doc/s2-avatar/component/parameters)，由 Qwen3-TTS 在本机合成声音。
实时模式采用 [Vidu S2 实时版](https://platform.vidu.cn/vidu-stream/doc/s2-avatar/realtime/parameters)，使用豆包语音和 `text_tts` 直接朗读回复文字。
使用国内接口 `https://api.vidu.cn`，明确指定 `model=vidu-s2`，由 Vidu 托管腾讯 TRTC 频道。
不需要另行配置 RTC 应用密钥，也不需要本地 MuseTalk 权重。
账号需要具备所选 S2 接口及豆包语音的使用权限，并有足够余额。

在启动服务的同一个 PowerShell 终端设置密钥，然后重新启动服务。
已经运行的服务不会自动读取另一个终端新增的环境变量。

```powershell
$env:VIDU_API_KEY = '在你本机填写 Vidu 国内站密钥'
.\scripts\start.ps1
```

1. 打开“模型与连接”，将数字人模型切换为“Vidu S2 · 云端实时数字人”。
2. 在“运行模式”选择“本地语音模式”或“实时模式”，开启数字人。
   实时模式下点击发送消息或开始语音会自动连接数字人，并在连接成功后继续本次操作；本地语音模式可点击“连接 Vidu S2 数字人”。
3. 等待渲染初始化和 RTC 画面订阅完成，再开始语音或文字对话。
4. 使用后点击“断开 Vidu 会话”，或关闭页面。

连接会上传当前人物照片。
本地语音模式将合成语音转换为 24 kHz、单声道、PCM16 小端，以每帧 100 ms 的实时速率上传。
实时模式不加载本地 Qwen3-TTS，完整回复文字通过 `type=100` 发送至 Vidu，不会让 Vidu 再生成一遍对话回复。
语音输入仍由本机 Silero VAD 与 Whisper 处理，打字对话只需要对话服务和 Vidu 会话。
浏览器接收数字人音视频，不向 RTC 发布摄像头或麦克风。
实时模式额外发布程序生成的全静音音轨，维持云端语音输入链路，避免长时间没有音频输入后失效。
API key 只用于服务端 HTTP / WebSocket 鉴权，浏览器仅接收临时 RTC 入会凭证。
浏览器 SDK 固定为 TRTC 5.20.1，文件保存在 `web/vendor/`，不从 CDN 加载。
Vidu 输出已经包含声音，正常情况下不会重复播放本地 WAV。

点击“停止回复”会取消当前音频上传或朗读等待、发送打断信令并立即静音浏览器播放。
停止回复会保留数字人会话，云端会话仍按官方规则计费；要结束会话须点击断开按钮。
切换运行模式、切换数字人模型、关闭数字人、更换照片、关闭页面或关闭服务都会结束控制连接。
切回本地语音模式后，需要重新加载 Qwen3-TTS 才能生成本地声音。
更换照片后需要重新连接 Vidu。
本地语音模式发送第一帧音频之前失败时回退到本地声音；已经开始上传后失败时停止本句，避免整句重播。
实时模式失败时显示错误并停止回复，不隐式加载本地 TTS。
实时模式的播放结束由浏览器远端音量活动及 1.8 秒静音窗口判断，不是 Vidu 的逐句完成回执。
切换或停止会取消旧请求，过期播放完成消息不会结束新的回复。
实时会话在 10 秒没有有效对话时自动挂断；指定文本朗读不会进入 Vidu 对话上下文，使用后仍可主动断开。
网络、S2 权限、托管 RTC 可用性及账户额度会影响连接，无法保证云端延迟。
当前已通过模拟官方协议的连接、初始化重试、音频节奏、打断和回退测试，以及实际浏览器的模型切换与配置提示检查。
Vidu 国内站真实会话创建、渲染初始化和浏览器 RTC 视频接收已通过，实测视频流为 1344×768。
真实 Qwen3-TTS 四字短句合成后，已成功向 Vidu 上传 2.48 秒音频。
这次 CPU 合成约耗时 76 秒，不能将此配置视为低延迟语音对话。
浏览器已检测到 RTC 远端音频，用户已确认听到声音回复。
测试会话已断开，使用时需要重新连接。
实时模式已通过真实人物画面接收、云端声音播放、连续句子、空闲约 90 秒后继续回复、播放中打断及打断后的下一轮对话验证。
四字回复的云端朗读文本提交到浏览器检测到音频约 1.9 秒，不包含对话模型生成时间。
对话 API 偶尔仍需较长等待，这不是固定的端到端延迟承诺。

## 网络与数据范围

后端只监听 `127.0.0.1`。
MuseTalk 模式下前端只连接本地服务。
后端向配置的 MiniMax 官方域名发送对话文本和当前会话上下文。
Vidu 本地语音模式额外向 Vidu 国内站发送人物图片、合成语音和回复文本，实时模式只发送人物图片与回复文本。
浏览器通过腾讯 RTC 接收音视频，实时模式发布的保活音轨为程序生成的全静音内容。
麦克风原始音频和浏览器历史存档不发送到外部服务。
人物图片会从浏览器传给 `127.0.0.1` 本机后端以进行口型推理。
选择 MiniMax 或云端数字人需要网络及对应 API key；选择 llama.cpp 与 MuseTalk 后可纯本地运行。

Hugging Face / Transformers 保持离线，模型使用本地路径加载。
Python socket 层仅允许回环连接、MiniMax 官方 API，以及显式连接 Vidu 时加入白名单的国内 API 地址。
前端没有 CDN、远程字体或分析脚本，CSP 限制连接来源。
HTTP 写操作与 WebSocket 验证同源来源。
系统浏览器、驱动和原生第三方库不属于 Python socket 拦截边界。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests scripts
.\.venv\Scripts\python.exe -m compileall -q app scripts
node --check web/app.js
node tests/audio-worklet.test.cjs
node --experimental-vm-modules tests/vidu-player.test.cjs
node --experimental-vm-modules tests/conversation-start.test.cjs
npm --prefix desktop test
```

界面验收需要 Playwright 和本地 Chromium。
界面脚本不调用 MiniMax。
以下 live 测试会通过你的 MiniMax key 发送一条短测试消息。

```powershell
.\.venv\Scripts\python.exe tests/e2e_ui.py
.\.venv\Scripts\python.exe -m scripts.check_minimax
.\.venv\Scripts\python.exe tests/e2e_minimax.py
```

本地语音检测和识别可使用自己的 WAV 验证。

```powershell
.\.venv\Scripts\python.exe -m scripts.check_speech --audio E:/audio/test.wav
.\.venv\Scripts\python.exe -m scripts.check_tts
```

完整浏览器语音验收将 WAV 作为 Chromium 的麦克风设备输入，使用真实本地模型及 MiniMax API，并验证音频播放完成。
该测试会发送识别后的短文本至 MiniMax，CPU 上可能需要数分钟。

```powershell
.\.venv\Scripts\python.exe tests/e2e_voice.py --audio E:/audio/test.wav
```

当前开发机器没有 NVIDIA GPU。
语音管线、桌面及手机界面、API 模型切换、MiniMax 国内站真实流式请求和浏览器文字对话已通过。
Silero v5、Whisper base、Qwen3-TTS 0.6B 的真实本地推理，以及从浏览器麦克风 WAV 输入到回复播放的完整语音闭环已通过。
CPU 上一次中文识别约 2.7 秒，两段回复分别合成约 61 秒和 17 秒。
MuseTalk 1.5 已在这台 CPU 机器生成 0.6 秒 / 15 帧的真实口型视频，带 H.264 画面与 AAC 音轨，总耗时约 218 秒。
浏览器完整测试中，单字回复的 TTS 耗时约 18 秒，1.52 秒 / 38 帧人物视频生成耗时约 293 秒，音画自动播放后正常回到待机。
检查确认嘴部随帧变化、背景基本保持不变，当前 CPU 性能不适合实时数字人对话。
58 项单元与集成测试覆盖口型失败回退、旧任务取消、显存预算、图片验证、视频访问范围及 Vidu 双模式协议。
目标显卡的峰值占用、推理延迟与实体麦克风设备仍需在目标环境验收。

## 上游与素材

直接复用 [Hugging Face speech-to-speech](https://github.com/huggingface/speech-to-speech) 的 ThreadManager 和 VADIterator。
上游版本与 Apache-2.0 许可见 `vendor/s2s/NOTICE.md` 和 `vendor/s2s/LICENSE`。
`app/pipeline.py` 适配四阶段队列，增加本地浏览器传输、MiniMax API、取消机制与模型热切换。
MuseTalk 推理代码来源和本地修改见 `vendor/musetalk_core/NOTICE.md`，MIT 许可与上游依赖许可见同目录 LICENSE。
`vendor/MuseTalk/` 是开发时的官方源码克隆，不参与运行且已加入忽略列表。
开发时保留的 `vendor/speech-to-speech/` 不参与运行，已加入忽略列表。

人物背景通过内置 imagegen 工具根据参考图生成，路径为 `web/assets/xiaoman.png`。
提示词在 `web/assets/portrait-prompt.txt`。
运行时图片直接从本地读取。

## Mac mini 本地大脑

保留 MuseTalk 1.5 本地口型，增加 llama.cpp 加载指定 Qwen3.5-9B Q4_K_M 的选项。
Mac M 系列 16GB 的安装、低内存配置与启动说明见 [MAC-LOCAL.md](MAC-LOCAL.md)。
云端数字人空闲 10 秒会自动断开，麦克风继续本地监听，再次说话自动重连。
本地 MuseTalk 不受此超时限制。
