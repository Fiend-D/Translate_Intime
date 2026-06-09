"""
音频流工具 - 音频数据流处理、重采样、格式转换
"""
import platform
import subprocess
import numpy as np
from typing import Optional, Union
import sounddevice as sd
from src.utils.logger import logger


IS_WINDOWS = platform.system() == "Windows"
WASAPI_LOOPBACK_PREFIX = "wasapi_loopback:"


# 过滤掉的 ALSA 虚拟/子设备名（精确匹配或包含）
_JUNK_DEVICE_PATTERNS = (
    "front", "surround", "center", "side", "rear",
    "iec958", "spdif", "a52", "hdmi",
    "dmix", "dsnoop",
    "lavrate", "samplerate", "speexrate",
    "upmix", "vdownmix",
    "speex",
)


def _is_junk_device(name: str) -> bool:
    """判断是否为无用的 ALSA 虚拟/子设备"""
    name_lower = name.lower()
    for pattern in _JUNK_DEVICE_PATTERNS:
        # 精确匹配（如 "front"）或包含后接非字母字符（如 "front:"）
        idx = name_lower.find(pattern)
        if idx < 0:
            continue
        # 确保不是正常设备名的一部分（如 "wavefront" 不会误匹配）
        after = name_lower[idx + len(pattern):]
        if not after or not after[0].isalpha():
            return True
    return False


def list_audio_devices() -> dict:
    """列出所有可用的音频设备（过滤无用虚拟设备 + 补充 PipeWire 设备）"""
    devices = sd.query_devices()
    result = {"input": [], "output": []}

    seen_names = set()
    for idx, dev in enumerate(devices):
        name = dev["name"]
        if _is_junk_device(name):
            continue
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        info = {
            "id": idx,
            "name": name,
            "input_channels": in_ch,
            "output_channels": out_ch,
            "sample_rate": int(dev["default_samplerate"]),
        }
        if in_ch > 0:
            result["input"].append(info)
            seen_names.add(name.lower())
        if out_ch > 0:
            result["output"].append(info)
            seen_names.add(name.lower())

    if IS_WINDOWS:
        _add_windows_loopback_devices(result, seen_names)
    else:
        # 补充 PipeWire/PulseAudio 设备（ALSA 没枚举的）
        _add_pulse_devices(result, seen_names)

    return result


def _add_windows_loopback_devices(result: dict, seen: set) -> None:
    """补充 Windows WASAPI loopback 捕获源。"""
    try:
        import soundcard as sc
    except Exception:
        logger.warning("soundcard 未安装，无法枚举 Windows WASAPI loopback 设备")
        return

    try:
        speakers = sc.all_speakers()
        default_speaker = sc.default_speaker()
    except Exception as e:
        logger.warning(f"枚举 Windows 扬声器失败: {e}")
        return

    for speaker in speakers:
        name = getattr(speaker, "name", "")
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        label = f"[默认] {name}" if default_speaker and name == default_speaker.name else name
        result["input"].append({
            "id": f"{WASAPI_LOOPBACK_PREFIX}{name}",
            "name": f"[Loopback] {label}",
            "input_channels": 2,
            "output_channels": 0,
            "sample_rate": 48000,
        })
        seen.add(key)


def _pulse_friendly_name(pulse_name: str) -> str:
    """从 PulseAudio 设备名提取产品名"""
    import re
    text = pulse_name.replace("_", " ")
    # 匹配 USB 产品名如 VT NEXUS 65（大写+数字，但排除长序列号）
    m = re.search(r'\b([A-Z]{2,}(?:\s+[A-Z0-9]+){1,4})\b', text)
    if m:
        name = m.group(1).title()
        # 截掉序列号（长十六进制串）
        name = re.sub(r'\s+[A-F0-9]{8,}.*', '', name)
        return name
    return text.split(".")[-1].replace("alsa input ", "").replace("alsa output ", "").title()


