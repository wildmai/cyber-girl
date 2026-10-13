"""Local adaptation of speech-to-speech's threaded, queue-connected cascade."""

import base64
import io
import json
import logging
import queue
import re
import threading
import time
import wave
from contextlib import nullcontext
from collections import deque
from dataclasses import dataclass

import numpy as np
from pydantic import ValidationError

from app.models import ModelError
from app.local_llm import LocalLLM
from app.performance import PERFORMANCE_RULES, Performance
from vendor.s2s.thread_manager import ThreadManager

log = logging.getLogger(__name__)


@dataclass
class Work:
    generation: int
    value: object


class VisibleText:
    """Keep model reasoning and incomplete XML-like tags out of speech output."""

    def __init__(self):
        self.buffer = ""
        self.thinking = False

    def feed(self, text, final=False):
        self.buffer += text
        output = ""
        while self.buffer:
            tag = "</think>" if self.thinking else "<think>"
            index = self.buffer.find(tag)
            if index >= 0:
                if not self.thinking:
                    output += self.buffer[:index]
                self.buffer = self.buffer[index + len(tag):]
                self.thinking = not self.thinking
                continue
            if self.thinking:
                # Retain only a potential split closing tag, not the entire reasoning.
                self.buffer = self.buffer[-(len(tag) - 1):]
                break
            keep = 0
            if not final:
                for size in range(1, min(len(tag), len(self.buffer) + 1)):
                    if self.buffer.endswith(tag[:size]):
                        keep = size
            output += self.buffer[:-keep] if keep else self.buffer
            self.buffer = self.buffer[-keep:] if keep else ""
            break
        return output


class Stage:
    def __init__(self, owner, name, handler):
        self.owner = owner
        self.name = name
        self.handler = handler
        self.queue = queue.Queue(maxsize=64 if name == "vad" else 16)
        self.stop_event = owner.stop_event

    def run(self):
        while not self.stop_event.is_set():
            try:
                work = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if work.generation != self.owner.generation:
                continue
            try:
                self.handler(work)
            except Exception as exc:
                log.exception("%s stage failed", self.name)
                self.owner.fail(work.generation, str(exc))


