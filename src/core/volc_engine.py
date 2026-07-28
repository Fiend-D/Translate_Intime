"""
火山引擎 AST 2.0 实时同传客户端。

- WebSocket: wss://openspeech.bytedance.com/api/v4/ast/v2/translate
- 协议: Protobuf TranslateRequest / TranslateResponse
- 鉴权: 新版 X-Api-Key；旧版 X-Api-App-Id/X-Api-App-Key + X-Api-Access-Key
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import aiohttp

# Ensure vendored protobuf package is importable
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from python_protogen.common.events_pb2 import Type
from python_protogen.products.understanding.ast.ast_service_pb2 import (
    TranslateRequest,
    TranslateResponse,
)
from src.utils.logger import logger

WS_URL = "wss://openspeech.bytedance.com/api/v4/ast/v2/translate"
DEFAULT_RESOURCE_ID = "volc.service_type.10053"

# 火山建议约 80ms 一包；长时间不发包会 SessionFailed: waiting next packet timeout
_FRAME_MS = 80
_FRAME_SAMPLES = int(16000 * _FRAME_MS / 1000)  # 1280
_FRAME_BYTES = _FRAME_SAMPLES * 2  # 2560 bytes int16 mono
_SILENCE_FRAME = b"\x00" * _FRAME_BYTES
_AUDIO_QUEUE_MAX = 50  # ~1s of 80ms-ish chunks; drop-oldest when full


def drop_oldest_put(queue: asyncio.Queue, item: bytes | None) -> None:
    """Non-blocking put that drops the oldest item when the queue is full."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(item)

# s2s：空字符串 = 复刻原音色；公版音色仅目标语为 zh/en 时可用
VOLC_VOICE_OPTIONS: list[tuple[str, str]] = [
    ("", "原音色（复刻）"),
    ("zh_female_vv_uranus_bigtts", "公版女声 VV"),
    ("zh_male_jingqiangkanye_emo_mars_bigtts", "公版男声 劲强"),
]

Mode = Literal["s2s", "s2t"]
AuthMode = Literal["api_key", "legacy"]


def resolve_volc_credentials(
    api_key: str = "",
    access_token: str = "",
) -> tuple[str, str, AuthMode]:
    """Resolve credentials from args or environment."""
    key = (
        api_key
        or os.environ.get("VOLC_API_KEY", "")
        or os.environ.get("VOLC_APP_KEY", "")
        or os.environ.get("VOLC_APP_ID", "")
        or os.environ.get("VOLC_APPID", "")
    ).strip()
    token = (
        access_token
        or os.environ.get("VOLC_ACCESS_TOKEN", "")
        or os.environ.get("VOLC_TOKEN", "")
    ).strip()

    if not key:
        return "", "", "api_key"

    # Numeric App ID + token => legacy; UUID/string API key => new
    if key.isdigit() and token:
        return key, token, "legacy"
    if token and key.isdigit():
        return key, token, "legacy"
    # Prefer API key mode when token empty or key looks like UUID/string
    return key, token, "api_key"