def _add_pulse_devices(result: dict, seen: set) -> None:
    """从 PulseAudio/PipeWire 补充 PortAudio 未枚举的设备"""
    import subprocess
    try:
        # 输入源
        sources = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True
        )
        for line in sources.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[1]
            if name.lower() in seen:
                continue
            result["input"].append({
                "id": name,  # PulseAudio 设备名直接作为 ID
                "name": f"{'🔁' if 'monitor' in name else '🎧'} {_pulse_friendly_name(name)}",
                "input_channels": 1,
                "output_channels": 0,
                "sample_rate": 16000,
            })

        # 输出
        sinks = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True
        )
        for line in sinks.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[1]
            if name.lower() in seen:
                continue
            result["output"].append({
                "id": name,
                "name": f"🔊 {_pulse_friendly_name(name)}",
                "input_channels": 0,
                "output_channels": 2,
                "sample_rate": 48000,
            })
    except Exception:
        pass  # pactl 不可用时跳过


def find_device_by_name(name_pattern: str, kind: str = "output") -> Optional[Union[int, str]]:
    """
    根据名称模糊匹配音频设备。
    先在 PortAudio 设备列表中查找，找不到则返回 PulseAudio 设备名。
    """
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        dev_name = dev["name"].lower()
        if kind == "input" and dev["max_input_channels"] > 0:
            if name_pattern.lower() in dev_name:
                return idx
        elif kind == "output" and dev["max_output_channels"] > 0:
            if name_pattern.lower() in dev_name:
                return idx

    # PortAudio 未找到，尝试返回 PulseAudio/PipeWire 设备名
    logger.info(f"PortAudio 未找到 '{name_pattern}'，尝试 PulseAudio 设备名")
    return str(name_pattern)  # sounddevice 支持直接传 PulseAudio 设备名