class Pipeline:
    @property
    def realtime(self):
        return bool(getattr(getattr(self.manager, "avatar", None), "realtime", False))

    def __init__(self, manager, emit):
        self.manager = manager
        self.emit = emit
        self.stop_event = threading.Event()
        self.generation = 0
        self.history = deque(maxlen=12)
        self.speaker = "Vivian"
        self.voice_instruction = "语气温柔自然，像和熟悉的朋友聊天。"
        self.busy = False
        self.vad_generation = -1
        self.audio_generation = -1
        self.stages = {name: Stage(self, name, getattr(self, "_" + name)) for name in ("vad", "stt", "llm", "tts")}
        self.threads = ThreadManager(list(self.stages.values()))
        self.threads.start()

    def send(self, kind, generation=None, **data):
        current = self.generation if generation is None else generation
        if not self.stop_event.is_set() and current == self.generation:
            self.emit({"type": kind, "generation": current, **data})

    def enqueue(self, stage, work):
        if work.generation != self.generation:
            return
        try:
            self.stages[stage].queue.put_nowait(work)
        except queue.Full:
            self.fail(work.generation, "处理队列已满，请稍后重试。")

    def interrupt(self):
        self.generation += 1
        self.busy = False
        avatar = getattr(self.manager, "avatar", None)
        if avatar and getattr(avatar, "provider", None) == "vidu":
            avatar.vidu.interrupt()
        model = self.manager.loaded.get("llm")
        if model is not None and hasattr(model, "cancel"):
            model.cancel()
        for stage in self.stages.values():
            while True:
                try:
                    stage.queue.get_nowait()
                except queue.Empty:
                    break
        self.send("interrupted")

    def fail(self, generation, message):
        if generation != self.generation:
            return
        self.interrupt()
        self.send("error", message=message)

    def text(self, value):
        value = value.strip()
        if not value or len(value) > 2000:
            raise ModelError("请输入 1 到 2000 字的消息。")
        if "llm" not in self.manager.loaded:
            raise ModelError("对话模型尚未连接，请在模型设置中准备并连接所选模型。")
        if self.realtime and not self.manager.avatar.vidu.status()["connected"]:
            raise ModelError("实时模式需要先在模型设置中连接 Vidu S2 数字人。")
        self.interrupt()
        self.busy = True
        self.touch_avatar()
        self.send("user", text=value)
        self.enqueue("llm", Work(self.generation, value))

    def audio(self, raw):
        if len(raw) != 1024:
            raise ModelError("麦克风帧必须为 16 kHz、单声道、512 samples 的 PCM16。")
        if self.busy:
            return
        required = ("vad", "stt", "llm") if self.realtime else self.stages
        if not all(s in self.manager.loaded for s in required):
            raise ModelError("请在模型设置中加载当前模式需要的本地语音模型。")
        if self.audio_generation != self.generation:
            self.audio_generation = self.generation
            log.info("Microphone PCM received; generation=%d", self.generation)
        self.enqueue("vad", Work(self.generation, raw))

    def touch_avatar(self):
        avatar = getattr(self.manager, "avatar", None)
        if avatar and hasattr(getattr(avatar, "vidu", None), "touch"):
            avatar.vidu.touch()

    def _vad(self, work):
        import torch
        with getattr(self.manager, "vad_lock", self.manager.lock):
            if work.generation != self.generation:
                return
            vad = self.manager.loaded["vad"]
            if self.vad_generation != work.generation:
                vad.reset_states()
                self.vad_generation = work.generation
            samples = np.frombuffer(work.value, dtype="<i2").astype(np.float32) / 32768
            tensor = torch.from_numpy(samples).to(self.manager.device)
            was_speaking = vad.triggered
            utterance = vad(tensor)
            if vad.triggered:
                self.touch_avatar()
            if vad.triggered and not was_speaking:
                log.info("VAD speech started; generation=%d", work.generation)
                self.send("state", work.generation, state="hearing")
            # Bound long monologues to 20 seconds so an open microphone cannot grow memory forever.
            if vad.triggered and vad.active_speech_samples >= 16000 * 20:
                utterance = vad.speech_buffer()
                vad.reset_states()
            if utterance:
                self.touch_avatar()
                self.busy = True
                audio = torch.cat(utterance).cpu().numpy()
                log.info("VAD utterance complete; seconds=%.2f", len(audio) / 16000)
                self.enqueue("stt", Work(work.generation, audio))

    def _stt(self, work):
        self.send("state", work.generation, state="transcribing")
        start = time.perf_counter()
        with self.manager.lock:
            if work.generation != self.generation:
                return
            result = self.manager.loaded["stt"].transcribe(work.value, language="zh",
                fp16=self.manager.device.startswith("cuda"), condition_on_previous_text=False)
        text = result["text"].strip()
        log.info("STT complete in %.3fs", time.perf_counter() - start)
        if text:
            self.send("user", work.generation, text=text)
            self.enqueue("llm", Work(work.generation, text))
        else:
            self.busy = False
            self.send("done", work.generation)

    def _llm(self, work):
        start = time.perf_counter()
        visible = VisibleText()
        answer = ""
        sentence = ""
        with getattr(self.manager, "llm_lock", self.manager.lock):
            model = self.manager.loaded["llm"]
            realtime = self.realtime
            with_speech = realtime or "tts" in self.manager.loaded
        self.send("state", work.generation, state="thinking", speech=with_speech)
        prompt = self.manager.persona.system_prompt()
        if realtime:
            prompt += "\n\n仅在本实时模式下，回复格式改为：" + PERFORMANCE_RULES
        history = []
        for message in self.history:
            content = message["content"]
            if realtime and message["role"] == "assistant":
                content = json.dumps({"speech": content, "action": message.get("action", "")}, ensure_ascii=False)
            history.append({"role": message["role"], "content": content})
        messages = [{"role": "system", "content": prompt}, *history,
                    {"role": "user", "content": work.value}]
        serial = isinstance(model, LocalLLM) and self.manager.config.get("low_memory", False)
        pending_speech = []

        def consume(text):
            nonlocal answer, sentence
            if not text:
                return
            answer += text
            if realtime:
                return  # Validate the plan before exposing any speech or metadata.
            sentence += text
            self.send("token", work.generation, text=text)
            while True:
                end = re.search(r"[。！？!?；;\n]", sentence)
                length = end.end() if end else (100 if len(sentence) >= 100 else 0)
                if not length:
                    break
                chunk, sentence = sentence[:length], sentence[length:]
                if chunk.strip() and with_speech:
                    if serial:
                        pending_speech.append(chunk.strip())
                    else:
                        self.enqueue("tts", Work(work.generation, chunk.strip()))

        with getattr(self.manager, "llm_lock", self.manager.lock):
            if work.generation != self.generation:
                return
            stream = model.create_chat_completion(messages=messages,
                max_tokens=2048, temperature=0.8, top_p=0.9, stream=True)
        # Network generation does not hold the GPU lock: TTS may synthesize each
        # completed sentence while MiniMax is still streaming subsequent text.
        with self.manager.lock if serial else nullcontext():
            if serial and getattr(self.manager, "avatar", None):
                self.manager.avatar.release_local()
            try:
                for chunk in stream:
                    if work.generation != self.generation or self.stop_event.is_set():
                        return
                    consume(visible.feed(chunk["choices"][0].get("delta", {}).get("content", "") or ""))
                consume(visible.feed("", final=True))
            finally:
                stream.close()
        for chunk in pending_speech:
            self.enqueue("tts", Work(work.generation, chunk))
        if realtime:
            try:
                performance = Performance.model_validate_json(answer)
            except ValidationError:
                raise ModelError("本轮台词与表情规划格式异常，请重试。") from None
            answer = performance.speech
            self.send("token", work.generation, text=answer)
            self.enqueue("tts", Work(work.generation, performance))
            log.info("Realtime performance planned; speech_chars=%d action_chars=%d",
                     len(answer), len(performance.action))
        if sentence.strip() and with_speech:
            self.enqueue("tts", Work(work.generation, sentence.strip()))
        if not answer.strip():
            raise ModelError("模型没有返回可朗读的内容，请重试或切换模型。")
        if work.generation == self.generation:
            assistant = {"role": "assistant", "content": answer}
            if realtime:
                assistant["action"] = performance.action
            self.history.extend(({"role": "user", "content": work.value}, assistant))
        self.send("assistant", work.generation, text=answer)
        if with_speech:
            self.enqueue("tts", Work(work.generation, None))
        else:
            self.busy = False
            self.send("done", work.generation)
        log.info("LLM complete in %.3fs", time.perf_counter() - start)

    def _tts(self, work):
        if work.value is None:
            self.busy = False
            self.send("done", work.generation)
            return
        if self.realtime:
            self.send("state", work.generation, state="cloud_synthesizing")
            vidu = self.manager.avatar.vidu
            if hasattr(vidu, "wait_ready") and not vidu.wait_ready(
                    lambda: work.generation == self.generation and not self.stop_event.is_set()):
                return
            self.manager.avatar.vidu.speak(work.value.speech,
                lambda: work.generation == self.generation and not self.stop_event.is_set(),
                lambda request_id: self.send("vidu_speaking", work.generation,
                    request_id=request_id, text=work.value.speech),
                action=work.value.action)
            return
        self.send("state", work.generation, state="synthesizing")
        start = time.perf_counter()
        log.info("TTS synthesis started on %s", self.manager.device)
        with self.manager.lock:
            if work.generation != self.generation:
                return
            wavs, sample_rate = self.manager.loaded["tts"].generate_custom_voice(
                text=work.value, language="Chinese", speaker=self.speaker,
                instruct=self.voice_instruction, max_new_tokens=1024)
        samples = (np.clip(np.asarray(wavs[0]), -1, 1) * 32767).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(samples.tobytes())
        log.info("TTS complete in %.3fs", time.perf_counter() - start)
        avatar = getattr(self.manager, "avatar", None)
        if avatar and avatar.enabled:
            if getattr(avatar, "provider", None) == "vidu":
                try:
                    avatar.vidu.stream(output.getvalue(), work.value,
                        lambda: work.generation == self.generation and not self.stop_event.is_set(),
                        lambda: self.send("vidu_speaking", work.generation,
                            text=work.value, duration=len(samples) / sample_rate))
                    return
                except Exception as exc:
                    log.warning("Vidu reply failed: %s", exc)
                    self.send("warning", work.generation, message=str(exc))
                    self.send("vidu_stopped", work.generation)
                    if getattr(exc, "audio_sent", False):
                        return
                    self.send("audio", work.generation, text=work.value,
                        data=base64.b64encode(output.getvalue()).decode("ascii"))
                    return
            last_progress = -1

            def progress(percent):
                nonlocal last_progress
                if percent == 0 or percent >= last_progress + 5 or percent == 100:
                    last_progress = percent
                    self.send("state", work.generation, state="animating", progress=percent)

            try:
                url = avatar.render(output.getvalue(),
                    lambda: work.generation == self.generation and not self.stop_event.is_set(), progress)
                if url:
                    self.send("avatar", work.generation, text=work.value,
                        url=url, audio=base64.b64encode(output.getvalue()).decode("ascii"))
                    return
            except Exception:
                log.exception("Avatar failed; retaining local voice playback")
                self.send("warning", work.generation, message="人物口型生成失败，改为播放声音。请在模型设置中查看原因。")
        self.send("audio", work.generation, text=work.value,
            data=base64.b64encode(output.getvalue()).decode("ascii"))

    def close(self):
        self.interrupt()
        self.stop_event.set()
        self.threads.stop()