class VolcASTClient:
    """One AST WebSocket session for a single translation direction."""

    def __init__(
        self,
        *,
        api_key: str,
        access_token: str = "",
        resource_id: str = DEFAULT_RESOURCE_ID,
        source_language: str = "zh",
        target_language: str = "en",
        mode: Mode = "s2t",
        sample_rate: int = 16000,
        speaker_id: str = "",
        speech_rate: int = 0,
        hotwords: list[str] | None = None,
        glossary: dict[str, str] | None = None,
        on_source_text: Callable[[str, bool], None] | None = None,
        on_translated_text: Callable[[str, bool], None] | None = None,
        on_audio: Callable[[bytes], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_usage: Callable[[dict], None] | None = None,
        session_rotate_minutes: int = 12,
        should_defer_rotate: Callable[[], bool] | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.resource_id = resource_id or DEFAULT_RESOURCE_ID
        self.source_language = source_language
        self.target_language = target_language
        self.mode: Mode = mode
        self.sample_rate = sample_rate
        self.speaker_id = (speaker_id or "").strip()
        self.speech_rate = max(-50, min(100, int(speech_rate)))
        self.hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]
        self.glossary = {
            str(k).strip(): str(v).strip()
            for k, v in (glossary or {}).items()
            if str(k).strip() and str(v).strip()
        }

        self.on_source_text = on_source_text
        self.on_translated_text = on_translated_text
        self.on_audio = on_audio
        self.on_error = on_error
        self.on_status = on_status
        self.on_usage = on_usage
        self.session_rotate_minutes = max(0, int(session_rotate_minutes or 0))
        self.should_defer_rotate = should_defer_rotate

        self.session_id = str(uuid.uuid4())
        self.connection_id = str(uuid.uuid4())

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._audio_queue: asyncio.Queue[bytes | None] | None = None
        self._sender_task: asyncio.Task | None = None
        self._receiver_task: asyncio.Task | None = None
        self._rotate_task: asyncio.Task | None = None
        self._session_started = asyncio.Event()
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect = 5
        self._reconnecting = False
        self._session_started_at = 0.0

        self._source_buf = ""
        self._translation_buf = ""

    @property
    def is_running(self) -> bool:
        return self._running

    async def connect(self) -> bool:
        """Connect, StartSession, wait for SessionStarted."""
        if not self.api_key:
            self._emit_error("火山引擎缺少 API Key / App ID")
            return False

        key, token, auth_mode = resolve_volc_credentials(self.api_key, self.access_token)
        self.api_key, self.access_token = key, token

        header_candidates = self._header_candidates(auth_mode)
        last_error: Exception | None = None

        self._http = aiohttp.ClientSession()
        for idx, headers in enumerate(header_candidates):
            try:
                self._ws = await self._http.ws_connect(
                    WS_URL,
                    headers=headers,
                    max_msg_size=16 * 1024 * 1024,
                    heartbeat=None,
                    compress=0,
                    autoping=True,
                )
                log_id = self._extract_log_id()
                used = [h for h in headers if h.startswith("X-Api-")]
                self._emit_status(
                    f"火山已连接 ({'/'.join(used)}; logid={log_id})"
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning(f"火山鉴权候选 {idx + 1}/{len(header_candidates)} 失败: {exc}")
                self._ws = None
                continue
        else:
            await self._close_http()
            self._emit_error(f"火山引擎连接失败: {last_error}")
            return False

        assert self._ws is not None
        if self._audio_queue is None:
            self._audio_queue = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        self._session_started.clear()
        self._running = True

        await self._ws.send_bytes(self._build_start_request().SerializeToString())
        self._receiver_task = asyncio.create_task(self._receive_loop())

        try:
            await asyncio.wait_for(self._session_started.wait(), timeout=8.0)
        except TimeoutError:
            self._emit_error("等待 SessionStarted 超时")
            await self._abort_connect_attempt(keep_alive=self._reconnecting)
            return False

        # Warm the send pipeline with one silence frame before real audio
        with contextlib.suppress(Exception):
            await self._send_frame(_SILENCE_FRAME)

        self._session_started_at = time.time()
        self._sender_task = asyncio.create_task(self._send_loop())
        if self.session_rotate_minutes > 0 and (
            self._rotate_task is None or self._rotate_task.done()
        ):
            self._rotate_task = asyncio.create_task(self._rotate_loop())
        self._emit_status(f"火山会话已启动 mode={self.mode} {self.source_language}->{self.target_language}")
        return True

    async def _abort_connect_attempt(self, *, keep_alive: bool) -> None:
        """Tear down a failed connect without always ending the client."""
        if self._sender_task is not None:
            self._sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender_task
            self._sender_task = None

        if self._receiver_task is not None:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        await self._close_http()

        if not keep_alive:
            self._running = False
            self._audio_queue = None
            self._session_started.clear()

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if not self._running or self._audio_queue is None:
            return
        if not pcm_bytes:
            return
        drop_oldest_put(self._audio_queue, pcm_bytes)

    async def close(self) -> None:
        self._running = False
        self._reconnecting = False
        if self._rotate_task is not None:
            self._rotate_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rotate_task
            self._rotate_task = None
        if self._audio_queue is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self._audio_queue.put_nowait(None)

        if self._sender_task is not None:
            self._sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender_task
            self._sender_task = None

        if self._receiver_task is not None:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send_bytes(self._build_finish_request().SerializeToString())
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

        await self._close_http()
        self._audio_queue = None
        self._emit_status("火山会话已关闭")

    def _header_candidates(self, auth_mode: AuthMode) -> list[dict[str, str]]:
        common = {
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": self.connection_id,
        }
        if auth_mode == "legacy":
            return [
                {
                    **common,
                    "X-Api-App-Id": self.api_key,
                    "X-Api-Access-Key": self.access_token,
                },
                {
                    **common,
                    "X-Api-App-Key": self.api_key,
                    "X-Api-Access-Key": self.access_token,
                },
            ]
        return [
            {
                **common,
                "X-Api-Key": self.api_key,
                "X-Api-App-Key": self.api_key,
            },
            # Some consoles still accept app-id style with API key string
            {
                **common,
                "X-Api-App-Id": self.api_key,
                "X-Api-Access-Key": self.access_token or self.api_key,
            },
        ]

    def _extract_log_id(self) -> str:
        if self._ws is None:
            return "-"
        resp = getattr(self._ws, "_response", None)
        headers = getattr(resp, "headers", None) if resp is not None else None
        if headers is None:
            return "-"
        return headers.get("X-Tt-Logid") or headers.get("x-tt-logid") or "-"

    async def _close_http(self) -> None:
        if self._http is not None:
            with contextlib.suppress(Exception):
                await self._http.close()
            self._http = None

    async def _send_frame(self, frame: bytes) -> None:
        assert self._ws is not None
        await self._ws.send_bytes(self._build_audio_request(frame).SerializeToString())

    async def _send_loop(self) -> None:
        """Send 80ms frames as soon as audio arrives; silence keepalive only when idle."""
        assert self._ws is not None
        assert self._audio_queue is not None
        pending = bytearray()
        finishing = False
        partial_idle = 0
        try:
            while self._running:
                # Drain everything already queued (no artificial wait)
                while True:
                    try:
                        chunk = self._audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if chunk is None:
                        finishing = True
                        break
                    pending.extend(chunk)

                if not self._session_started.is_set() or self._reconnecting:
                    if finishing:
                        break
                    await asyncio.sleep(0.005)
                    continue

                # Flush complete frames immediately (ahead of realtime → lower E2E latency)
                while len(pending) >= _FRAME_BYTES:
                    frame = bytes(pending[:_FRAME_BYTES])
                    del pending[:_FRAME_BYTES]
                    await self._send_frame(frame)
                    partial_idle = 0

                if finishing:
                    if pending:
                        pad = _FRAME_BYTES - len(pending)
                        await self._send_frame(bytes(pending) + (b"\x00" * pad))
                        pending.clear()
                    with contextlib.suppress(Exception):
                        await self._ws.send_bytes(
                            self._build_finish_request().SerializeToString()
                        )
                    break

                # Wait briefly for more audio; keepalive only when fully idle
                try:
                    chunk = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=_FRAME_MS / 1000.0,
                    )
                    if chunk is None:
                        finishing = True
                    else:
                        pending.extend(chunk)
                        partial_idle = 0
                except TimeoutError:
                    if pending:
                        partial_idle += 1
                        # ~160ms with a stuck partial → pad once (end of utterance)
                        if partial_idle >= 2:
                            pad = _FRAME_BYTES - len(pending)
                            await self._send_frame(bytes(pending) + (b"\x00" * pad))
                            pending.clear()
                            partial_idle = 0
                    else:
                        await self._send_frame(_SILENCE_FRAME)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._running and not self._reconnecting:
                self._emit_error(f"火山发送失败: {exc}")

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_response(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    self._emit_status(f"火山文本帧: {msg.data[:200]}")
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._running and not self._reconnecting:
                self._emit_error(f"火山接收失败: {exc}")

    @staticmethod
    def _is_packet_timeout(message: str) -> bool:
        lower = message.lower()
        return (
            "waiting next packet" in lower
            or "timeout waiting next packet" in lower
            or "packet timeout" in lower
        )

    async def _try_reconnect(self) -> None:
        if not self._running or self._reconnecting:
            return
        if self._reconnect_attempts >= self._max_reconnect:
            self._emit_error("火山会话多次超时，请重新开启通道")
            return

        self._reconnect_attempts += 1
        self._emit_status(
            f"火山会话超时，正在重连（{self._reconnect_attempts}/{self._max_reconnect}）…"
        )
        ok = await self.rotate_session(
            reason="packet-timeout",
            backoff_s=min(1.5 * self._reconnect_attempts, 5.0),
        )
        if ok:
            self._reconnect_attempts = 0
            self._emit_status("火山会话已重连")
        else:
            self._emit_error("火山重连失败")

    async def rotate_session(self, *, reason: str = "rotate", backoff_s: float = 0.0) -> bool:
        """Tear down WS and StartSession again while keeping the audio queue."""
        if not self._running or self._reconnecting:
            return False
        self._reconnecting = True
        try:
            if self._sender_task is not None:
                self._sender_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._sender_task
                self._sender_task = None

            current = asyncio.current_task()
            if self._receiver_task is not None and self._receiver_task is not current:
                self._receiver_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receiver_task
                self._receiver_task = None

            if self._ws is not None:
                with contextlib.suppress(Exception):
                    await self._ws.send_bytes(self._build_finish_request().SerializeToString())
                with contextlib.suppress(Exception):
                    await self._ws.close()
                self._ws = None
            await self._close_http()

            self.session_id = str(uuid.uuid4())
            self.connection_id = str(uuid.uuid4())
            self._session_started.clear()
            self._source_buf = ""
            self._translation_buf = ""

            if backoff_s > 0:
                await asyncio.sleep(backoff_s)
            if not self._running:
                return False

            ok = await self.connect()
            if ok and reason not in {"packet-timeout"}:
                self._emit_status(f"会话轮转完成（{reason}）")
            return ok
        finally:
            self._reconnecting = False

    async def _rotate_loop(self) -> None:
        """Periodically rotate before cloud session ceilings."""
        interval = max(60.0, float(self.session_rotate_minutes) * 60.0)
        try:
            while self._running and self.session_rotate_minutes > 0:
                await asyncio.sleep(min(30.0, interval / 4))
                if not self._running or self._reconnecting:
                    continue
                if self._session_started_at <= 0:
                    continue
                age = time.time() - self._session_started_at
                if age < interval:
                    continue
                # Prefer rotating while VAD gate is closed
                deferred = 0.0
                while (
                    self.should_defer_rotate is not None
                    and self.should_defer_rotate()
                    and deferred < 5.0
                    and self._running
                ):
                    await asyncio.sleep(0.25)
                    deferred += 0.25
                if not self._running or self._reconnecting:
                    continue
                mins = max(1, int(age / 60))
                self._emit_status(f"会话轮转（已运行 {mins}m）")
                await self.rotate_session(reason="schedule")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"rotate loop ended: {exc}")

    def _handle_response(self, raw: bytes) -> None:
        response = TranslateResponse()
        try:
            response.ParseFromString(raw)
        except Exception as exc:
            self._emit_error(f"Protobuf 解析失败: {exc}")
            return

        event = response.event
        text = (response.text or "").strip()

        if event == Type.SessionStarted:
            self._session_started.set()
            self._emit_status("SessionStarted")
            return

        if event == Type.SessionFailed:
            message = getattr(response.response_meta, "Message", "") or "unknown error"
            if self._is_packet_timeout(message):
                self._emit_status(f"SessionFailed: {message}")
                if self._running and not self._reconnecting:
                    asyncio.create_task(self._try_reconnect())
            else:
                self._emit_error(f"SessionFailed: {message}")
            return

        if event == Type.SessionFinished:
            self._emit_status("SessionFinished")
            return

        # Sentence boundaries — reset buffers for the next utterance
        if event == Type.SourceSubtitleStart:
            self._source_buf = ""
            return

        if event == Type.TranslationSubtitleStart:
            self._translation_buf = ""
            return

        # Streaming interim (豆包式：边说边出字)
        if event == Type.SourceSubtitleResponse:
            if text:
                self._source_buf = self._merge_stream_text(self._source_buf, text)
                if self.on_source_text and self._source_buf:
                    self.on_source_text(self._source_buf, False)
            return

        if event == Type.SourceSubtitleEnd:
            final = text or self._source_buf
            self._source_buf = ""
            if final and self.on_source_text:
                self.on_source_text(final, True)
            return

        if event == Type.TranslationSubtitleResponse:
            if text:
                self._translation_buf = self._merge_stream_text(self._translation_buf, text)
                if self.on_translated_text and self._translation_buf:
                    self.on_translated_text(self._translation_buf, False)
            return

        if event == Type.TranslationSubtitleEnd:
            final = text or self._translation_buf
            self._translation_buf = ""
            if final and self.on_translated_text:
                self.on_translated_text(final, True)
            return

        if event == Type.TTSResponse and response.data:
            if self.on_audio:
                self.on_audio(bytes(response.data))
            return

        if event == Type.AudioMuted:
            return

        if event == Type.UsageResponse:
            try:
                from google.protobuf.json_format import MessageToDict

                usage = MessageToDict(response, preserving_proto_field_name=True)
            except Exception:
                usage = {"response_meta": {"SessionID": response.response_meta.SessionID}}
            if self.on_usage:
                try:
                    self.on_usage(usage)
                except Exception as exc:
                    logger.debug(f"on_usage failed: {exc}")
            # Compact status line for logs / tracker fallback
            billing = (usage.get("response_meta") or {}).get("Billing") or {}
            items = billing.get("Items") or []
            parts = [
                f"{it.get('Unit')}={it.get('Quantity')}"
                for it in items
                if isinstance(it, dict) and it.get("Unit") is not None
            ]
            summary = ", ".join(parts) if parts else "ok"
            self._emit_status(f"[usage] {summary}")
            return

    @staticmethod
    def _merge_stream_text(buf: str, text: str) -> str:
        """Merge cumulative or delta subtitle fragments from the server."""
        if not buf:
            return text
        if text.startswith(buf):
            return text
        if buf.startswith(text):
            return buf
        return buf + text

    def _build_start_request(self) -> TranslateRequest:
        request = TranslateRequest()
        request.request_meta.SessionID = self.session_id
        request.request_meta.ConnectionID = self.connection_id
        request.request_meta.ResourceID = self.resource_id
        request.request_meta.Endpoint = "translate"
        request.event = Type.StartSession
        request.user.uid = "translator_intime"
        request.user.did = "desktop"
        request.user.platform = sys.platform
        request.user.sdk_version = "0.3"

        request.source_audio.format = "wav"
        request.source_audio.codec = "raw"
        request.source_audio.rate = self.sample_rate
        request.source_audio.bits = 16
        request.source_audio.channel = 1

        request.request.mode = self.mode
        request.request.source_language = self.source_language
        request.request.target_language = self.target_language
        if self.speech_rate != 0:
            request.request.speech_rate = self.speech_rate
        if self.hotwords:
            request.request.corpus.hot_words_list.extend(self.hotwords[:200])
        if self.glossary:
            for src, tgt in list(self.glossary.items())[:200]:
                request.request.corpus.glossary_list[src] = tgt

        if self.mode == "s2s":
            request.target_audio.format = "pcm"
            request.target_audio.rate = self.sample_rate
            # 不传 / 空 = 复刻输入音色；传公版 speaker_id = 指定音色
            if self.speaker_id:
                request.request.speaker_id = self.speaker_id

        return request

    def _build_audio_request(self, pcm_bytes: bytes) -> TranslateRequest:
        request = TranslateRequest()
        request.request_meta.SessionID = self.session_id
        request.request_meta.ConnectionID = self.connection_id
        request.request_meta.ResourceID = self.resource_id
        request.request_meta.Endpoint = "translate"
        request.event = Type.TaskRequest
        request.source_audio.binary_data = pcm_bytes
        return request

    def _build_finish_request(self) -> TranslateRequest:
        request = TranslateRequest()
        request.request_meta.SessionID = self.session_id
        request.request_meta.ConnectionID = self.connection_id
        request.request_meta.ResourceID = self.resource_id
        request.request_meta.Endpoint = "translate"
        request.event = Type.FinishSession
        return request

    def _emit_error(self, message: str) -> None:
        logger.error(message)
        if self.on_error:
            self.on_error(message)

    def _emit_status(self, message: str) -> None:
        logger.info(message)
        if self.on_status:
            self.on_status(message)


class VolcRuntime:
    """Runs one or more VolcASTClient instances on a dedicated asyncio loop/thread."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._clients: dict[str, VolcASTClient] = {}
        self._ready = threading.Event()

    @property
    def clients(self) -> dict[str, VolcASTClient]:
        return self._clients

    def start_thread(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, name="volc-ast", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def submit(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("VolcRuntime not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=20.0)

    def submit_nowait(self, coro: Any) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _connect_client(self, name: str, client: VolcASTClient) -> bool:
        ok = await client.connect()
        if ok:
            self._clients[name] = client
        return ok

    def connect_client(self, name: str, client: VolcASTClient) -> bool:
        self.start_thread()
        return bool(self.submit(self._connect_client(name, client)))

    def disconnect_client(self, name: str) -> None:
        """Close one client; keep runtime alive if others remain."""
        client = self._clients.pop(name, None)
        if client is None or self._loop is None:
            return

        async def _close() -> None:
            with contextlib.suppress(Exception):
                await client.close()

        with contextlib.suppress(Exception):
            self.submit(_close())

    def send_audio(self, name: str, pcm: bytes) -> None:
        client = self._clients.get(name)
        if client is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(client.send_audio(pcm), self._loop)

    def stop(self) -> None:
        if self._loop is None:
            return

        async def _shutdown() -> None:
            for client in list(self._clients.values()):
                with contextlib.suppress(Exception):
                    await client.close()
            self._clients.clear()

        with contextlib.suppress(Exception):
            self.submit(_shutdown())

        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._clients.clear()
