"""
语音合成模块 (TTS) - 基于 edge-tts
将翻译后的文本合成为语音
"""
import asyncio
import io
import tempfile
import queue
import threading
from pathlib import Path
from typing import Optional, Union
import sounddevice as sd
import soundfile as sf
import numpy as np
from src.utils.config import TTSConfig
from src.utils.logger import logger


class TTSEngine:
    """语音合成引擎"""

    def __init__(self, config: TTSConfig):
        self.config = config
        self._output_device: Optional[Union[int, str]] = None
        self._sample_rate = 24000
        self._device_warned = False
        self._output_queue: queue.Queue = queue.Queue(maxsize=64)
        self._output_thread: Optional[threading.Thread] = None
        self._output_running = False  # 只警告一次

    def set_output_device(self, device_id: Optional[Union[int, str]]) -> None:
        """设置音频输出设备"""
        self._output_device = device_id

    async def synthesize_to_file(self, text: str, output_path: Path, is_target: bool = True) -> bool:
        """
        将文本合成为语音文件
        :param text: 待合成文本
        :param output_path: 输出文件路径
        :param is_target: True=使用目标外语声音, False=使用中文声音
        """
        if not text.strip():
            return False

        voice = self.config.target_voice if is_target else self.config.voice
        return await self._synthesize_edge_tts(text, output_path, voice)

    async def synthesize_and_play(self, text: str, is_target: bool = True) -> None:
        """
        合成语音并直接播放到虚拟音频设备
        """
        if not text.strip():
            return

        voice = self.config.target_voice if is_target else self.config.voice

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp_path = Path(tmp.name)
            success = await self._synthesize_edge_tts(text, tmp_path, voice)
            if success:
                await self._play_audio_file(tmp_path)

    async def _synthesize_edge_tts(
        self, text: str, output_path: Path, voice: str
    ) -> bool:
        """使用 edge-tts 合成语音"""
        try:
            import edge_tts
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=self.config.rate,
                volume=self.config.volume,
            )
            await communicate.save(str(output_path))
            logger.debug(f"TTS合成完成: {text[:30]}...")
            return True
        except Exception as e:
            logger.error(f"TTS合成失败: {e}")
            return False

    async def _play_audio_file(self, file_path: Path) -> None:
        """播放音频文件到输出队列（非阻塞）"""
        try:
            data, sr = sf.read(str(file_path), dtype="float32")
            if data.ndim > 1:
                data = data[:, 0]
            self._enqueue_audio(data, sr)
        except Exception as e:
            logger.error(f"音频播放失败: {e}")

    def _enqueue_audio(self, data: np.ndarray, sr: int) -> None:
        """放入输出队列，队列满则丢弃最旧的"""
        try:
            self._output_queue.put_nowait((data, sr))
        except queue.Full:
            try:
                self._output_queue.get_nowait()  # 丢最旧
            except queue.Empty:
                pass
            try:
                self._output_queue.put_nowait((data, sr))
            except queue.Full:
                pass
        self._start_output_thread()

    def _start_output_thread(self) -> None:
        """启动后台放音线程"""
        if self._output_thread and self._output_thread.is_alive():
            return
        self._output_running = True
        self._output_thread = threading.Thread(target=self._output_worker, daemon=True)
        self._output_thread.start()

    def _output_worker(self) -> None:
        """后台线程：从队列取音频播放"""
        while self._output_running:
            try:
                data, sr = self._output_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                sd.play(data, samplerate=sr, device=self._output_device)
                sd.wait()
            except (ValueError, sd.PortAudioError):
                if not self._device_warned:
                    logger.warning(f"TTS设备不可用，使用系统默认")
                    self._device_warned = True
                try:
                    sd.play(data, samplerate=sr)
                    sd.wait()
                except Exception:
                    pass
            except Exception:
                pass

    def stop_output(self) -> None:
        """停止放音线程"""
        self._output_running = False
        if self._output_thread:
            self._output_thread.join(timeout=2)

    def play_text_sync(self, text: str, is_target: bool = True) -> None:
        """同步播放文本（便捷方法）"""
        asyncio.run(self.synthesize_and_play(text, is_target))

    async def synthesize_stream(self, text: str, is_target: bool = True) -> Optional[np.ndarray]:
        """
        合成语音并返回音频数据（用于流式处理）
        """
        voice = self.config.target_voice if is_target else self.config.voice

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp_path = Path(tmp.name)
            success = await self._synthesize_edge_tts(text, tmp_path, voice)
            if success:
                data, _ = sf.read(str(tmp_path), dtype="float32")
                if data.ndim > 1:
                    data = data[:, 0]
                return data
            return None
