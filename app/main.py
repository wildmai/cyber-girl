import asyncio
import contextlib
import json
import logging
import mimetypes
import os
import re
from typing import Literal
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

import anyio
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.offline import enable_offline
from app.minimax import api_base_url
from app.vidu import API_HOST, ViduError
from app.deployment import network_config

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from app.models import ROOT, ModelError, ModelManager  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.persona import DEFAULT_PERSONA, PersonaConfig  # noqa: E402
from app.sessions import SessionModels, new_token  # noqa: E402

manager = ModelManager()
enable_offline(urlparse(api_base_url()).hostname
               if manager.catalog[manager.selected["llm"]].get("backend") == "minimax" else None)
sessions = {}
opening_dialogues = 0
MAX_DIALOGUES = int(os.environ.get("SAIBOGIRL_MAX_DIALOGUES", "8"))
MAX_AVATARS = int(os.environ.get("SAIBOGIRL_MAX_AVATARS", "3"))
avatar_connect_lock = asyncio.Lock()
PUBLIC_ORIGIN, ALLOWED_HOSTS = network_config()


@asynccontextmanager
async def lifespan(app):
    (ROOT / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(ROOT / "logs/app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    logging.info("Companion started; local speech with optional cloud avatar")
    try:
        await asyncio.to_thread(manager.switch, {}, True)
    except ModelError as exc:
        logging.warning("LLM not ready: %s", exc)
    else:
        if os.environ.get("SAIBOGIRL_DESKTOP") == "1" or os.environ.get("SAIBOGIRL_AUTOLOAD_SPEECH") == "1":
            try:
                await asyncio.to_thread(manager.switch, {})
            except ModelError as exc:
                logging.warning("Desktop speech models not ready: %s", exc)
    yield
    for pipeline in list(sessions.values()):
        await asyncio.to_thread(pipeline.close)
        await asyncio.to_thread(pipeline.manager.close)
    sessions.clear()
    await asyncio.to_thread(manager.close)
    logging.getLogger().removeHandler(handler)
    handler.close()


app = FastAPI(title="赛博女友 · 小满", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def local_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    expected_origin = PUBLIC_ORIGIN or str(request.base_url).rstrip("/")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != expected_origin:
        return JSONResponse(status_code=403, content={"detail": "仅允许本地页面请求"})
    response = await call_next(request)
    if request.url.path in ("/", "/app.js", "/vidu.js", "/styles.css"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
        "media-src 'self' blob: data:; connect-src 'self' https://*.qcloud.com wss://*.qcloud.com "
        "https://*.myqcloud.com wss://*.myqcloud.com https://*.tencentcloudapi.com wss://*.tencentcloudapi.com "
        "https://*.cloud-rtc.com wss://*.cloud-rtc.com https://*.rtc-web.com wss://*.rtc-web.com "
        "https://*.rtc-web.io wss://*.rtc-web.io "
        "https://*.qq.com wss://*.qq.com; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    return response


@app.get("/api/status")
async def status(request: Request):
    result = await asyncio.to_thread(request_models(request).status)
    result["capacity"] = {"dialogues": len(sessions), "max_dialogues": MAX_DIALOGUES, "max_avatars": MAX_AVATARS}
    return result


def request_pipeline(request):
    return sessions.get(request.headers.get("x-conversation-token"))


def request_models(request):
    pipeline = request_pipeline(request)
    if pipeline:
        return pipeline.manager
    if request.headers.get("x-conversation-token") or (request.method != "GET" and (PUBLIC_ORIGIN or sessions)):
        raise HTTPException(409, "对话连接已失效，请刷新页面后重试。")
    return manager


def exclusive_models(request):
    current = request_models(request)
    if len(sessions) > (1 if request_pipeline(request) else 0):
        raise HTTPException(409, "其他用户正在使用共享语音模型，暂时不能切换模型。")
    return current


@app.get("/api/persona")
async def persona(request: Request):
    return JSONResponse(request_models(request).persona.status(), headers={"Cache-Control": "no-store"})


def save_persona(config, request):
    current = request_models(request)
    try:
        result = current.persona.save(config)
    except OSError as exc:
        logging.exception("Could not save LLM persona")
        raise HTTPException(503, "人设保存失败，请检查本机配置目录的写入权限。") from exc
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
        pipeline.history.clear()
    if current.avatar.realtime:
        # Doubao sessions do not support session_update; reconnect with the new persona.
        current.avatar.vidu.close()
    return result


@app.put("/api/persona")
async def configure_persona(body: PersonaConfig, request: Request):
    return await asyncio.to_thread(save_persona, body, request)


@app.delete("/api/persona")
async def reset_persona(request: Request):
    return await asyncio.to_thread(save_persona, PersonaConfig(prompt=DEFAULT_PERSONA), request)


class AvatarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    provider: Literal["musetalk", "vidu"] | None = None
    mode: Literal["standard", "realtime"] | None = None


@app.post("/api/avatar")
async def configure_avatar(body: AvatarRequest, request: Request):
    current = request_models(request)
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    try:
        await asyncio.to_thread(current.avatar.set_enabled, body.enabled, body.provider, body.mode)
        return current.status()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/avatar/vidu/connect")
async def connect_vidu(request: Request):
    pipeline = request_pipeline(request)
    if not pipeline:
        raise HTTPException(409, "请先打开本地对话页面。")
    current = pipeline.manager
    if not current.avatar.enabled or current.avatar.provider != "vidu":
        raise HTTPException(409, "请先选择并开启 Vidu S2 数字人。")
    enable_offline(API_HOST)
    async with avatar_connect_lock:
        vidu = current.avatar.vidu
        if vidu.connecting or vidu.ws is not None:
            raise HTTPException(409, "数字人已连接或正在连接，请勿重复创建会话。")
        active = sum(p.manager.avatar.vidu.connecting or p.manager.avatar.vidu.ws is not None for p in sessions.values())
        if active >= MAX_AVATARS:
            vidu.error = "数字人同时在线人数已达上限，请稍后重试。"
            raise HTTPException(429, "数字人同时在线人数已达上限，请稍后重试。本次未创建计费会话。")
        vidu.connecting = True
    try:
        rtc = await asyncio.to_thread(vidu.open, current.avatar.portrait,
                                      current.avatar.config["mode"], current.persona.status()["prompt"])
        if pipeline.stop_event.is_set() or await request.is_disconnected():
            await asyncio.to_thread(vidu.close)
            raise HTTPException(409, "对话页面已断开，数字人会话已结束。")
        return JSONResponse({"rtc": rtc, "live_id": vidu.live_id, "mode": vidu.mode},
                            headers={"Cache-Control": "no-store"})
    except ViduError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        vidu.connecting = False


@app.post("/api/avatar/vidu/disconnect")
async def disconnect_vidu(request: Request):
    current = request_models(request)
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    await asyncio.to_thread(current.avatar.vidu.close)
    return current.status()


@app.get("/api/avatar/clips/{name}")
async def avatar_clip(name: str, request: Request):
    path = request_models(request).avatar.clip_path(name)
    if path is None:
        raise HTTPException(404, "人物视频不存在或已过期")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@app.get("/api/avatar/portrait")
async def avatar_portrait(request: Request):
    return FileResponse(request_models(request).avatar.portrait, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.put("/api/avatar/portrait")
async def change_portrait(request: Request):
    current = request_models(request)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(413, "图片大小不能超过 15 MB")
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    try:
        return await asyncio.to_thread(current.avatar.set_portrait, bytes(content))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/avatar/portrait")
async def restore_portrait(request: Request):
    current = request_models(request)
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    return await asyncio.to_thread(current.avatar.set_portrait)


class SwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection: dict[str, str] = Field(default_factory=dict)


@app.post("/api/models/switch")
async def switch(body: SwitchRequest, request: Request):
    current = exclusive_models(request)
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    try:
        result = await asyncio.to_thread(current.switch, body.selection)
        if pipeline:
            pipeline.vad_generation = -1
        return result
    except ModelError as exc:
        raise HTTPException(409, str(exc)) from exc


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(pattern="^(vad|stt|llm|tts)$")
    name: str = Field(min_length=1, max_length=60)
    path: str = Field(min_length=1, max_length=1024)
    vram_gib: float = Field(ge=0.1, le=14, allow_inf_nan=False)


@app.post("/api/models/register")
async def register(body: RegisterRequest, request: Request):
    current = exclusive_models(request)
    try:
        return await asyncio.to_thread(current.register, body.model_dump())
    except ModelError as exc:
        raise HTTPException(409, str(exc)) from exc


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str


@app.post("/api/llm/connect")
async def connect_llm(body: LLMRequest, request: Request):
    current = request_models(request)
    if (current.catalog.get(body.model_id, {}).get("backend") == "llama.cpp"
            or current.catalog[current.shared.selected["llm"] if isinstance(current, SessionModels)
                               else current.selected["llm"]].get("backend") == "llama.cpp"):
        current = exclusive_models(request)
    pipeline = request_pipeline(request)
    if pipeline:
        pipeline.interrupt()
    try:
        if isinstance(current, SessionModels):
            return await asyncio.to_thread(current.connect_llm, body.model_id)
        return await asyncio.to_thread(current.switch, {"llm": body.model_id}, True)
    except ModelError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.websocket("/ws")
async def conversation(socket: WebSocket):
    global opening_dialogues
    origin = socket.headers.get("origin")
    host = socket.headers.get("host")
    if origin != (PUBLIC_ORIGIN or f"http://{host}"):
        await socket.close(code=1008)
        return
    await socket.accept()
    if len(sessions) + opening_dialogues >= MAX_DIALOGUES:
        await socket.send_json({"type": "error", "message": "当前同时在线人数已达上限，请稍后重试。"})
        await socket.close(code=1013)
        return
    loop = asyncio.get_running_loop()
    outgoing = asyncio.Queue(maxsize=256)

    def enqueue(event):
        if outgoing.full():
            pipeline.interrupt()
            # Clear stale output before reporting backpressure.
            while not outgoing.empty():
                outgoing.get_nowait()
            outgoing.put_nowait({"type": "error", "generation": pipeline.generation,
                                "message": "播放连接过慢，已停止当前回复。"})
        else:
            outgoing.put_nowait(event)

    token = new_token()
    profile = socket.cookies.get("xiaoman_profile", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", profile):
        profile = new_token()
    opening_dialogues += 1
    try:
        current = await asyncio.to_thread(SessionModels, manager, profile)
    except Exception:
        logging.exception("Dialogue initialization failed")
        await socket.close(code=1011)
        return
    finally:
        opening_dialogues -= 1
    pipeline = Pipeline(current, lambda event: loop.call_soon_threadsafe(enqueue, event))
    sessions[token] = pipeline
    current.avatar.vidu.on_expire = lambda reason: pipeline.send(
        "avatar_idle" if current.avatar.vidu.expired_idle else "avatar_closed", message=reason)
    current.avatar.vidu.is_busy = lambda: pipeline.busy
    logging.info("Dialogue opened; active=%d", len(sessions))

    async def sender():
        while True:
            event = await outgoing.get()
            if event.get("generation", pipeline.generation) == pipeline.generation:
                await socket.send_json(event)

    send_task = asyncio.create_task(sender())
    await socket.send_json({"type": "connected", "generation": 0, "session_token": token})
    try:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                break
            try:
                if message.get("bytes") is not None:
                    pipeline.audio(message["bytes"])
                    continue
                raw = message.get("text", "")
                if len(raw) > 10000:
                    raise ModelError("消息过长")
                data = json.loads(raw)
                match data.get("type"):
                    case "text":
                        pipeline.text(str(data.get("text", "")))
                    case "interrupt":
                        pipeline.interrupt()
                    case "reset":
                        pipeline.interrupt()
                        pipeline.history.clear()
                    case "voice":
                        if data.get("speaker") not in ("Vivian", "Serena", "Ono_Anna", "Sohee"):
                            raise ModelError("不支持的预设音色")
                        pipeline.speaker = data["speaker"]
                    case "vidu_ready":
                        current.avatar.vidu.ready(str(data.get("live_id", "")))
                    case "vidu_playback_done":
                        current.avatar.vidu.playback_done(str(data.get("live_id", "")), str(data.get("request_id", "")))
                    case "avatar_connect_failed":
                        pipeline.fail(pipeline.generation, "数字人未能连接，请检查连接提示后重试。")
                    case _:
                        raise ModelError("不支持的对话消息")
            except (ModelError, ValueError, AttributeError) as exc:
                pipeline.send("error", message=str(exc))
    except WebSocketDisconnect:
        pass
    finally:
        sessions.pop(token, None)
        logging.info("Dialogue closed; active=%d", len(sessions))
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
        # Client disconnect may cancel the route while cleanup is awaiting a thread.
        with anyio.CancelScope(shield=True):
            await asyncio.to_thread(pipeline.close)
            await asyncio.to_thread(current.close)


@app.get("/")
async def index(request: Request):
    response = FileResponse(ROOT / "web/index.html")
    profile = request.cookies.get("xiaoman_profile", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", profile):
        response.set_cookie("xiaoman_profile", new_token(), httponly=True,
                            secure=bool(PUBLIC_ORIGIN), samesite="strict", max_age=31536000)
    return response


app.mount("/", StaticFiles(directory=ROOT / "web"), name="web")
