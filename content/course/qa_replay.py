"""Replay representative decoded MP4 frames through the skill's canvas QA.

This checks raster replay determinism and motion, not baked text bounds.
Independent visual review and the whole-film gate remain necessary.
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "output/course"

HTML = """<!doctype html><meta charset="utf-8"><canvas id="c" width="1920" height="1080"></canvas>
<script>
const ERAS = PLAN;
window.__canvas = document.querySelector('canvas');
window.__total = ERAS.at(-1).t1;
const images = {};
window.renderSolo = (id, t) => {
  const frames = images[id];
  const i = Math.min(frames.length-1, Math.max(0, Math.floor(t*30)));
  window.__canvas.getContext('2d').drawImage(frames[i],0,0,1920,1080);
};
window.prepare = async () => {};
window.renderFrame = t => {
  const e = ERAS.find(e => t < e.t1) || ERAS.at(-1);
  window.renderSolo(e.id,t-e.t0);
};
Promise.all(ERAS.flatMap(e => {
  images[e.id] = [];
  return Array.from({length:e.count},(_,i) => new Promise((resolve,reject) => {
    const im = new Image(); images[e.id][i] = im;
    im.onload=resolve; im.onerror=reject;
    im.src=`${e.id}/${String(i+1).padStart(4,'0')}.jpg`;
  }));
})).then(() => window.__ready=true).catch(e => window.__bootFailed=String(e));
</script>"""


def build(film, narrated=False):
    directory = OUT / "final" / film if narrated else OUT / film
    plan = json.loads((directory / "有声时间轴.json").read_text(encoding="utf-8")) if narrated else json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))[film]
    chosen = {}
    for shot in plan["shots"]:
        # A pulse shot includes both the locating move and the return to context.
        if shot["move"] == "快推":
            chosen.setdefault(shot["kind"], shot)
    for shot in plan["shots"]:
        chosen.setdefault(shot["kind"], shot)
    root = directory / "qa-replay"
    root.mkdir(parents=True, exist_ok=True)
    scenes, t0 = [], 0
    for kind, shot in chosen.items():
        target = root / kind
        target.mkdir(exist_ok=True)
        seconds = min(3, shot["duration"])
        count = round(seconds * 30)
        source = directory / "work" / (shot["id"] + ".mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
                        "-vf", "scale=960:540", "-frames:v", str(count),
                        "-q:v", "2", str(target / "%04d.jpg")], check=True)
        scenes.append({"id": kind, "source_shot": shot["id"], "t0": t0,
                       "t1": t0 + seconds, "count": count})
        t0 += seconds
    (root / "index.html").write_text(HTML.replace("PLAN", json.dumps(scenes)), encoding="utf-8")
    (root / "范围.json").write_text(json.dumps({"source": "actual encoded shot MP4",
        "sample": scenes, "limits": "960x540 raster replay; baked text is not tracked; not a whole-film QA"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--film", choices=("promo", "tutorial"), required=True)
    parser.add_argument("--narrated", action="store_true")
    args = parser.parse_args()
    build(args.film, args.narrated)
