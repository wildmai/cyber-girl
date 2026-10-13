import gc
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from app.minimax import MiniMaxLLM, api_base_url
from app.local_llm import LOCAL_MODEL, LocalLLM, server_binary
from app.persona import PersonaStore

ROOT = Path(__file__).resolve().parent.parent
STAGES = ("vad", "stt", "llm", "tts")
log = logging.getLogger(__name__)


class ModelError(RuntimeError):
    pass


class ModelManager:
    """One shared inference lock also acts as the hot-swap quiescence barrier."""

    def __init__(self, config_path=None, loader=None):
        self.config_path = Path(config_path or os.environ.get("SAIBOGIRL_MODEL_CONFIG") or ROOT / "config/models.json")
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not any(m["id"] == LOCAL_MODEL["id"] for m in self.config["models"]):
            self.config["models"].append(dict(LOCAL_MODEL))
        self.catalog = {m["id"]: m for m in self.config["models"]}
        self.selected = dict(self.config["selected"])
        self.loaded = {}
        self.lock = threading.RLock()
        self.loader = loader or self._load
        self.switching = False
        self.last_error = None
        self._gpu_cache = (0, None)
        self._device = None
        from app.avatar import AvatarService
        self.avatar = AvatarService(self, self.config_path.with_name("avatar.json"))
        self.persona = PersonaStore(self.config_path.with_name("persona.json"))
        self._validate_selection(self.selected)

    def path(self, spec):
        value = Path(spec["path"]).expanduser()
        return value if value.is_absolute() else ROOT / value

    @property
    def device(self):
        return self._device or self.config["device"]

    def available(self, spec):
        if spec.get("backend") == "minimax":
            return bool(os.environ.get("MINIMAX_API_KEY", "").strip())
        path = self.path(spec)
        if spec.get("backend") == "llama.cpp":
            return path.is_file() and bool(server_binary())
        if spec["stage"] == "tts":
            return path.is_dir() and (path / "config.json").is_file() and any(path.glob("*.safetensors"))
        return path.is_file()

    @property
    def required_stages(self):
        return STAGES[:-1] if self.avatar.realtime else STAGES

    def estimate(self, selection, include_tts=None):
        if include_tts is None:
            include_tts = not self.avatar.realtime
        total = self.config["reserve_gib"]
        avatar_cost = self.avatar.estimated_gib if self.avatar.enabled else 0
        llm_cost = self.catalog[selection["llm"]]["vram_gib"]
        serial = self.config.get("low_memory", False) and self.catalog[selection["llm"]].get("backend") == "llama.cpp"
        total += max(avatar_cost, llm_cost) if serial else avatar_cost
        for model_id in selection.values():
            spec = self.catalog[model_id]
            if spec["stage"] == "tts" and not include_tts:
                continue
            if spec["stage"] == "llm" and serial:
                continue
            amount = spec["vram_gib"]
            total += amount
        return round(total, 2)

    def _validate_selection(self, selection):
        if set(selection) != set(STAGES):
            raise ModelError("需要分别选择 VAD、STT、LLM 和 TTS 模型")
        for stage, model_id in selection.items():
            if model_id not in self.catalog or self.catalog[model_id]["stage"] != stage:
                raise ModelError(f"无效的 {stage} 模型: {model_id}")
        estimate = self.estimate(selection)
        if estimate > self.config["budget_gib"]:
            raise ModelError(f"组合预估需要 {estimate:.2f} GiB，超过 {self.config['budget_gib']} GiB 预算。请选择更小的模型。")

    def gpu(self):
        timestamp, result = self._gpu_cache
        if time.monotonic() - timestamp < 5:
            return result
        executable = shutil.which("nvidia-smi")
        fallback = Path("C:/Windows/System32/nvidia-smi.exe")
        if not executable and fallback.is_file():
            executable = str(fallback)
        result = None
        if executable:
            try:
                output = subprocess.check_output(
                    [executable, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits", "--id=0"],
                    timeout=3, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                name, total, used = output.strip().split(",")
                result = {"name": name.strip(), "total_gib": round(float(total)/1024, 2), "used_gib": round(float(used)/1024, 2)}
            except (OSError, ValueError, subprocess.SubprocessError):
                log.warning("GPU telemetry unavailable")
        self._gpu_cache = (time.monotonic(), result)
        return result

    def status(self):
        local = self.catalog[self.selected["llm"]].get("backend") == "llama.cpp"
        return {
            "offline": local and not (self.avatar.enabled and self.avatar.provider == "vidu"),
            "low_memory": self.config.get("low_memory", False),
            "speech_local": not self.avatar.realtime, "device": self.device, "budget_gib": self.config["budget_gib"],
            "reserve_gib": self.config["reserve_gib"], "estimated_gib": self.estimate(self.selected),
            "gpu": self.gpu(), "selected": dict(self.selected), "loaded": list(self.loaded),
            "ready": all(stage in self.loaded for stage in self.required_stages), "switching": self.switching,
            "last_error": self.last_error,
            "avatar": self.avatar.status(),
            "llm_api": {"provider": "llama.cpp" if local else "MiniMax", "local": local,
                        "base_url": "http://127.0.0.1" if local else api_base_url(),
                        "key_configured": local or bool(os.environ.get("MINIMAX_API_KEY", "").strip()),
                        "connected": "llm" in self.loaded},
            "models": [{**m, "available": self.available(m)} for m in self.catalog.values()],
            "dependencies": {name: importlib.util.find_spec(module) is not None for name, module in
                             (("PyTorch", "torch"), ("Whisper", "whisper"), ("MiniMax HTTP", "httpx"), ("Qwen3-TTS", "qwen_tts"))},
        }

    def _release(self, stage):
        model = self.loaded.pop(stage, None)
        if model is not None and stage == "llm":
            model.close()
        del model
        gc.collect()
        import sys
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()

    def _check_memory(self):
        if not self.device.startswith("cuda"):
            return
        self._gpu_cache = (0, None)
        gpu = self.gpu()
        if gpu and gpu["used_gib"] > min(self.config["budget_gib"], gpu["total_gib"] - 0.5):
            raise ModelError("显卡实际占用超过安全预算。请释放其他 GPU 程序或选择更小的模型。")

    def switch(self, changes, llm_only=False, required_stages=None):
        with self.lock:
            candidate = {**self.selected, **changes}
            self._validate_selection(candidate)
            targets = ("llm",) if llm_only else (required_stages or self.required_stages)
            if not self.available(self.catalog[candidate["llm"]]):
                if self.catalog[candidate["llm"]].get("backend") == "llama.cpp":
                    raise ModelError("请先运行 scripts/prepare_llm.py，准备本地 GGUF 与 llama-server。")
                raise ModelError("请在本机设置 MINIMAX_API_KEY 并重启服务。密钥不会发送到浏览器。")
            missing = [str(self.path(self.catalog[candidate[s]])) for s in targets if s != "llm" and not self.available(self.catalog[candidate[s]])]
            if missing:
                raise ModelError("缺少本地模型文件，请按 README 放置权重：\n" + "\n".join(missing))
            previous = dict(self.selected)
            previously_loaded = set(self.loaded)
            changed = [s for s in targets if candidate[s] != previous[s] or s not in self.loaded]
            self.switching = True
            self.last_error = None
            try:
                # All replaced models are released BEFORE allocating any replacement.
                for stage in changed:
                    self._release(stage)
                for stage in changed:
                    log.info("Loading %s: %s", stage, candidate[stage])
                    self.loaded[stage] = self.loader(self.catalog[candidate[stage]])
                    if stage != "llm":
                        self._check_memory()
                self.selected = candidate
                temporary = self.config_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({**self.config, "selected": candidate}, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.config_path)
                log.info("Model switch complete; estimate %.2f GiB", self.estimate(candidate))
            except Exception as exc:
                # Drop partial replacements, then attempt to restore the previous set.
                self.last_error = str(exc)
                for stage in changed:
                    self._release(stage)
                for stage in changed:
                    if stage not in previously_loaded:
                        continue
                    try:
                        self.loaded[stage] = self.loader(self.catalog[previous[stage]])
                        if stage != "llm":
                            self._check_memory()
                    except Exception:
                        self._release(stage)
                        log.exception("Cannot restore %s", stage)
                raise ModelError(f"模型加载失败，已尝试恢复原配置：{exc}") from exc
            finally:
                self.switching = False
            return self.status()

    def _load(self, spec):
        if spec["stage"] == "llm":
            if spec.get("backend") == "llama.cpp":
                return LocalLLM(self.path(spec), low_memory=self.config.get("low_memory", False))
            from app.offline import enable_offline
            from urllib.parse import urlparse
            enable_offline(urlparse(api_base_url()).hostname)
            return MiniMaxLLM(model=spec["model"])
        import torch
        device = self.config["device"]
        if device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self._device = device
        if device == "cpu":
            torch.set_num_threads(min(4, os.cpu_count() or 1))
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ModelError("未检测到 CUDA 版 PyTorch / NVIDIA GPU。请安装 CUDA 推理环境；CPU 调试可将 config/models.json 的 device 改为 cpu。")
        path = str(self.path(spec).resolve())
        match spec["stage"]:
            case "vad":
                from vendor.s2s.vad_iterator import VADIterator
                model = torch.jit.load(path, map_location=device).eval()
                return VADIterator(model, min_silence_duration_ms=650, speech_pad_ms=160)
            case "stt":
                import whisper
                # A file path, never a model alias: load_model cannot trigger a download.
                return whisper.load_model(path, device=device)
            case "tts":
                from qwen_tts import Qwen3TTSModel
                return Qwen3TTSModel.from_pretrained(path, device_map=device,
                    dtype=torch.bfloat16 if device.startswith("cuda") else torch.float32,
                    attn_implementation="sdpa", local_files_only=True)

    def register(self, spec):
        with self.lock:
            path = Path(spec["path"]).expanduser()
            if str(path).startswith(("\\\\", "//")):
                raise ModelError("离线模式不支持网络共享路径，请使用本机磁盘。")
            if not path.is_absolute() or not path.exists():
                raise ModelError("请输入已存在的本地绝对路径，不支持网络地址或自动下载。")
            suffix = {"vad": ".jit", "stt": ".pt", "llm": ".gguf"}.get(spec["stage"])
            if suffix and (not path.is_file() or path.suffix.lower() != suffix):
                raise ModelError(f"该模型类型需要 {suffix} 文件")
            if spec["stage"] == "tts" and not self.available(spec):
                raise ModelError("TTS 目录需要 config.json 和完整 safetensors 权重")
            model = {**spec, "id": f"local-{uuid.uuid4().hex[:10]}", "detail": "自定义本地权重", "path": str(path.resolve())}
            if spec["stage"] == "llm":
                model["backend"] = "llama.cpp"
            updated = {**self.config, "selected": self.selected, "models": [*self.config["models"], model]}
            temporary = self.config_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.config_path)
            self.config = updated
            self.catalog[model["id"]] = model
            return model

    def close(self):
        with self.lock:
            self.avatar.unload()
            for stage in STAGES:
                self._release(stage)
