"""Export reviewable subtitles, chapters and boards from the resolved timeline."""

import json
from pathlib import Path

from render import display_text, wrap_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def export():
    plan = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))
    for film in ("promo", "tutorial"):
        item = plan[film]
        out = ROOT / "output/course" / film
        out.mkdir(parents=True, exist_ok=True)
        subtitles = []
        chapters = []
        board = ["画风：产品实录，真实界面定位控件，源码与同色流程图解释一条语音请求。",
                 "色板：ink", "角色：无新增角色，仅保留真实产品截图里的既有人物形象。", "",
                 "| 时间段 | 画面里的物 | 它在做什么 | 镜头 | 屏上字 | 做法 |",
                 "|---|---|---|---|---|---|"]
        descriptions = {
            "ui": ("实际界面中的控件", "定位设置区域，镜头放大后返回应用全貌"),
            "code": ("当前源码文件", "沿函数相邻行定位处理阶段"),
            "commands": ("终端命令示范", "定位当前安装或验证命令"),
            "memory": ("分阶段内存预算条", "对比大脑与口型两个阶段的预算"),
            "sessions": ("两个浏览器的会话容器", "分别隔离私人状态并连接共享推理权重"),
            "idle": ("数字人连接和本地监听器", "沿空闲回收及重连路径传递请求"),
            "captions": ("媒体播放时间轴", "由播放时钟定位字幕揭示位置"),
            "generations": ("新旧两代任务队列", "取消旧代输出并保留新任务"),
            "https": ("浏览器与服务端连接", "经 HTTPS 和 WebSocket 传递语音"),
            "verification": ("四层验证记录", "从格式检查逐层推进到真机播放"),
            "packets": ("PCM 音频帧缓冲区", "把采集声音分成定长二进制帧"),
            "pipeline": ("语音请求与五个处理器", "按顺序切句识别回应并合成口型"),
        }
        for i, shot in enumerate(item["shots"], 1):
            text = "\n".join(wrap_text(display_text(shot["caption"]), 32))
            subtitles.append(f"{i}\n{stamp(shot['start'])} --> {stamp(shot['start']+shot['duration'])}\n{text}\n")
            if shot.get("chapter_intro"):
                seconds = round(shot["start"])
                chapters.append(f"{seconds // 60:02}:{seconds % 60:02} {shot['title']}")
            obj, action = descriptions[shot["kind"]]
            if shot["asset"] == "musetalk.png":
                obj, action = "历史本地口型设置面板", "查看本地开关与按句生成设置"
            title = shot["title"] if shot.get("chapter_intro") else ""
            board.append(f"| {shot['start']:.3f}-{shot['start']+shot['duration']:.3f} | {obj} | {action} | {shot['move']} | {title} | 真实素材 {shot['asset']} + t2 脉冲运镜，镜{shot['id']} |")
        (out / "字幕.srt").write_text("\n".join(subtitles), encoding="utf-8")
        (out / "章节.txt").write_text("\n".join(chapters)+"\n", encoding="utf-8")
        (HERE / film / "镜头表.md").write_text("\n".join(board)+"\n", encoding="utf-8")
        (HERE / film / "文稿.txt").write_text("\n".join(c["text"] for c in item["chapters"])+"\n", encoding="utf-8")


if __name__ == "__main__":
    export()
