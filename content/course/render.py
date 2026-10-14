"""Deterministic real-product footage, pulse cameras, diagrams and preview subtitles.

Frames use local screenshots and real source excerpts, not invented app results.
The final narration pass can replace the provisional timeline after Doubao TTS.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/course"
W, H, FPS = 1920, 1080, 30
C = {"bg": "#121417", "surface": "#1e2226", "ink": "#f2efe8",
     "sub": "#a39e93", "accent": "#f2b33d", "teal": "#5fb3a1"}
FONTS = [Path("C:/Windows/Fonts/msyh.ttc"), Path("/System/Library/Fonts/PingFang.ttc"),
         Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
         Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")]
FONT = next((p for p in FONTS if p.exists()), None)


def font(size):
    if FONT is None:
        raise RuntimeError("Install a Chinese font or add its path to FONTS before rendering.")
    return ImageFont.truetype(str(FONT), size)


def display_text(text):
    names = {"llama 点 cpp": "llama.cpp", "llama 点cpp": "llama.cpp", "llama 点 js": "llama.js",
             "llama server": "llama-server", "Qwen 三 TTS": "Qwen3-TTS", "Q 四 K M": "Q4_K_M",
             "Audio worklet": "AudioWorklet", "app 点 js": "app.js", "Control 加 C": "Ctrl+C",
             "audio worklet": "AudioWorklet", "app main": "app/main.py", "PCM 十六": "PCM16",
             "setup mac 点 command": "setup-mac.command", "start mac 点 command": "start-mac.command",
             "Python 三点一一": "Python 3.11", "SHA 二五六": "SHA-256", "MP 四": "MP4",
             "API 密钥": "API key", "九 B": "9B", "cyber girl": "cyber-girl"}
    for a, b in names.items():
        text = text.replace(a, b)
    return text


def label(draw, xy, text, size=36, color="ink"):
    draw.text(xy, text, font=font(size), fill=C.get(color, color))


def panel(draw, box, fill="surface", outline="sub", width=2, radius=22):
    draw.rounded_rectangle(box, radius=radius, fill=C.get(fill, fill), outline=C.get(outline, outline), width=width)


def wrap_text(text, limit=34):
    lines, line, width = [], "", 0
    for ch in text:
        step = 1 if ord(ch) > 255 else 0.55
        if width + step > limit:
            lines.append(line)
            line, width = "", 0
        line += ch
        width += step
    if line:
        lines.append(line)
    if len(lines) > 1 and len(lines[-1]) < 5:
        tail = lines[-2][-5:]
        lines[-2] = lines[-2][:-5]
        lines[-1] = tail + lines[-1]
    return lines


def caption_lines(text, limit=32):
    """Keep technical words intact and prefer clause boundaries for captions."""
    tokens = re.findall(r"[A-Za-z0-9_.:/+\-]+|.", text)
    lines, current, width = [], [], 0
    for token in tokens:
        size = sum(1 if ord(ch) > 255 else 0.55 for ch in token)
        if width+size > limit and current:
            punctuation = [i+1 for i, value in enumerate(current) if value in "，。；、,;:："]
            cut = next((i for i in reversed(punctuation) if i >= len(current)//2), len(current))
            lines.append("".join(current[:cut]))
            current = current[cut:]
            width = sum(1 if ord(ch) > 255 else 0.55 for value in current for ch in value)
        current.append(token)
        width += size
    if current:
        tail = "".join(current)
        if lines and len(re.sub(r"[\W_]+", "", tail)) < 5:
            previous = re.findall(r"[A-Za-z0-9_.:/+\-]+|.", lines.pop())
            moved = []
            while previous and len("".join(moved)) < 5:
                moved.insert(0, previous.pop())
            if previous:
                lines.append("".join(previous))
            tail = "".join(moved) + tail
        lines.append(tail)
    return lines


def source_excerpt(shot):
    source = ROOT / shot["source"]
    lines = source.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if shot["symbol"] in line)
    # Reveal a different neighboring group as the explanation advances.
    start += min(shot["variant"] * 2, 6)
    return source.name, [(i + 1, line) for i, line in enumerate(lines[start:start + 8], start)]


def diagram(draw, shot):
    kind, variant = shot["kind"], shot["variant"]
    if kind == "memory":
        label(draw, (120, 210), "16GB Mac · 重型模型分阶段运行", 52)
        for i, (name, weight, color) in enumerate([("Whisper + TTS + VAD", 4.1, "sub"),
                                                  ("阶段 A：Qwen / llama.cpp", 6.5, "accent"),
                                                  ("阶段 B：MuseTalk", 3.8, "teal")]):
            y = 330 + i * 154
            panel(draw, (120, y, 1780, y + 106), outline=color)
            draw.rounded_rectangle((128, y + 78, 128 + weight / 16 * 1640, y + 98), 8, fill=C[color])
            label(draw, (160, y + 20), f"{name}    {weight:g} GiB", 35)
        label(draw, (130, 835), "A / B 不叠加 · 预算约 13.1 GiB（含余量）· 待 Mac 实测", 32, "sub")
        return
    if kind == "sessions":
        for i in range(2):
            x = 100 + i * 900
            panel(draw, (x, 250, x + 820, 590), outline="accent" if i == variant % 2 else "teal")
            label(draw, (x + 50, 294), f"用户 {'A' if i == 0 else 'B'} 的浏览器", 48)
            for k, value in enumerate(["独立上下文", "独立 VAD / 取消", "独立人设 / 数字人"]):
                label(draw, (x + 60, 385 + k * 56), value, 36, "sub")
            draw.line((x + 400, 590, 960, 750), fill=C["sub"], width=6)
        panel(draw, (430, 740, 1490, 875), outline="teal")
        label(draw, (490, 778), "共享 Whisper / TTS 权重 + 推理锁", 43)
        return
    if kind == "idle":
        label(draw, (120, 190), "计费连接暂停，本地麦克风仍在监听", 50)
        for i, name in enumerate(["第三方数字人", "10 秒空闲", "本地监听", "新语音重连"]):
            x = 100 + i * 450
            panel(draw, (x, 390, x + 380, 625), outline="accent" if i == variant else "sub")
            label(draw, (x + 30, 460), name, 43)
            if i < 3:
                draw.line((x + 380, 506, x + 450, 506), fill=C["teal"], width=8)
        label(draw, (160, 772), "保留 generation 与首句缓冲 → 等待连接就绪 → 继续回复", 39, "teal")
        return
    if kind == "captions":
        label(draw, (120, 220), "由播放时钟驱动字幕", 58)
        panel(draw, (120, 360, 1780, 700), outline="teal")
        draw.line((180, 580, 1700, 580), fill=C["sub"], width=8)
        for i in range(6):
            x = 180 + i * 304
            draw.line((x, 566, x, 600), fill=C["ink"], width=3)
        label(draw, (180, 400), "今天有点累，陪我聊聊", 72)
        label(draw, (160, 795), "媒体开始播放后才揭示 · 字符权重估算，不是逐字强制对齐", 37, "sub")
        return
    if kind == "generations":
        label(draw, (120, 210), "停止一次，丢弃整轮过期输出", 54)
        for i, name in enumerate(["旧代次 / generation 7", "新代次 / generation 8"]):
            y = 355 + i * 240
            label(draw, (130, y - 55), name, 38, "sub" if i == 0 else "accent")
            for j, stage in enumerate(["识别", "对话", "语音", "视频"]):
                x = 120 + j * 450
                panel(draw, (x, y, x + 380, y + 145), outline="sub" if i == 0 else "teal")
                label(draw, (x + 110, y + 44), stage, 46, "sub" if i == 0 else "ink")
                if i == 0:
                    draw.line((x + 20, y + 120, x + 350, y + 20), fill=C["sub"], width=5)
        return
    if kind == "https":
        label(draw, (130, 220), "Host 校验 ≠ 麦克风安全上下文", 53)
        names = ["浏览器 / HTTPS", "Nginx / WSS", "应用后端 / STT"]
        for i, name in enumerate(names):
            x = 100 + i * 620
            panel(draw, (x, 400, x + 540, 650), outline="teal" if i == variant % 3 else "sub")
            label(draw, (x + 30, 485), name, 37)
            if i < 2:
                draw.line((x + 540, 525, x + 620, 525), fill=C["accent"], width=8)
        label(draw, (150, 800), "可信证书 + 正确 origin + WebSocket 升级 + 续期", 42, "sub")
        return
    if kind == "verification":
        label(draw, (130, 200), "沿着证据逐层收敛", 56)
        for i, name in enumerate(["单元 / 格式与取消", "协议 / 连接和回收", "浏览器 / 播放与权限", "真机 / 推理与听见"]):
            y = 340 + i * 125
            panel(draw, (140 + i * 80, y, 1730, y + 98), outline="accent" if i == variant else "sub")
            label(draw, (190 + i * 80, y + 25), name, 40)
        return
    stages = ["Silero VAD", "Whisper", "Qwen / LLM", "Qwen3-TTS", "MuseTalk"]
    if kind == "packets":
        label(draw, (120, 240), "16 kHz · PCM16 · 512 samples / frame", 53)
        for i in range(10):
            x = 120 + i * 168
            panel(draw, (x, 420, x + 145, 670), outline="teal" if i <= variant + 3 else "sub")
            label(draw, (x + 20, 455), "PCM", 36)
            label(draw, (x + 18, 530), "1024", 34, "accent")
            label(draw, (x + 22, 585), "bytes", 29, "sub")
        label(draw, (120, 820), "一帧约 32ms → WebSocket 二进制数据 → VAD 缓冲", 41, "sub")
    else:
        label(draw, (120, 240), "一次语音请求的完整旅程", 56)
        for i, name in enumerate(stages):
            x = 80 + i * 365
            panel(draw, (x, 440, x + 320, 675), outline="accent" if i == variant else "sub", width=4)
            label(draw, (x + 22, 510), name, 32)
            label(draw, (x + 22, 575), ["切句", "识别", "回应", "声音", "口型"][i], 40, "teal")
            if i < 4:
                draw.line((x + 320, 550, x + 365, 550), fill=C["teal"], width=7)
        label(draw, (130, 820), "示例输入：今天有点累，陪我聊聊", 41, "sub")


def make_plate(shot, path):
    original = Image.open(HERE / "assets" / shot["asset"]).convert("RGB")
    if shot["asset"] == "reply.jpg":
        # The historical screenshot footer predates the generic provider labels.
        original = original.crop((0, 0, original.width, 730))
    if shot["asset"] == "musetalk.png":
        original = original.crop((0, 0, original.width, round(original.height * 0.61)))
    if shot["asset"] == "microphone.jpg":
        original = original.crop((0, 245, original.width, 665))
    if shot["asset"] == "audio-wait.png":
        original = original.crop((0, 105, original.width, 700))
    im = Image.new("RGB", (W, H), C["bg"])
    image = ImageOps.contain(original, (W, H), method=Image.Resampling.LANCZOS)
    im.paste(image, ((W - image.width) // 2, (H - image.height) // 2))
    is_diagram = shot["kind"] not in ("ui", "commands", "code")
    if is_diagram:
        im = ImageEnhance.Brightness(im).enhance(0.17)
    draw = ImageDraw.Draw(im)
    if shot["kind"] == "code":
        panel(draw, (72, 165, 1850, 895), fill="ink", outline="accent")
        filename, lines = source_excerpt(shot)
        label(draw, (115, 195), filename + " · 当前源码摘录", 36, "bg")
        row = 0
        for j, (number, line) in enumerate(lines):
            for part, text in enumerate(wrap_text(line.expandtabs(4), 50) or [""]):
                if row >= 13:
                    break
                y = 275 + row * 44
                label(draw, (112, y), f"{number:>3}" if part == 0 else ">", 27, "bg")
                if j == shot["variant"]:
                    draw.rounded_rectangle((190, y-3, 1780, y+39), 5, fill=C["accent"])
                label(draw, (202, y), text, 30, "bg")
                row += 1
    elif shot["kind"] == "commands":
        panel(draw, (80, 190, 1840, 880), fill="ink", outline="accent")
        label(draw, (130, 235), "Mac 终端 · 操作命令示范", 43, "bg")
        for j, command in enumerate(shot["commands"]):
            if j == shot["variant"] and not shot.get("narrated"):
                draw.rounded_rectangle((130, 343+j*95, 1770, 404+j*95), 6, fill=C["accent"])
            label(draw, (140, 350 + j * 95), "$ " + command, 38, "bg")
    elif is_diagram:
        diagram(draw, shot)
    elif shot["asset"] not in ("musetalk.png", "microphone.jpg") and not (shot.get("narrated") and shot["asset"] in ("home.png", "input.png", "reply.jpg")):
        # A locator marks the actual region being discussed; no app result is invented.
        if shot["asset"] in ("persona.png", "models.png", "local-qwen.png"):
            box = (1330, 220 + shot["variant"] * 105, 1850, 330 + shot["variant"] * 105)
        else:
            box = [(165, 460, 760, 700), (145, 775, 820, 930), (1560, 60, 1860, 130),
                   (150, 160, 760, 390)][shot["variant"]]
        draw.rounded_rectangle(box, 15, outline=C["accent"], width=3)
    backdrop = ImageEnhance.Brightness(ImageOps.fit(original, (W, H))).enhance(0.17)
    scale = 1.0 if shot["kind"] == "ui" and shot["asset"] in ("home.png", "input.png", "reply.jpg") else 0.80
    if shot.get("narrated") and shot["kind"] == "https":
        scale = 0.75
    inset = im.resize((round(W * scale), round(H * scale)), Image.Resampling.LANCZOS)
    backdrop.paste(inset, (round(W * (1-scale) / 2), round(H * (1-scale) / 2)))
    im = backdrop
    im.save(path)


def make_overlay(shot, path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    if shot["title"] and shot.get("chapter_intro", False):
        title = shot["title"]
        width = int(draw.textlength(title, font=font(37))) + 58
        panel(draw, (88, 32, 88 + width, 99), fill="bg", outline="accent", radius=11)
        label(draw, (117, 44), title, 37)
    # The preview label is intentionally visible and cannot be confused with final narration.
    label(draw, (1390, 38), "cyber-girl" if shot.get("narrated") else "画面预览 · 旁白待合成", 28, "sub")
    if shot["kind"] not in ("ui", "code", "commands"):
        label(draw, (90, 125), "流程示意", 26, "sub")
    if shot["asset"] == "musetalk.png":
        label(draw, (88, 125), "历史本地设置界面 · 非速度演示", 29, "sub")
    if shot["asset"] == "audio-wait.png":
        label(draw, (88, 125), "历史本地语音界面 · 非速度演示", 29, "sub")
    im.save(path)


def ass_time(seconds):
    centis = round(seconds * 100)
    return f"{centis // 360000}:{centis // 6000 % 60:02}:{centis // 100 % 60:02}.{centis % 100:02}"


def subtitle_file(shots, path, local=False):
    fam = "Microsoft YaHei" if os.name == "nt" else "PingFang SC"
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{fam},46,&H00F2EFE8,&H00F2EFE8,&H00121417,&H80121417,0,0,0,0,100,100,0,0,3,10,0,2,120,120,64,1",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    for s in shots:
        if "subtitle_events" in s:
            for event in s["subtitle_events"]:
                text = "\\N".join(caption_lines(display_text(event["text"]), 32)).replace("{", "(").replace("}", ")")
                start = max(0, event["start"] - s["start"]) if local else event["start"]
                end = min(s["duration"], event["end"] - s["start"]) if local else event["end"]
                lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
            continue
        text = "\\N".join(wrap_text(display_text(s["caption"]), 32)).replace("{", "(").replace("}", ")")
        start = 0 if local else s["start"]
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(start+s['duration'])},Default,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_shot(shot, work):
    target = work / (shot["id"] + ".mp4")
    stamp = hashlib.sha256((json.dumps(shot, ensure_ascii=False, sort_keys=True)
                           + Path(__file__).read_text(encoding="utf-8")).encode()).hexdigest()
    marker = target.with_suffix(".sha256")
    if target.exists() and marker.exists() and marker.read_text() == stamp:
        return target
    plate = work / (shot["id"] + ".png")
    make_plate(shot, plate)
    sub = work / (shot["id"] + ".ass")
    subtitle_file([shot], sub, local=True)
    overlay = work / (shot["id"] + "-overlay.png")
    make_overlay(shot, overlay)
    start, end = 1.0, 1.32
    main_ui = shot["kind"] == "ui" and shot["asset"] in ("home.png", "input.png", "reply.jpg")
    if main_ui:
        end = 1.60
    # Locate the relevant control, hold, then return to the full product context.
    pulse = "min(max((on-12)/9,0),1)-min(max((on-65)/9,0),1)"
    if shot["move"] == "横移":
        zoom = "1.32"
        fx = f"(0.45+0.1*({pulse}))"
    elif shot["move"] in ("停", "切"):
        zoom = "1.08"
        fx = str([0.35, 0.65, 0.5, 0.8][shot["variant"]])
    else:
        zoom = f"{start}+({end-start})*({pulse})"
        fx = "0.5"
    if main_ui:
        # Keep the left edge of the actual control labels inside the close-up.
        fx = f"(0.15*({pulse}))" if shot["move"] == "横移" else "0.0"
    fy = "0.5"
    vf = (f"[0:v]zoompan=z='{zoom}':x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}'"
          f":d={shot['frames']}:s={W}x{H}:fps={FPS}[cam];[cam][1:v]overlay=0:0[base]")
    inputs = ["-i", plate.name, "-i", overlay.name]
    if shot["kind"] not in ("ui", "code", "commands", "verification"):
        token = work / "request.png"
        if not token.exists():
            chip = Image.new("RGBA", (110, 48), (0, 0, 0, 0))
            d = ImageDraw.Draw(chip)
            panel(d, (0, 0, 108, 46), fill="accent", outline="accent", radius=7)
            label(d, (27, 7), "请求", 25, "bg")
            chip.save(token)
        inputs += ["-i", token.name]
        vf += f";[base][2:v]overlay=x='180+mod(t+{shot['variant'] * 0.4},2.1)/2.1*1480':y=705[moving];[moving]ass={sub.name}[out]"
    else:
        vf += f";[base]ass={sub.name}[out]"
    command = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", vf, "-map", "[out]",
               "-frames:v", str(shot["frames"]), "-c:v", "libx264", "-preset", "fast",
               "-crf", "19", "-threads", "2", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", target.name]
    subprocess.run(command, cwd=work, check=True, capture_output=True)
    marker.write_text(stamp)
    return target


def build(film, stills_only=False):
    plan = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))[film]
    directory = OUT / film
    work = directory / "work"
    work.mkdir(parents=True, exist_ok=True)
    subtitle_file(plan["shots"], directory / "字幕.ass")
    if stills_only:
        selected = [plan["shots"][0]]
        for kind in ("ui", "code", "commands", "pipeline", "memory", "idle", "sessions", "captions"):
            chosen = next((s for s in plan["shots"] if s["kind"] == kind), None)
            if chosen:
                selected.append(chosen)
        for shot in selected:
            make_plate(shot, directory / (shot["id"] + "-preview.png"))
        return
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = {executor.submit(render_shot, shot, work): shot for shot in plan["shots"]}
        for i, future in enumerate(as_completed(pending), 1):
            try:
                future.result()
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(exc.stderr.decode(errors="replace")) from exc
            if i % 10 == 0 or i == len(pending):
                print(f"{film}: {i}/{len(pending)} shots rendered", flush=True)
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{s['id']}.mp4'\n" for s in plan["shots"]), encoding="utf-8")
    name = "cyber-girl-宣传片-画面预览.mp4" if film == "promo" else "cyber-girl-开发教程-画面预览.mp4"
    target = directory / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", "-movflags", "+faststart", str(target)], check=True)
    print(target, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--film", choices=("promo", "tutorial", "both"), default="both")
    parser.add_argument("--stills", action="store_true")
    args = parser.parse_args()
    for selected in ("promo", "tutorial") if args.film == "both" else (args.film,):
        build(selected, args.stills)
