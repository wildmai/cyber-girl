# cyber-girl 教程与视频工程

本工程配合 [详细开发教程](../../docs/开发教程.md) 和 [开发复盘](../../docs/开发复盘.md) 使用。
视觉方向为产品实录，以实际产品截图、当前源码摘录和流程动画解释开发过程。
画面包含截图运镜与图解，并不是连续操作录屏。

## 视频版本

首先输出约 90 秒宣传片和 18 分钟教程片的无声预览，画面标注“旁白待合成”。
用户配置密钥并指定克隆音色后，已生成 32 段豆包旁白。
有声时间轴按真实音频重排，宣传片约 79 秒，教程约 15 分 7 秒。
字幕使用服务返回的字词时间戳，专名与文字归一化差异只在同一句内插值。
原无声预览继续保留，有声版输出在 `output/course/final/`。
制作阶段没有调用付费数字人服务。

## 源文件

| 路径 | 用途 |
|---|---|
| `script.json` | 章节口播与最初视觉提纲 |
| `timeline.json` | 无声预览的逐镜时间轴，有声版由 `finish.py` 按实际旁白重排 |
| `assets/SOURCES.json` | 实际截图来源、采集日期与 SHA-256 |
| `promo/`、`tutorial/` | 可审阅的镜头表和口播文稿 |
| `render.py` | 确定性 Pillow + FFmpeg 渲染器 |
| `export.py` | 从时间轴导出镜头表、SRT 和章节 |
| `narrate.py` | 按章节生成豆包旁白，成功音频缓存复用，未确认结果不自动重试 |
| `finish.py` | 用实际字词时间戳重排镜头、字幕并混入旁白 |
| `qa_replay.py` | 将实际 MP4 的代表镜头解码为栅格回放，供技能 QA 检查 |
| `制作复盘.md` | 本次画面修复、审片与自动检查的边界 |

所有新增视频文字、口播和字幕统一称“第三方数字人”。
本地开源技术保留 Silero、Whisper、llama.cpp、Qwen3-TTS 和 MuseTalk 等真实名称。
历史 MuseTalk 截图仅展示本地设置，并裁去下方的旧服务名称。
`reply.jpg` 为历史云端回复截图，不用来证明本地 MuseTalk 的生成效果或速度。
合成时统一裁去该截图底部的旧技术条，避免历史服务品牌在全貌或图解背景中露出。
TTS 章节使用真实的历史本地合成等待界面，并裁去上下旧服务标记。
`local-qwen.png` 是设置页截图，只用作图解和命令背景，不作为目标 Mac 推理成功的证据。

## 重新生成

先安装 Python 3.11、Pillow 和带 libass/libx264 的 FFmpeg。
中文字体使用系统字体，工程没有附带或重新分发字体文件。
Linux 可安装 Noto CJK，macOS 使用 PingFang，Windows 使用微软雅黑。

```bash
python content/course/export.py
python content/course/render.py --film promo
python content/course/render.py --film tutorial
```

输出位于 `output/course/promo/` 与 `output/course/tutorial/`。
MP4、渲染缓存、审片与自动检查报告不提交到 Git。
修改渲染器或逐镜内容会使对应缓存失效。
修改截图或源码摘录后，应先清除对应镜头的 `.sha256` 缓存标记再重新生成。

## 画面检查

先用技能的 `storyboard_lint.py` 检查两份镜头表，再渲染。
整片使用 `film_gate.py` 检查快速动作、近空白和蓝紫底，红灯版本不交付。
代表镜头使用 `qa_replay.py` 解码，再通过技能 `qa.py` 检查栅格回放的确定性与运动。
栅格 QA 不能追踪已烧录文字的外框，也不等于整片逐帧验收。
字体裁切、字幕遮挡、截图与讲解的对应关系仍要独立审片。
长教程安排两位独立审片人，报告记录视频指纹和检查范围。
最后由 `deliver.py` 写出带事实指纹的交付说明，再以 `--verify` 复验。

## 重新配音与合成

在本机设置 `DOUBAO_TTS_API_KEY`，不要写入 JSON、脚本或提交到仓库。
音色通过命令行传入，不将私人音色 ID 固定在源码或默认设置中。
`seed-icl-2.0` 是资源版本，请求体的合成分支使用官方支持的 `seed-tts-2.0-standard`。

```bash
python content/course/narrate.py --speaker YOUR_SPEAKER_ID --film both
python content/course/finish.py --film both
```

`narrate.py` 记录请求摘要、服务用量、结束状态、实际时长和音频 SHA-256。
同样的文本与参数命中完整缓存后不会重复调用付费接口。
连接中断或未返回结束成功状态时保留不确定记录并停止，不自动重新提交。
先检查供应商结果，再决定如何处理该记录。
`finish.py` 将同句字幕与返回的字词时间关联，镜头保持 2-6 秒，尾部按帧补静音而不拉伸声音。
最终旁白以单声道 AAC 编码，目标响度为 -16 LUFS、真峰值上限为 -1.5 dBTP。
服务端时间戳也可能有识别误差，不能替代对专名发音和最终成片的复核。

接口依据 [豆包单向流式 HTTP 官方文档](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh)。

## 验证边界

截图来自真实应用，命令画面为操作示范，源码画面为当前源码摘录。
流程动画用于解释机制，不代表实时运行过程或速度。
16GB Mac 的预算约 13.1 GiB 是估算，目标 Mac 尚未完成完整推理与内存实测。
MuseTalk 当前 CPU 路线按句生成，历史生成速度不适合宣传为低延迟实时互动。
GitHub 仓库保持私有，视频工程不改变仓库访问权限。
