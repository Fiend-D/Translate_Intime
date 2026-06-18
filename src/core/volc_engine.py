"""
火山引擎实时语音翻译引擎 (Volcano AST)
- 字节跳动国内服务，免费额度
- 单条 WebSocket 完成: ASR → 翻译 → TTS
- 参考: https://www.volcengine.com/docs/6561/1756902
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, Callable

import aiohttp
import numpy as np

from src.utils.logger import logger

try:
    from python_protogen.common.events_pb2 import Type
    from python_protogen.products.understanding.ast.ast_service_pb2 import (
        TranslateRequest,
        TranslateResponse,
    )
except ModuleNotFoundError:
    sayhey_root = Path(__file__).resolve().parents[4] / "sayhey" / "SayHey"
    if sayhey_root.exists():
        sys.path.insert(0, str(sayhey_root))
    from python_protogen.common.events_pb2 import Type
    from python_protogen.products.understanding.ast.ast_service_pb2 import (
        TranslateRequest,
        TranslateResponse,
    )


class VolcASREngine:
    """
    火山引擎语音翻译引擎 (Speech-to-Speech)
    
    单次 WebSocket 连接完成:
      发送 PCM 音频 → 接收识别文本 → 接收翻译文本 → 接收合成语音
    
    免费额度: 每月 5 万次调用
    注册: https://console.volcengine.com/speech/service/8
    """

    # 火山引擎 AST 2.0 服务端点
    WS_URL = "wss://openspeech.bytedance.com/api/v4/ast/v2/translate"

    def __init__(
        self,
        app_id: str = "",
        access_token: str = "",
        resource_id: str = "",
        mode: str = "s2s",
        enable_tts: bool = True,
    ):
        self.app_id = app_id or os.getenv("VOLC_APP_ID", "")
        self.access_token = access_token or os.getenv("VOLC_ACCESS_TOKEN", "")
        self.api_key = os.getenv("VOLC_APP_KEY", "") or os.getenv("VOLC_API_KEY", "")
        self.resource_id = resource_id or os.getenv("VOLC_RESOURCE_ID", "volc.service_type.10053")
        self.mode = mode
        self.enable_tts = enable_tts  # 是否启用音频输出（节省token）
        self.session_id = str(uuid.uuid4())
        self.connection_id = str(uuid.uuid4())
        self.source_lang = "zh"
        self.target_lang = "en"
        self._source_parts: list[str] = []
        self._translation_parts: list[str] = []
        
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        
        # Token用量统计回调
        self.on_usage_update: Optional[Callable[[dict], None]] = None
        
        # 回调
        self.on_source_text: Optional[Callable[[str], None]] = None   # 识别原文
        self.on_translated_text: Optional[Callable[[str], None]] = None  # 翻译结果
        self.on_audio: Optional[Callable[[bytes], None]] = None  # TTS PCM 音频
        self.on_error: Optional[Callable[[str], None]] = None

    @property
    def is_available(self) -> bool:
        return bool(self.api_key or self.app_id or (self.app_id and self.access_token))

    @property
    def _use_legacy_auth(self) -> bool:
        """旧版控制台使用数字 App ID + Access Token；新版优先使用 API Key。"""
        return (
            not self.api_key
            and bool(self.app_id and self.access_token)
            and self.app_id.strip().isdigit()
        )

    def _build_header_candidates(self) -> list[tuple[str, dict[str, str]]]:
        """构建火山 AST 2.0 WebSocket 鉴权请求头候选。"""
        if self._use_legacy_auth:
            logger.info("火山引擎鉴权模式: 旧版 App ID + Access Token")
            common = {
                "X-Api-Access-Key": self.access_token,
                "X-Api-Resource-Id": self.resource_id,
                "X-Api-Connect-Id": self.connection_id,
            }
            return [
                ("旧版 App-Id", {"X-Api-App-Id": self.app_id, **common}),
                ("旧版 App-Key", {"X-Api-App-Key": self.app_id, **common}),
            ]

        api_key = self.api_key or self.app_id.strip()
        logger.info("火山引擎鉴权模式: 新版 API Key")
        return [
            ("新版 API Key", {
                "X-Api-Key": api_key,
                "X-Api-Resource-Id": self.resource_id,
                "X-Api-Connect-Id": self.connection_id,
            })
        ]

    def _build_start_request(self) -> TranslateRequest:
        request = TranslateRequest()
        request.request_meta.SessionID = self.session_id
        request.request_meta.ConnectionID = self.connection_id
        request.request_meta.ResourceID = self.resource_id
        request.request_meta.Endpoint = "translate"
        request.event = Type.StartSession
        request.user.uid = "translator_intime"
        request.user.did = "linux_desktop"
        request.user.platform = "Linux"
        request.user.sdk_version = "demo"

        request.source_audio.format = "wav"
        request.source_audio.codec = "raw"
        request.source_audio.rate = 16000
        request.source_audio.bits = 16
        request.source_audio.channel = 1

        # 仅在需要音频输出时设置 target_audio（节省token）
        if self.mode == "s2s" and self.enable_tts:
            request.target_audio.format = "pcm"
            request.target_audio.rate = 16000

        request.request.mode = self.mode
        request.request.source_language = self.source_lang
        request.request.target_language = self.target_lang
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

    def _response_log_id(self) -> Optional[str]:
        """从 aiohttp WebSocket 底层响应头中读取火山 logid。"""
        response = getattr(self._ws, "_response", None)
        headers = getattr(response, "headers", None)
        return headers.get("X-Tt-Logid") if headers else None

    @staticmethod
    def _append_stream_part(parts: list[str], text: str) -> None:
        """累积火山字幕片段，兼容增量片段和完整覆盖两种返回形态。"""
        if not text:
            return
        current = "".join(parts)
        if text == current:
            return
        if text.startswith(current):
            parts.clear()
            parts.append(text)
            return
        parts.append(text)

    @staticmethod
    def _consume_stream_text(parts: list[str], final_text: str = "") -> str:
        text = final_text.strip() or "".join(parts).strip()
        parts.clear()
        return text

    async def connect(self, source_lang: str = "zh", target_lang: str = "en") -> bool:
        """建立 WebSocket 连接并发送初始化配置"""
        if not self.is_available:
            logger.warning("火山引擎凭证未配置")
            return False

        self.source_lang = source_lang
        self.target_lang = target_lang
        self.session_id = str(uuid.uuid4())
        self.connection_id = str(uuid.uuid4())

        last_error: Optional[Exception] = None
        for auth_name, headers in self._build_header_candidates():
            self._session = aiohttp.ClientSession()
            try:
                logger.info(f"尝试火山引擎鉴权头: {auth_name}")
                self._ws = await self._session.ws_connect(
                    self.WS_URL,
                    headers=headers,
                    max_msg_size=16 * 1024 * 1024,
                    heartbeat=None,
                    timeout=30,
                )
                log_id = self._response_log_id()
                logger.info(f"火山引擎已连接，X-Tt-Logid={log_id}")
                break
            except Exception as e:
                last_error = e
                logger.warning(f"火山引擎鉴权头 {auth_name} 连接失败: {e}")
                await self._session.close()
                self._session = None
                self._ws = None

        if not self._ws:
            logger.error(f"火山引擎连接失败: {last_error}")
            return False
        
        await self._ws.send_bytes(self._build_start_request().SerializeToString())
        started = await self._wait_session_started()
        if not started:
            await self.stop()
            return False

        self._running = True
        logger.info(f"火山引擎会话已就绪 ({source_lang}→{target_lang})")
        return True

    async def _wait_session_started(self) -> bool:
        """等待服务端 SessionStarted；文档要求收到后才能发送音频包。"""
        if not self._ws:
            return False

        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            try:
                msg = await self._ws.receive(timeout=1)
            except asyncio.TimeoutError:
                continue

            if msg.type == aiohttp.WSMsgType.BINARY:
                event = self._handle_response(msg.data)
                if event == Type.SessionStarted:
                    return True
                if event == Type.SessionFailed:
                    return False
            elif msg.type == aiohttp.WSMsgType.TEXT:
                logger.info(f"火山引擎文本帧: {msg.data}")
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                logger.error("火山引擎会话启动前连接关闭")
                return False

        logger.error("火山引擎会话启动超时，未收到 SessionStarted")
        return False

    async def send_audio(self, pcm_data: np.ndarray) -> None:
        """发送 PCM16 音频数据"""
        if not self._ws or not self._running:
            return
        
        # float32 [-1,1] → int16
        audio = np.clip(pcm_data * 32767, -32768, 32767).astype(np.int16)
        payload = audio.tobytes()
        
        try:
            request = self._build_audio_request(payload)
            await self._ws.send_bytes(request.SerializeToString())
        except Exception as e:
            logger.error(f"音频发送失败: {e}")

    async def recv_loop(self) -> None:
        """接收循环: 解析服务端返回的识别/翻译/TTS数据"""
        if not self._ws:
            return
        
        while self._running:
            try:
                msg = await self._ws.receive(timeout=1)
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    logger.info(f"火山引擎文本帧: {msg.data}")
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_response(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"接收异常: {e}")
                break

    def _handle_response(self, data: bytes) -> int:
        """处理火山 AST 2.0 Protobuf 响应。"""
        response = TranslateResponse()
        response.ParseFromString(data)

        if response.event == Type.SessionStarted:
            logger.info(f"火山引擎会话已启动: {self.session_id}")
        elif response.event == Type.SourceSubtitleStart:
            self._source_parts.clear()
        elif response.event == Type.SourceSubtitleResponse:
            self._append_stream_part(self._source_parts, response.text)
        elif response.event == Type.SourceSubtitleEnd:
            text = self._consume_stream_text(self._source_parts, response.text)
            if text and self.on_source_text:
                self.on_source_text(text)
        elif response.event == Type.TranslationSubtitleStart:
            self._translation_parts.clear()
        elif response.event == Type.TranslationSubtitleResponse:
            self._append_stream_part(self._translation_parts, response.text)
        elif response.event == Type.TranslationSubtitleEnd:
            text = self._consume_stream_text(self._translation_parts, response.text)
            if text and self.on_translated_text:
                self.on_translated_text(text)
        elif response.event == Type.TTSResponse and response.data:
            if self.on_audio:
                self.on_audio(response.data)
        elif response.event == Type.AudioMuted:
            logger.debug(f"火山引擎静音片段: {response.muted_duration_ms}ms")
        elif response.event == Type.SessionFailed:
            err = response.response_meta.Message or "unknown"
            logger.error(f"火山引擎会话失败: {err}")
            if self.on_error:
                self.on_error(err)
        elif response.event == Type.SessionFinished:
            logger.info("火山引擎会话已结束")
        
        # 提取并上报用量信息
        self._extract_usage(response)
        
        return response.event
    
    def _extract_usage(self, response: TranslateResponse) -> None:
        """从响应中提取token用量信息"""
        try:
            usage = {}
            
            # 尝试从响应元数据中提取用量
            if hasattr(response, 'response_meta') and response.response_meta:
                meta = response.response_meta
                if hasattr(meta, 'usage') and meta.usage:
                    usage_data = meta.usage
                    if hasattr(usage_data, 'input_tokens'):
                        usage['input_tokens'] = usage_data.input_tokens
                    if hasattr(usage_data, 'output_tokens'):
                        usage['output_tokens'] = usage_data.output_tokens
                    if hasattr(usage_data, 'total_tokens'):
                        usage['total_tokens'] = usage_data.total_tokens
            
            # 如果没有从元数据获取到，尝试估算
            if not usage and response.event in (Type.SourceSubtitleEnd, Type.TranslationSubtitleEnd, Type.TTSResponse):
                text = response.text if hasattr(response, 'text') else ''
                audio_data = response.data if hasattr(response, 'data') else b''
                
                if text:
                    # 文本token估算：中文约1字1token，英文约1词1token
                    input_tokens = len(text)  # 粗略估算
                    usage['input_tokens'] = input_tokens
                    usage['output_tokens'] = input_tokens  # 翻译输出约等于输入
                    
                if audio_data:
                    # 音频token估算：按音频时长估算，16kHz PCM16 约 32000 bytes/秒
                    audio_duration_sec = len(audio_data) / 32000
                    audio_tokens = int(audio_duration_sec * 16000)  # 约16000 tokens/秒音频
                    usage['output_audio_tokens'] = audio_tokens
            
            if usage and self.on_usage_update:
                self.on_usage_update(usage)
                
        except Exception as e:
            logger.debug(f"提取用量信息失败: {e}")

    async def stop(self) -> None:
        """停止连接"""
        self._running = False
        if self._ws:
            try:
                await self._ws.send_bytes(self._build_finish_request().SerializeToString())
                await self._ws.close()
            except Exception:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("火山引擎已断开")

    # ---- 便捷接口（兼容现有 TranslationEngine） ----

    async def translate_audio(
        self,
        audio: np.ndarray,
        source_lang: str = "zh",
        target_lang: str = "en",
    ) -> Optional[str]:
        """
        同步方式翻译一段音频，返回翻译文本
        （用于替换当前的 ASR → 翻译 两步骤）
        """
        source_text = None
        translated_text = None
        
        # 临时回调
        def on_src(t):
            nonlocal source_text
            source_text = t
        def on_tgt(t):
            nonlocal translated_text
            translated_text = t
        
        old_src = self.on_source_text
        old_tgt = self.on_translated_text
        self.on_source_text = on_src
        self.on_translated_text = on_tgt
        
        await self.connect(source_lang, target_lang)
        await self.send_audio(audio)
        
        # 等 5 秒收结果
        deadline = time.time() + 5
        while time.time() < deadline and translated_text is None:
            try:
                msg = await self._ws.receive(timeout=0.5)
                if msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_response(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
            except asyncio.TimeoutError:
                continue
        
        await self.stop()
        self.on_source_text = old_src
        self.on_translated_text = old_tgt
        
        return translated_text
