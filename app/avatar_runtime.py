"""MuseTalk 1.5 inference, with local photo detection and cached face materials."""

import logging
import subprocess

import cv2
import numpy as np
import torch
from transformers import WhisperModel

from vendor.musetalk_core.models.unet import PositionalEncoding, UNet
from vendor.musetalk_core.models.vae import VAE
from vendor.musetalk_core.utils.audio_processor import AudioProcessor
from vendor.musetalk_core.utils.blending import get_image_blending, get_image_prepare_material
from vendor.musetalk_core.utils.face_parsing import FaceParsing

log = logging.getLogger(__name__)


class Cancelled(RuntimeError):
    pass


class MuseTalkRuntime:
    def __init__(self, model_dir, device):
        self.device = torch.device(device)
        self.dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        self.vae = VAE(str(model_dir / "sd-vae"), use_float16=self.device.type == "cuda", device=device)
        self.unet = UNet(str(model_dir / "musetalkV15/musetalk.json"),
                         str(model_dir / "musetalkV15/unet.pth"),
                         use_float16=self.device.type == "cuda", device=self.device)
        self.unet.model.eval().requires_grad_(False)
        if self.device.type == "cpu":
            # The FP32 UNet alone has 3.4 GB of weights. Quantize attention linears
            # in place on CPU to reduce paging on 16 GB hosts; CUDA stays FP16.
            self.unet.model = torch.ao.quantization.quantize_dynamic(
                self.unet.model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
        self.vae.vae.eval().requires_grad_(False)
        self.pe = PositionalEncoding().to(device=self.device, dtype=self.dtype)
        self.audio = AudioProcessor(str(model_dir / "whisper"))
        self.whisper = WhisperModel.from_pretrained(str(model_dir / "whisper"), local_files_only=True,
            dtype=self.dtype).to(self.device).eval().requires_grad_(False)
        # Parsing runs once per photo on CPU, avoiding a resident GPU allocation.
        self.parser = FaceParsing(left_cheek_width=90, right_cheek_width=90,
                                  model_dir=str(model_dir / "face-parse-bisent"), device="cpu")
        self.material = None
        self.photo_key = None

    def prepare_photo(self, photo):
        key = (str(photo), photo.stat().st_mtime_ns)
        if self.photo_key == key:
            return self.material
        frame = cv2.imdecode(np.fromfile(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法读取人物图片。")
        # Bound full-frame encoding cost, preserving the original aspect ratio.
        scale = min(1, 1280 / max(frame.shape[:2]))
        frame = cv2.resize(frame, (int(frame.shape[1] * scale) // 2 * 2,
                                   int(frame.shape[0] * scale) // 2 * 2))
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = detector.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(64, 64))
        if not len(faces):
            raise ValueError("未检测到清晰正脸，请更换无遮挡的正面人物照片。")
        x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
        # Include chin margin as in v1.5; the face parser protects hair and the hand.
        box = (int(x), max(0, int(y - h * .08)), int(min(frame.shape[1], x + w)),
               int(min(frame.shape[0], y + h + h * .12)))
        x1, y1, x2, y2 = box
        with torch.inference_mode():
            latent = self.vae.get_latents_for_unet(cv2.resize(frame[y1:y2, x1:x2], (256, 256)))
            mask, crop = get_image_prepare_material(frame, box, fp=self.parser, mode="jaw")
        self.material = frame, box, latent.to(self.dtype), mask, crop
        self.photo_key = key
        log.info("MuseTalk photo prepared: face %s, frame %s", box, frame.shape)
        return self.material

    @torch.inference_mode()
    def render(self, photo, audio, output, ffmpeg, valid, progress, fps=25):
        def check():
            if not valid():
                raise Cancelled()

        check()
        frame, box, latent, mask, crop = self.prepare_photo(photo)
        check()
        features, length = self.audio.get_audio_feature(str(audio), weight_dtype=self.dtype)
        prompts = self.audio.get_whisper_chunk(features, self.device, self.dtype,
                                               self.whisper, length, fps=fps)
        if len(prompts) == 0:
            raise ValueError("语音太短，无法生成人物口型。")
        silent = output.with_suffix(".silent.mp4")
        writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                (frame.shape[1], frame.shape[0]))
        if not writer.isOpened():
            raise RuntimeError("无法初始化本地视频编码器。")
        batch = 2 if self.device.type == "cuda" else 1
        x1, y1, x2, y2 = box
        try:
            for start in range(0, len(prompts), batch):
                check()
                conditioning = self.pe(prompts[start:start + batch].to(self.device, self.dtype))
                inputs = latent.repeat(len(conditioning), 1, 1, 1)
                prediction = self.unet.model(inputs, torch.tensor([0], device=self.device),
                                             encoder_hidden_states=conditioning).sample
                faces = self.vae.decode_latents(prediction)
                for face in faces:
                    face = cv2.resize(face, (x2 - x1, y2 - y1))
                    writer.write(get_image_blending(frame, face, box, mask, crop))
                progress(min(99, int((start + len(faces)) / len(prompts) * 100)))
            check()
        finally:
            writer.release()
        # One muxed A/V timeline, with faststart for local browser playback.
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent),
            "-i", str(audio), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-vf", "tpad=stop_mode=clone:stop_duration=0.1", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", str(output)], capture_output=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        silent.unlink(missing_ok=True)
        if result.returncode:
            raise RuntimeError("本地视频封装失败：" + result.stderr.decode(errors="replace")[-300:])
        check()
        progress(100)
