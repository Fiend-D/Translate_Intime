"""
阿里云实时语音翻译引擎 (Qwen3.5 LiveTranslate)
- 阿里云百炼服务，支持60种语言互译
- WebSocket 协议，支持音频+文本输出
- 参考: https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime
"""
import asyncio
import json
import os
import uuid
from typing import Optional, Callable

import aiohttp
import numpy as np

from src.utils.logger import logger


class AliyunLiveTranslateEngine:
    """
    阿里云 Qwen3.5 LiveTranslate 实时翻译引擎
    
    单次 WebSocket 连接完成:
      发送 PCM 音频 → 接收识别原文 → 接收翻译文本 → 接收合成语音(可选)
    
    支持:
    - 60种语言互译
    - 音频+文本输出 或 仅文本输出
    - 原文识别输出
    - 热词增强
    - 音色复刻
    """

    # 阿里云百炼 WebSocket 端点
    WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-livetranslate-flash-realtime"

    def __init__(
        self,
        api_key: str = "",
        enable_audio_output: bool = True,
        voice: str = "Tina",
    ):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.enable_audio_output = enable_audio_output
        self.voice = voice
        self.source_lang = "zh"
        self.target_lang = "en"
        self._session_id = str(uuid.uuid4())
        
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        
        # 回调
        self.on_source_text: Optional[Callable[[str], None]] = None
        self.on_translated_text: Optional[Callable[[str], None]] = None
        self.on_audio: Optional[Callable[[bytes], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_usage_update: Optional[Callable[[dict], None]] = None

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def _build_headers(self) -> dict:
        """构建 WebSocket 鉴权请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_session_update(self) -> dict:
        """构建 session.update 事件"""
        event = {
            "event": "session.update",
            "session": {
                "input_audio_transcription": {
                    "model": "qwen3-asr-flash-realtime",
                    "language": self.source_lang,
                },
                "translation": {
                    "language": self.target_lang,
                },
                "modalities": ["text", "audio"] if self.enable_audio_output else ["text"],
                "voice": self.voice,
                "input_audio_format": "pcm_16000hz_mono_16bit",
                "output_audio_format": "pcm_24000hz_mono_16bit",
            }
        }
        return event

    def _build_audio_append(self, pcm_bytes: bytes) -> dict:
        """构建 input_audio_buffer.append 事件"""
        import base64
        return {
            "event": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm_bytes).decode("utf-8"),
        }

    async def connect(self, source_lang: str = "zh", target_lang: str = "en") -> bool:
        """建立 WebSocket 连接并发送初始化配置"""
        if not self.is_available:
            logger.warning("阿里云 API Key 未配置")
            return False

        self.source_lang = source_lang
        self.target_lang = target_lang
        self._session_id = str(uuid.uuid4())

        self._session = aiohttp.ClientSession()
        try:
            logger.info("连接阿里云 Qwen3.5 LiveTranslate...")
            self._ws = await self._session.ws_connect(
                self.WS_URL,
                headers=self._build_headers(),
                max_msg_size=16 * 1024 * 1024,
                heartbeat=None,
                timeout=30,
            )
            logger.info("阿里云 WebSocket 已连接")
        except Exception as e:
            logger.error(f"阿里云连接失败: {e}")
            await self._session.close()
            self._session = None
            return False

        # 发送 session.update 配置
        try:
            await self._ws.send_json(self._build_session_update())
            logger.info(f"阿里云会话配置已发送 ({source_lang}→{target_lang})")
        except Exception as e:
            logger.error(f"阿里云会话配置发送失败: {e}")
            await self.stop()
            return False

        self._running = True
        return True

    async def send_audio(self, pcm_data: np.ndarray) -> None:
        """发送 PCM16 音频数据"""
        if not self._ws or not self._running:
            return
        
        # float32 [-1,1] → int16
        audio = np.clip(pcm_data * 32767, -32768, 32767).astype(np.int16)
        payload = audio.tobytes()
        
        try:
            event = self._build_audio_append(payload)
            await self._ws.send_json(event)
        except Exception as e:
            logger.error(f"阿里云音频发送失败: {e}")

    async def recv_loop(self) -> None:
        """接收循环: 解析服务端返回的数据"""
        if not self._ws:
            return
        
        while self._running:
            try:
                msg = await self._ws.receive(timeout=1)
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_response(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    logger.debug(f"阿里云二进制帧: {len(msg.data)} bytes")
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"阿里云接收异常: {e}")
                break

    def _handle_response(self, data: str) -> None:
        """处理阿里云 JSON 响应"""
        try:
            event = json.loads(data)
            event_type = event.get("event", "")
            
            if event_type == "session.updated":
                logger.info("阿里云会话配置已更新")
                
            elif event_type == "conversation.item.input_audio_transcription.text":
                # 流式原文识别
                text = event.get("text", "")
                if text and self.on_source_text:
                    self.on_source_text(text)
                    
            elif event_type == "conversation.item.input_audio_transcription.completed":
                # 原文识别完成
                text = event.get("text", "")
                if text and self.on_source_text:
                    self.on_source_text(text)
                    
            elif event_type == "conversation.item.translation.text":
                # 流式翻译结果
                text = event.get("text", "")
                if text and self.on_translated_text:
                    self.on_translated_text(text)
                    
            elif event_type == "conversation.item.translation.completed":
                # 翻译完成
                text = event.get("text", "")
                if text and self.on_translated_text:
                    self.on_translated_text(text)
                    
            elif event_type == "conversation.item.audio":
                # 音频输出
                import base64
                audio_data = event.get("audio", "")
                if audio_data and self.on_audio:
                    pcm_bytes = base64.b64decode(audio_data)
                    self.on_audio(pcm_bytes)
                    
            elif event_type == "error":
                err_msg = event.get("error", {}).get("message", "未知错误")
                logger.error(f"阿里云错误: {err_msg}")
                if self.on_error:
                    self.on_error(err_msg)
                    
            elif event_type == "session.created":
                logger.info("阿里云会话已创建")
                
            # 提取用量信息
            self._extract_usage(event)
            
        except json.JSONDecodeError:
            logger.warning(f"阿里云收到非JSON数据: {data[:200]}")
        except Exception as e:
            logger.error(f"阿里云响应处理异常: {e}")

    def _extract_usage(self, event: dict) -> None:
        """从响应中提取用量信息"""
        try:
            usage = event.get("usage", {})
            if usage and self.on_usage_update:
                self.on_usage_update(usage)
        except Exception:
            pass

    async def stop(self) -> None:
        """停止连接"""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("阿里云已断开")
