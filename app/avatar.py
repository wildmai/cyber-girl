"""Local avatar lifecycle and bounded media storage, sharing the speech GPU lock."""

import gc
import importlib.util
import io
import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)
REQUIRED = ("musetalkV15/musetalk.json", "musetalkV15/unet.pth", "sd-vae/config.json",
            "sd-vae/diffusion_pytorch_model.bin", "whisper/config.json", "whisper/pytorch_model.bin",
            "whisper/preprocessor_config.json", "face-parse-bisent/79999_iter.pth",
            "face-parse-bisent/resnet18-safe.pth")


class AvatarService:
    @property
    def estimated_gib(self):
        return 3.8 if self.provider == "musetalk" else 0

    def __init__(self, manager, config_path):
        self.manager = manager
        self.config_path = Path(config_path)
        self.config = {"enabled": False, "custom_portrait": False, "provider": "musetalk", "mode": "standard"}
        if self.config_path.is_file():
            self.config.update(json.loads(self.config_path.read_text(encoding="utf-8")))
        self.model_dir = ROOT / "models/MuseTalk"
        self.media_dir = ROOT / "output/avatar"
        self.runtime = None
        self.last_error = None
        from app.vidu import ViduSession
        self.vidu = ViduSession()

    @property
    def provider(self):
        return self.config["provider"]

    @property
    def enabled(self):
        return self.config["enabled"]

    @property
    def realtime(self):
        return self.enabled and self.provider == "vidu" and self.config["mode"] == "realtime"

    @property
    def portrait(self):
        return self.config_path.parent / "avatar-portrait.png" if self.config["custom_portrait"] else ROOT / "web/assets/xiaoman.png"

    def status(self):
        missing = [name for name in REQUIRED if not (self.model_dir / name).is_file()]
        dependencies = all(importlib.util.find_spec(module) is not None for module in ("diffusers", "cv2", "torchvision"))
        local_available = not missing and dependencies and bool(shutil.which("ffmpeg"))
        vidu = self.vidu.status()
        return {"name": "MuseTalk 1.5" if self.provider == "musetalk" else "Vidu S2", "provider": self.provider,
                "mode": self.config["mode"], "realtime": self.realtime,
                "enabled": self.enabled, "loaded": self.runtime is not None if self.provider == "musetalk" else vidu["connected"],
                "available": local_available if self.provider == "musetalk" else vidu["key_configured"],
                "providers": [{"id": "musetalk", "name": "MuseTalk 1.5 · 本地口型", "available": local_available},
                              {"id": "vidu", "name": "Vidu S2 · 云端实时数字人", "available": vidu["key_configured"]}],
                "vidu": vidu, "missing": missing if self.provider == "musetalk" else [],
                "estimated_gib": self.estimated_gib, "last_error": vidu["last_error"] if self.provider == "vidu" else self.last_error,
                "portrait_url": "/api/avatar/portrait" if self.config["custom_portrait"] else "/assets/xiaoman.png"}

    def save(self):
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)

    def set_enabled(self, enabled, provider=None, mode=None):
        with self.manager.lock:
            provider = provider or self.provider
            mode = mode or self.config["mode"]
            if provider not in ("musetalk", "vidu"):
                raise ValueError("不支持的数字人服务。")
            if mode not in ("standard", "realtime"):
                raise ValueError("不支持的运行模式。")
            if enabled and provider == "musetalk" and not self.status()["providers"][0]["available"]:
                raise ValueError("MuseTalk 尚未准备完成，请运行 scripts/prepare_avatar.py 并安装 requirements-avatar.txt 与 FFmpeg。")
            old_cost = self.estimated_gib if self.enabled else 0
            new_cost = 3.8 if enabled and provider == "musetalk" else 0
            realtime = enabled and provider == "vidu" and mode == "realtime"
            estimate = (self.manager.estimate(self.manager.selected, include_tts=not realtime)
                        if realtime != self.realtime else self.manager.estimate(self.manager.selected))
            serial = (self.manager.config.get("low_memory", False)
                      and self.manager.catalog[self.manager.selected["llm"]].get("backend") == "llama.cpp")
            if serial:
                llm_cost = self.manager.catalog[self.manager.selected["llm"]]["vram_gib"]
                estimate += max(llm_cost, new_cost) - max(llm_cost, old_cost)
            else:
                estimate += new_cost - old_cost
            if estimate > self.manager.config["budget_gib"]:
                raise ValueError("开启人物口型后超出显存预算，请先选择更小的语音模型。")
            if provider != self.provider or mode != self.config["mode"] or not enabled:
                self.unload()
            self.config["provider"] = provider
            self.config["enabled"] = enabled
            self.config["mode"] = mode
            if realtime and "tts" in self.manager.loaded and not getattr(self.manager, "shared_models", False):
                self.manager._release("tts")
            self.last_error = None
            self.vidu.error = None
            self.save()
            return self.status()

    def set_portrait(self, data=None):
        image = None
        if data is not None:
            if len(data) > 15 * 1024 * 1024:
                raise ValueError("图片大小不能超过 15 MB。")
            try:
                with Image.open(io.BytesIO(data)) as source:
                    if source.width * source.height > 16_000_000:
                        raise ValueError("图片像素过大，请缩小至 1600 万像素以内。")
                    if source.format not in ("PNG", "JPEG", "WEBP"):
                        raise ValueError("请选择 PNG、JPG 或 WebP 图片。")
                    image = source.convert("RGB")
                    image.thumbnail((1920, 1920))
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("无法读取图片，请选择有效的人物照片。") from exc
        with self.manager.lock:
            self.vidu.close()
            if image is not None:
                temporary = self.config_path.parent / "avatar-portrait.tmp"
                image.save(temporary, format="PNG")
                temporary.replace(self.config_path.parent / "avatar-portrait.png")
            self.config["custom_portrait"] = image is not None
            self.save()
            return self.status()

    def clip_path(self, name):
        if not re.fullmatch(r"[0-9a-f]{32}\.mp4", name):
            return None
        path = self.media_dir / name
        return path if path.is_file() else None

    def cleanup(self):
        self.media_dir.mkdir(parents=True, exist_ok=True)
        for path in self.media_dir.iterdir():
            if path.is_file() and re.fullmatch(r"[0-9a-f]{32}\.(wav|silent\.mp4)", path.name):
                if time.time() - path.stat().st_mtime > 3600:
                    path.unlink(missing_ok=True)
        files = sorted((path for path in self.media_dir.glob("*.mp4")
                        if re.fullmatch(r"[0-9a-f]{32}\.mp4", path.name)),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            if index >= 24 or time.time() - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)

    def render(self, wav, valid, progress):
        if not self.enabled or not valid():
            return None
        with self.manager.lock:
            if not self.enabled or not valid():
                return None
            if not self.status()["available"]:
                raise ValueError("人物口型模型文件不完整，请重新准备 MuseTalk 1.5 权重。")
            from app.avatar_runtime import Cancelled, MuseTalkRuntime

            self.cleanup()
            name = uuid.uuid4().hex
            audio = self.media_dir / f"{name}.wav"
            output = self.media_dir / f"{name}.mp4"
            started = time.perf_counter()
            success = False
            try:
                progress(0)
                if self.runtime is None:
                    device = self.manager.device
                    if device == "auto":
                        import torch
                        device = "cuda:0" if torch.cuda.is_available() else "cpu"
                        self.manager._device = device
                    log.info("Loading MuseTalk 1.5 on %s", device)
                    self.runtime = MuseTalkRuntime(self.model_dir, device)
                    self.manager._check_memory()
                audio.write_bytes(wav)
                self.runtime.render(self.portrait, audio, output, shutil.which("ffmpeg"), valid, progress)
                self.manager._check_memory()
                if not valid():
                    return None
                self.last_error = None
                success = True
                log.info("MuseTalk clip complete in %.2fs", time.perf_counter() - started)
                return "/api/avatar/clips/" + output.name
            except Cancelled:
                log.info("MuseTalk generation cancelled")
                return None
            except Exception as exc:
                self.last_error = str(exc)
                self.unload()
                raise
            finally:
                audio.unlink(missing_ok=True)
                output.with_suffix(".silent.mp4").unlink(missing_ok=True)
                if not success:
                    output.unlink(missing_ok=True)
                if self.manager.config.get("low_memory", False):
                    self.release_local()

    def unload(self):
        self.vidu.close()
        self.release_local()

    def release_local(self):
        self.runtime = None
        gc.collect()
        import sys
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
