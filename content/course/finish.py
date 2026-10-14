"""Align existing visual shots and captions to actual narrated word timestamps."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from difflib import SequenceMatcher
import json
import math
import re
import subprocess
import wave

import numpy as np

from render import FPS, HERE, ROOT, caption_lines, display_text, render_shot, subtitle_file

OUT = ROOT / "output/course/final"


def normalized(text):
    return re.sub(r"[\W_]+", "", text).lower()


def character_times(record):
    """Map original characters to returned word timestamps, preserving pauses."""
    raw = normalized(record["req_params"]["text"])
    starts, ends = np.full(len(raw), np.nan), np.full(len(raw), np.nan)
    sentences = sorted((s for s in record["sentences"] if s.get("words")),
                       key=lambda s: s["words"][0]["startTime"])
    cursor = 0
    located = []
    for sentence in sentences:
        text = normalized(sentence["text"])
        offset = raw.find(text, cursor)
        if offset < 0:
            raise ValueError(f"Subtitle text cannot be located in {record['chapter']}: {sentence['text']}")
        words = sentence["words"]
        spoken, ts, te = "", [], []
        for word in words:
            part = normalized(word["word"])
            for i in range(len(part)):
                a, b = word["startTime"], word["endTime"]
                ts.append(a + (b-a) * i / len(part))
                te.append(a + (b-a) * (i+1) / len(part))
            spoken += part
        for block in SequenceMatcher(None, spoken, text, autojunk=False).get_matching_blocks():
            for i in range(block.size):
                starts[offset+block.b+i] = ts[block.a+i]
                ends[offset+block.b+i] = te[block.a+i]
        # A transliterated technical term may differ from the spoken token text.
        # Fill only that local sentence between known timings, never across a pause.
        indices = np.arange(len(text))
        for times, first, last in ((starts, words[0]["startTime"], words[-1]["endTime"]),
                                   (ends, words[0]["startTime"], words[-1]["endTime"])):
            values = times[offset:offset+len(text)]
            known = np.flatnonzero(np.isfinite(values))
            if len(known):
                values[:] = np.interp(indices, known, values[known], left=first, right=last)
            else:
                values[:] = np.linspace(first, last, len(text))
        located.append({"text": sentence["text"], "offset": offset,
                        "start": words[0]["startTime"], "end": words[-1]["endTime"]})
        cursor = offset + len(text)
    if not np.all(np.isfinite(starts)) or cursor != len(raw):
        raise ValueError(f"Missing timestamp coverage in {record['chapter']}")
    if np.min(np.diff(starts)) < -0.05 or np.any(ends < starts-0.05):
        raise ValueError(f"Non-monotonic timestamps in {record['chapter']}")
    if max(ends) > record["duration_s"] + 0.2:
        raise ValueError("Subtitle extends beyond audio")
    return starts, ends, located


def caption_events(record, chapter_start):
    starts, ends, sentences = character_times(record)
    events = []
    for sentence in sentences:
        chunks = caption_lines(sentence["text"], 30)
        cursor = sentence["offset"]
        for chunk in chunks:
            size = len(normalized(chunk))
            if size:
                events.append({"start": chapter_start+float(starts[cursor]),
                               "end": chapter_start+float(ends[cursor+size-1]),
                               "text": display_text(chunk)})
                cursor += size
    return starts, events


def retime_chapter(shots, record, chapter_start, global_index):
    starts, events = caption_events(record, chapter_start)
    chapter_frames = math.ceil(record["duration_s"] * FPS)
    char_cursor, boundaries, visuals = 0, [0], []
    for i, old in enumerate(shots):
        if i:
            boundary = round(float(starts[char_cursor]) * FPS)
            boundaries.append(max(boundaries[-1], min(chapter_frames, boundary)))
        char_cursor += len(normalized(old["caption"]))
        visuals.append(old)
    assert char_cursor == len(starts), record["chapter"]
    boundaries.append(chapter_frames)
    segments = []
    for i, old in enumerate(visuals):
        a, b = boundaries[i:i+2]
        if b-a < 2*FPS and segments:
            segments[-1][1] = b
        else:
            segments.append([a, b, old])
    if len(segments) > 1 and segments[0][1] < 2*FPS:
        segments[1][0] = 0
        segments.pop(0)
    result = []
    for a, b, old in segments:
        count = max(1, math.ceil((b-a) / (6*FPS)))
        cuts = [round(a+(b-a)*i/count) for i in range(count+1)]
        for i in range(count):
            shot = copy.deepcopy(old)
            shot.update(id=f"{record['chapter']}_{global_index+len(result)+1:03}",
                        frames=cuts[i+1]-cuts[i], start=chapter_start+cuts[i]/FPS,
                        narrated=True, chapter_intro=not result,
                        move=old["move"] if i == 0 else "快推")
            shot["duration"] = shot["frames"]/FPS
            shot["subtitle_events"] = [e for e in events if e["end"] > shot["start"]
                and e["start"] < shot["start"]+shot["duration"]]
            if record["chapter"] in ("p01", "t01") and not result:
                shot["title"] = ""
            result.append(shot)
    return result, events, chapter_frames


def prepare(film):
    old = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))[film]
    records = json.loads((ROOT / f"output/course/narration/{film}.json").read_text(encoding="utf-8"))
    directory = OUT / film
    directory.mkdir(parents=True, exist_ok=True)
    shots, events, chapters, all_pcm, frame_cursor = [], [], [], bytearray(), 0
    for chapter, record in zip(old["chapters"], records, strict=True):
        if chapter["id"] != record["chapter"]:
            raise ValueError("Narration chapter order differs from the visual plan")
        original = [s for s in old["shots"] if s["chapter_id"] == chapter["id"]]
        part, subtitles, frames = retime_chapter(original, record, frame_cursor/FPS, len(shots))
        shots.extend(part)
        events.extend(subtitles)
        chapters.append({"title": chapter["title"], "start": frame_cursor/FPS,
                         "duration": frames/FPS, "audio_sha256": record["audio_sha256"]})
        with wave.open(record["audio"], "rb") as source:
            assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, 48000)
            pcm = source.readframes(source.getnframes())
        all_pcm.extend(pcm)
        all_pcm.extend(b"\0" * (frames * 1600 * 2 - len(pcm)))
        frame_cursor += frames
    with wave.open(str(directory / "旁白.wav"), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48000)
        target.writeframes(all_pcm)
    plan = {"film": film, "duration": frame_cursor/FPS, "frames": frame_cursor,
            "chapters": chapters, "shots": shots, "subtitles": events,
            "alignment": "Doubao returned word timestamps; technical token gaps interpolated within their sentence"}
    (directory / "有声时间轴.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    subtitle_file([{ "start": 0, "duration": plan["duration"], "subtitle_events": events}], directory / "字幕.ass")
    def stamp(t):
        ms = round(t*1000)
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    (directory / "字幕.srt").write_text("\n".join(
        f"{i}\n{stamp(e['start'])} --> {stamp(e['end'])}\n{e['text']}\n" for i, e in enumerate(events, 1)), encoding="utf-8")
    (directory / "章节.txt").write_text("\n".join(
        f"{int(c['start'])//60:02}:{int(c['start'])%60:02} {c['title']}" for c in chapters)+"\n", encoding="utf-8")
    print(f"{film}: actual audio {plan['duration']:.2f}s, {len(shots)} shots, {len(events)} timed captions", flush=True)
    return directory, plan


def finish(film, prepare_only=False):
    directory, plan = prepare(film)
    if prepare_only:
        return
    work = directory / "work"
    work.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(render_shot, s, work) for s in plan["shots"]]
        for i, task in enumerate(as_completed(pending), 1):
            try:
                task.result()
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(exc.stderr.decode(errors="replace")) from exc
            if i % 20 == 0 or i == len(pending):
                print(f"{film}: {i}/{len(pending)} shots", flush=True)
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{s['id']}.mp4'\nduration {s['duration']:.9f}\n" for s in plan["shots"]), encoding="utf-8")
    name = "cyber-girl-宣传片.mp4" if film == "promo" else "cyber-girl-开发教程.mp4"
    target = directory / name
    temporary = target.with_name(target.stem + ".partial.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-i", str(directory / "旁白.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "160k",
                    "-t", str(plan["duration"]), "-movflags", "+faststart", str(temporary)], check=True)
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type:format=duration", "-of", "json", str(temporary)]))
    if {s["codec_type"] for s in info["streams"]} != {"audio", "video"} or abs(float(info["format"]["duration"])-plan["duration"]) > 0.1:
        raise RuntimeError("Final mux is incomplete; previous output retained.")
    temporary.replace(target)
    print(target, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--film", choices=("promo", "tutorial", "both"), default="both")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    for film in ("promo", "tutorial") if args.film == "both" else (args.film,):
        finish(film, args.prepare_only)