class AudioStream:
    """音频流封装，支持输入/输出。device 可以是 PortAudio ID 或 PulseAudio 设备名"""

    def __init__(
        self,
        device: Optional[Union[int, str]] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._stream: Optional[sd.InputStream] = None
        self._pulse_process: Optional[subprocess.Popen] = None
        self._loopback_recorder = None
        self._active = False

    def open_input(self) -> None:
        """打开音频输入流"""
        if isinstance(self.device, str):
            if self.device.startswith(WASAPI_LOOPBACK_PREFIX):
                self._open_windows_loopback(self.device.removeprefix(WASAPI_LOOPBACK_PREFIX))
                return
            if IS_WINDOWS:
                actual_device = self.device
                self._open_sounddevice_input(actual_device)
                return
            self._open_pulse_input(self.device)
            return
        if isinstance(self.device, int):
            try:
                dev_info = sd.query_devices(self.device)
                dev_name = str(dev_info.get("name", ""))
                if ".monitor" in dev_name:
                    logger.info(
                        f"输入设备 ID {self.device} 是 monitor 源，改用 parec 捕获: {dev_name}"
                    )
                    self.device = dev_name
                    self._open_pulse_input(dev_name)
                    return
            except Exception:
                pass
        else:
            actual_device = self.device

        actual_device = self.device
        self._open_sounddevice_input(actual_device)

    def _open_sounddevice_input(self, actual_device) -> None:
        """用 sounddevice 打开普通输入设备。"""
        self._stream = sd.InputStream(
            device=actual_device,
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.chunk_size,
            dtype=np.float32,
        )
        self._stream.start()
        self._active = True
        logger.info(f"音频输入流已打开 (device={self.device}, sr={self.sample_rate})")

    def _open_windows_loopback(self, speaker_name: str) -> None:
        """用 soundcard 捕获 Windows WASAPI loopback。"""
        try:
            import soundcard as sc
        except Exception as e:
            raise RuntimeError("未安装 soundcard，无法使用 Windows WASAPI loopback") from e

        target = None
        for speaker in sc.all_speakers():
            if speaker.name == speaker_name:
                target = speaker
                break
        if target is None:
            target = sc.default_speaker()
            logger.warning(f"未找到指定扬声器 '{speaker_name}'，使用默认输出: {target.name}")

        self._loopback_recorder = sc.get_microphone(
            id=target.name,
            include_loopback=True,
        ).recorder(
            samplerate=48000,
            channels=2,
        )
        self._loopback_recorder.__enter__()
        self._active = True
        logger.info(f"WASAPI loopback 已打开 (speaker={target.name}, sr=48000)")

    def _open_pulse_input(self, source_name: str) -> None:
        """用 parec 直接捕获 PulseAudio/PipeWire source，适合 monitor 源。"""
        cmd = [
            "parec",
            f"--device={source_name}",
            "--format=s16le",
            f"--rate={self.sample_rate}",
            f"--channels={self.channels}",
            "--raw",
        ]
        try:
            self._pulse_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError("未找到 parec，请安装 pulseaudio-utils") from e

        self._active = True
        logger.info(f"PulseAudio输入流已打开 (source={source_name}, sr={self.sample_rate})")

    def open_output(self) -> None:
        """打开音频输出流"""
        if isinstance(self.device, str):
            import os
            os.environ["PULSE_SINK"] = self.device
            actual_device = None
            logger.info(f"音频输出: PulseAudio 设备 {self.device}")
        else:
            actual_device = self.device

        self._stream = sd.OutputStream(
            device=actual_device,
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.chunk_size,
            dtype=np.float32,
        )
        self._stream.start()
        self._active = True
        logger.info(f"音频输出流已打开 (device={self.device}, sr={self.sample_rate})")

    def read_chunk(self) -> Optional[np.ndarray]:
        """读取一个音频块（float32 numpy array）"""
        if self._loopback_recorder is not None:
            if not self._active:
                return None
            source_rate = 48000
            frames = max(1, int(self.chunk_size * source_rate / self.sample_rate))
            data = self._loopback_recorder.record(numframes=frames)
            if data.size == 0:
                return None
            mono = data.mean(axis=1).astype(np.float32)
            return self.resample(mono, source_rate, self.sample_rate)

        if self._pulse_process is not None:
            if not self._active or self._pulse_process.stdout is None:
                return None
            byte_count = self.chunk_size * self.channels * 2
            raw = self._pulse_process.stdout.read(byte_count)
            if not raw:
                err = ""
                if self._pulse_process.stderr is not None:
                    err = self._pulse_process.stderr.read().decode(errors="ignore").strip()
                logger.warning(f"PulseAudio输入流无数据: {err}")
                return None
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return audio.reshape(-1, self.channels).mean(axis=1)

        if self._stream is None or not self._active:
            return None
        data, _ = self._stream.read(self.chunk_size)
        return data.flatten()

    def write_chunk(self, data: np.ndarray) -> None:
        """写入一个音频块"""
        if self._stream is not None and self._active:
            self._stream.write(data.reshape(-1, self.channels))

    def close(self) -> None:
        """关闭音频流"""
        self._active = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._pulse_process is not None:
            self._pulse_process.terminate()
            try:
                self._pulse_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._pulse_process.kill()
            self._pulse_process = None
        if self._loopback_recorder is not None:
            try:
                self._loopback_recorder.__exit__(None, None, None)
            except Exception:
                pass
            self._loopback_recorder = None

    @staticmethod
    def resample(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """音频重采样（numpy线性插值，零依赖）"""
        ratio = target_sr / orig_sr
        if ratio == 1.0:
            return data
        n_out = max(1, int(round(len(data) * ratio)))
        x_in = np.arange(len(data), dtype=np.float32)
        x_out = np.linspace(0, len(data) - 1, n_out, dtype=np.float32)
        return np.interp(x_out, x_in, data.astype(np.float32)).astype(data.dtype)

    @staticmethod
    def float32_to_int16(data: np.ndarray) -> np.ndarray:
        """float32 [-1,1] -> int16"""
        return np.clip(data * 32767, -32768, 32767).astype(np.int16)

    @staticmethod
    def int16_to_float32(data: np.ndarray) -> np.ndarray:
        """int16 -> float32 [-1,1]"""
        return (data / 32768.0).astype(np.float32)
