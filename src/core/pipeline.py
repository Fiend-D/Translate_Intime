"""
双向翻译管道 - 核心调度模块
协调 ASR -> Translation -> TTS 两条管道并发运行

管道1（出站）: 麦克风 -> ASR(中文) -> 翻译(中->外) -> TTS(外) -> 游戏
管道2（入站）: 游戏声音 -> ASR(外语) -> 翻译(外->中) -> 字幕显示(+可选TTS)
"""
import asyncio
import os
import queue
from dataclasses import dataclass, field
from typing import Optional, Callable
import numpy as np

from src.utils.config import AppConfig
from src.utils.logger import logger
from src.core.asr_engine import ASREngine
from src.core.translator import TranslationEngine
from src.core.tts_engine import TTSEngine
from src.audio.stream import AudioStream
from src.audio.virtual_device import VirtualAudioDevice


@dataclass
class TranslationResult:
    """翻译结果"""
    source_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    direction: str  # "outbound" | "inbound"


class TranslationPipeline:
    """
    双向翻译管道管理器
    维护两条独立的 asyncio 管道并发运行
    """

    def __init__(self, config: AppConfig):
        self.config = config

        # 火山引擎（端到端，替代本地 ASR + 翻译）
        self._volc_engine = None
        self._volc_inbound_engine = None
        use_cloud_model = config.translation.use_cloud_model
        volc_requested = use_cloud_model and config.translation.backend.lower() == "volc"
        volc_configured = use_cloud_model and bool(
            config.translation.volc_app_id
            or config.translation.volc_access_token
            or os.getenv("VOLC_APP_ID")
            or os.getenv("VOLC_ACCESS_TOKEN")
            or os.getenv("VOLC_APP_KEY")
            or os.getenv("VOLC_API_KEY")
        )
        if volc_requested or volc_configured:
            from src.core.volc_engine import VolcASREngine
            logger.info(
                f"火山引擎配置: use_cloud={use_cloud_model}, "
                f"requested={volc_requested}, configured={volc_configured}"
            )
            self._volc_engine = VolcASREngine(
                app_id=config.translation.volc_app_id,
                access_token=config.translation.volc_access_token,
                mode="s2s",
            )
            self._volc_inbound_engine = VolcASREngine(
                app_id=config.translation.volc_app_id,
                access_token=config.translation.volc_access_token,
                mode="s2t",
            )

        # ASR 引擎（本地模式）
        self._asr_zh = ASREngine(config.asr, language=config.asr.source_language)
        self._asr_foreign = ASREngine(config.asr, language=config.asr.target_language)

        # 翻译引擎（复用）
        self._translator = TranslationEngine(config.translation)

        # TTS 引擎
        self._tts = TTSEngine(config.tts)

        # 音频流
        self._mic_stream: Optional[AudioStream] = None   # 麦克风输入
        self._game_stream: Optional[AudioStream] = None  # 游戏声音输入

        # 虚拟音频设备
        self._virtual_device = VirtualAudioDevice()

        # 任务控制
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # 回调
        self._on_subtitle: Optional[Callable[[TranslationResult], None]] = None
        self._on_outbound: Optional[Callable[[TranslationResult], None]] = None
        self._on_asr_text: Optional[Callable[[str, str], None]] = None  # (text, direction)
        self._on_status: Optional[Callable[[str], None]] = None  # 状态文字

    def on_subtitle(self, callback: Callable[[TranslationResult], None]) -> None:
        """注册字幕显示回调（入站翻译结果）"""
        self._on_subtitle = callback

    def on_outbound(self, callback: Callable[[TranslationResult], None]) -> None:
        """注册出站翻译结果回调"""
        self._on_outbound = callback

    def on_asr_text(self, callback: Callable[[str, str], None]) -> None:
        """注册 ASR 原文回调（text, direction），翻译前立即触发"""
        self._on_asr_text = callback

    def on_status(self, callback: Callable[[str], None]) -> None:
        """注册状态更新回调"""
        self._on_status = callback

    async def initialize(self) -> None:
        """初始化所有引擎和设备"""
        logger.info("初始化翻译管道...")

        # 设置虚拟音频设备（存在则创建，不存在则跳过）
        self._virtual_device.setup_full()

        if self._volc_engine and self._volc_engine.is_available:
            logger.info("火山引擎可用，跳过本地 ASR 模型加载")
        else:
            await self._ensure_asr_models_loaded()

        # TTS 输出设备：仅当用户明确配置时才指定，否则系统默认
        tts_device = self.config.audio.output_device
        if tts_device is not None:
            self._tts.set_output_device(tts_device)
            logger.info(f"TTS输出: 指定设备 ID={tts_device}")
        else:
            logger.info("TTS输出: 系统默认扬声器")

        logger.info("翻译管道初始化完成")

    async def _ensure_asr_models_loaded(self) -> None:
        """按需加载本地 ASR 模型。"""
        if getattr(self, "_asr_models_loaded", False):
            return

        # 加载 ASR 模型（中文 + 外语各一个，共用同一个模型文件）
        logger.info(f"下载/加载本地 ASR 模型（backend={self.config.asr.backend}）...")
        ok = self._asr_zh.load_model()
        if not ok:
            logger.error("ASR 模型加载失败！翻译功能不可用。"
                         "如 HuggingFace 不可访问，设置环境变量后重试:\n"
                         "  export HF_ENDPOINT=https://hf-mirror.com\n"
                         "  python run.py")
        else:
            # 外语 ASR 复用同一模型缓存
            if not self._asr_foreign.share_model_from(self._asr_zh):
                self._asr_foreign.load_model()
            self._asr_models_loaded = True

    async def start(self) -> None:
        """启动双向翻译管道"""
        if self._running:
            return

        self._running = True

        # 麦克风输入
        self._mic_stream = AudioStream(
            device=self.config.audio.input_device,
            sample_rate=self.config.audio.sample_rate,
            channels=self.config.audio.channels,
        )
        try:
            self._mic_stream.open_input()
        except Exception as e:
            logger.warning(f"麦克风打开失败 (device={self.config.audio.input_device}): {e}")
            logger.info("使用系统默认麦克风")
            self._mic_stream.device = None
            self._mic_stream.open_input()

        # 游戏声音捕获 — 仅当用户明确配置了设备才启用
        self._game_stream = None
        if self.config.audio.game_output_device is not None:
            self._game_stream = AudioStream(
                device=self.config.audio.game_output_device,
                sample_rate=self.config.audio.sample_rate,
                channels=self.config.audio.channels,
            )
            try:
                self._game_stream.open_input()
                logger.info("游戏声音捕获已开启")
            except Exception as e:
                logger.warning(f"游戏声音捕获设备打开失败: {e}")
                self._game_stream.close()
                self._game_stream = None
        else:
            logger.info("未配置游戏声音捕获设备，仅启用出站翻译（你说→外）")

        # 启动并发任务
        if self._volc_engine and self._volc_engine.is_available:
            self._tasks = [
                asyncio.create_task(self._volc_outbound_loop(), name="volc"),
            ]
            if self._game_stream is not None and self._volc_inbound_engine:
                self._tasks.append(
                    asyncio.create_task(self._volc_inbound_loop(), name="volc-inbound")
                )
                logger.info("✅ 火山引擎双向管道已启动（出站 + 系统音频入站）")
            else:
                logger.info("✅ 火山引擎管道已启动（端到端语音翻译）")
        else:
            self._tasks = [
                asyncio.create_task(self._outbound_loop(), name="outbound"),
            ]
            if self._game_stream is not None:
                self._tasks.append(
                    asyncio.create_task(self._inbound_loop(), name="inbound")
                )
                logger.info("✅ 双向翻译管道已启动（出站 + 入站）")
            else:
                logger.info("✅ 出站翻译管道已启动（你说→外语）")

    async def stop(self) -> None:
        """停止管道"""
        self._running = False

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._mic_stream:
            self._mic_stream.close()
        if self._game_stream:
            self._game_stream.close()

        logger.info("翻译管道已停止")

    # ---- 火山引擎管道（端到端） ----

    async def _volc_outbound_loop(self) -> None:
        """
        火山引擎出站：麦克风 → WebSocket → 识别+翻译+TTS
        单条连接完成全部，延迟最低
        """
        if not self._volc_engine:
            return

        logger.info("火山引擎管道启动")
        
        # 连接
        ok = await self._volc_engine.connect(
            self.config.translation.source_lang,
            self.config.translation.target_lang,
        )
        if not ok:
            logger.error("火山引擎连接失败，回退本地模式")
            await self._ensure_asr_models_loaded()
            await self._outbound_loop()
            return

        # 设置回调
        self._volc_engine.on_source_text = lambda t: (
            self._on_asr_text(t, "outbound") if self._on_asr_text else None,
            self._on_status("翻译中...") if self._on_status else None,
        )
        
        def on_translated(translated: str):
            # 构建结果
            result = TranslationResult(
                source_text="",  # 火山引擎的原文通过 on_source_text 单独回调
                translated_text=translated,
                source_lang=self.config.translation.source_lang,
                target_lang=self.config.translation.target_lang,
                direction="outbound",
            )
            if self._on_outbound:
                self._on_outbound(result)
            if self._on_status:
                self._on_status("🎙 监听中")
        
        self._volc_engine.on_translated_text = on_translated

        # 接收循环（后台）
        recv_task = asyncio.create_task(self._volc_engine.recv_loop())

        # 发送循环：持续发送 PCM 音频
        try:
            while self._running:
                chunk = self._mic_stream.read_chunk() if self._mic_stream else None
                if chunk is None:
                    await asyncio.sleep(0.005)
                    continue
                
                # 噪声门
                chunk[np.abs(chunk) < self.config.asr.noise_gate_threshold] = 0
                
                await self._volc_engine.send_audio(chunk)
                await asyncio.sleep(0.005)
                
        except asyncio.CancelledError:
            pass
        finally:
            recv_task.cancel()
            await self._volc_engine.stop()

    async def _volc_inbound_loop(self) -> None:
        """
        火山引擎入站：系统/游戏声音 -> WebSocket -> 原文+译文字幕
        """
        if not self._volc_inbound_engine:
            return

        logger.info("火山引擎入站管道启动（系统音频→字幕）")

        ok = await self._volc_inbound_engine.connect(
            self.config.translation.target_lang,
            self.config.translation.source_lang,
        )
        if not ok:
            logger.error("火山引擎入站连接失败，回退本地入站模式")
            await self._ensure_asr_models_loaded()
            await self._inbound_loop()
            return

        self._volc_inbound_engine.on_source_text = lambda t: (
            self._on_asr_text(t, "inbound") if self._on_asr_text else None
        )

        def on_translated(translated: str):
            result = TranslationResult(
                source_text="",
                translated_text=translated,
                source_lang=self.config.translation.target_lang,
                target_lang=self.config.translation.source_lang,
                direction="inbound",
            )
            if self._on_subtitle:
                self._on_subtitle(result)
            if self._on_status:
                self._on_status("🎙 监听中")

        self._volc_inbound_engine.on_translated_text = on_translated

        recv_task = asyncio.create_task(self._volc_inbound_engine.recv_loop())

        try:
            while self._running:
                chunk = self._game_stream.read_chunk() if self._game_stream else None
                if chunk is None:
                    await asyncio.sleep(0.005)
                    continue

                chunk[np.abs(chunk) < self.config.asr.noise_gate_threshold] = 0
                await self._volc_inbound_engine.send_audio(chunk)
                await asyncio.sleep(0.005)

        except asyncio.CancelledError:
            pass
        finally:
            recv_task.cancel()
            await self._volc_inbound_engine.stop()

    # ---- 本地出站管道 ----

    async def _outbound_loop(self) -> None:
        """本地出站翻译循环：麦克风 -> ASR -> 翻译 -> TTS。"""
        logger.info("出站管道(中->外) 启动")
        silence_counter = 0
        had_voice = False

        while self._running:
            try:
                # 1. 读取麦克风音频
                chunk = self._mic_stream.read_chunk() if self._mic_stream else None
                if chunk is None:
                    await asyncio.sleep(0.005)
                    continue

                # 语音活动检测
                energy = np.sqrt(np.mean(chunk ** 2))
                if energy < 0.005:  # 静音阈值（降低以提高灵敏度）
                    silence_counter += 1
                    if had_voice and silence_counter > 15:  # 0.15s静音后触发识别（加速）
                        text = self._asr_zh.transcribe()
                        if text:
                            await self._process_outbound(text)
                        silence_counter = 0
                        had_voice = False
                    await asyncio.sleep(0.005)
                    continue

                # 噪声门：低于阈值的音频归零
                noise_gate = self.config.asr.noise_gate_threshold
                chunk[np.abs(chunk) < noise_gate] = 0

                silence_counter = 0
                had_voice = True
                self._asr_zh.feed_audio(chunk)

                # 2. 默认等静音断句后识别；长时间连续语音才兜底切分
                if self._asr_zh.should_force_flush():
                    text = self._asr_zh.transcribe()
                    if text:
                        await self._process_outbound(text)
                    had_voice = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"出站管道异常: {e}")
                await asyncio.sleep(0.1)

    async def _process_outbound(self, text: str) -> None:
        """处理出站翻译：中文 -> 外语 -> TTS"""
        # 立即显示 ASR 原文
        if self._on_asr_text:
            self._on_asr_text(text, "outbound")
        if self._on_status:
            self._on_status("翻译中...")

        try:
            translated = await self._translator.translate(
                text,
                source_lang=self.config.translation.source_lang,
                target_lang=self.config.translation.target_lang,
            )

            result = TranslationResult(
                source_text=text,
                translated_text=translated,
                source_lang=self.config.translation.source_lang,
                target_lang=self.config.translation.target_lang,
                direction="outbound",
            )

            if self._on_outbound:
                self._on_outbound(result)
            if self._on_status:
                self._on_status("监听中")

            # TTS合成并输出
            await self._tts.synthesize_and_play(translated, is_target=True)

        except Exception as e:
            logger.error(f"出站处理失败: {e}")
            if self._on_status:
                self._on_status("监听中")

    # ---- 入站管道：外语 -> 中文 ----

    async def _inbound_loop(self) -> None:
        """
        入站翻译循环
        游戏声音 -> ASR(外语) -> 翻译(外->中) -> 字幕显示 + 可选中文TTS
        """
        logger.info("入站管道(外->中) 启动")
        silence_counter = 0
        last_level_log = 0.0
        had_voice = False

        while self._running:
            try:
                chunk = self._game_stream.read_chunk() if self._game_stream else None
                if chunk is None:
                    await asyncio.sleep(0.005)
                    continue

                energy = np.sqrt(np.mean(chunk ** 2))
                now = asyncio.get_running_loop().time()
                if now - last_level_log >= 1.0:
                    logger.info(f"系统音频输入电平 rms={energy:.5f}")
                    last_level_log = now
                if energy < 0.005:
                    silence_counter += 1
                    if had_voice and silence_counter > 15:
                        text = self._asr_foreign.transcribe()
                        if text:
                            await self._process_inbound(text)
                        silence_counter = 0
                        had_voice = False
                    await asyncio.sleep(0.005)
                    continue

                silence_counter = 0
                had_voice = True
                chunk[np.abs(chunk) < self.config.asr.noise_gate_threshold] = 0
                self._asr_foreign.feed_audio(chunk)
                if self._asr_foreign.should_force_flush():
                    text = self._asr_foreign.transcribe()
                    if text:
                        await self._process_inbound(text)
                    had_voice = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"入站管道异常: {e}")
                await asyncio.sleep(0.1)

    async def _process_inbound(self, text: str) -> None:
        """处理入站翻译：外语 -> 中文 -> 字幕 + 可选TTS"""
        # 立即显示 ASR 原文
        if self._on_asr_text:
            self._on_asr_text(text, "inbound")
        if self._on_status:
            self._on_status("翻译中...")

        try:
            translated = await self._translator.translate(
                text,
                source_lang=self.config.translation.target_lang,
                target_lang=self.config.translation.source_lang,
            )

            result = TranslationResult(
                source_text=text,
                translated_text=translated,
                source_lang=self.config.translation.target_lang,
                target_lang=self.config.translation.source_lang,
                direction="inbound",
            )

            if self._on_subtitle:
                self._on_subtitle(result)
            if self._on_status:
                self._on_status("监听中")

            # 可选：播放中文语音播报
            if self.config.ui.play_chinese_voice:
                await self._tts.synthesize_and_play(translated, is_target=False)

        except Exception as e:
            logger.error(f"入站处理失败: {e}")
