"""
虚拟音频设备管理器 - 跨平台（Windows/Linux）虚拟音频设备管理
用于将合成语音输出到游戏输入，以及捕获游戏输出声音
"""
import subprocess
import platform
from typing import Optional
from src.utils.logger import logger

OS_NAME = platform.system()


class VirtualAudioDevice:
    """
    虚拟音频设备的抽象表示
    - Windows: 使用 VB-Cable 或同类型虚拟设备
    - Linux: 使用 PulseAudio null sink / PipeWire
    """

    def __init__(self, name: str = "translator_virtual"):
        self.name = name
        self.is_windows = OS_NAME == "Windows"

    # --- 虚拟扬声器（TTS输出 -> 游戏麦克风输入） ---

    def create_loopback_speaker_to_mic(self) -> bool:
        """
        创建虚拟扬声器 -> 虚拟麦克风的环回。
        应用将合成的语音播放到虚拟扬声器，游戏将此虚拟麦克风作为输入。
        """
        if self.is_windows:
            return self._windows_create_speaker()
        else:
            return self._linux_create_speaker()

    def _windows_create_speaker(self) -> bool:
        """Windows: 依赖已安装的 VB-Cable 等虚拟音频驱动"""
        logger.info("Windows: 请确保已安装 VB-Cable 或类似虚拟音频设备")
        logger.info("下载: https://vb-audio.com/Cable/")
        return True  # VB-Cable 安装后自动可用

    @staticmethod
    def _run(*args, check: bool = False) -> subprocess.CompletedProcess:
        """安全运行命令，pactl 不存在时不崩溃"""
        try:
            return subprocess.run(args, capture_output=True, text=True, check=check)
        except FileNotFoundError:
            logger.warning(f"命令未找到: {args[0]}，请安装 pulseaudio-utils")
            raise
        except subprocess.CalledProcessError as e:
            logger.warning(f"命令失败: {' '.join(args)} -> {e.stderr.strip()}")
            raise

    def _linux_create_speaker(self) -> bool:
        """Linux: 创建 PulseAudio/PipeWire null sink"""
        try:
            # 检查是否已有该设备
            result = self._run("pactl", "list", "short", "modules")
            if self.name in result.stdout:
                logger.info(f"虚拟音频设备 '{self.name}' 已存在")
                return True

            # 创建 null sink 作为虚拟扬声器
            self._run("pactl", "load-module", "module-null-sink",
                      f"sink_name={self.name}_sink",
                      f"sink_properties=device.description={self.name}_Speaker",
                      check=True)
            logger.info(f"已创建虚拟扬声器: {self.name}_sink")
            return True
        except FileNotFoundError:
            logger.warning("pactl 未安装，跳过虚拟音频设备创建。"
                           "请运行: sudo apt install pulseaudio-utils")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"创建虚拟扬声器失败: {e}")
            return False

    def _linux_create_mic_monitor(self) -> bool:
        """
        Linux: 创建虚拟扬声器的 monitor 源，用于捕获游戏声音
        """
        try:
            # null sink 自带 monitor 源: {sink_name}.monitor
            logger.info(f"Monitor 源自动可用: {self.name}_sink.monitor")
            return True
        except Exception as e:
            logger.error(f"创建 monitor 源失败: {e}")
            return False

    def setup_full(self) -> bool:
        """完整设置虚拟音频环境"""
        ok = self.create_loopback_speaker_to_mic()
        if not self.is_windows:
            ok = ok and self._linux_create_mic_monitor()
        return ok

    def get_virtual_speaker_name(self) -> Optional[str]:
        """获取虚拟扬声器设备名"""
        if self.is_windows:
            return "CABLE Input (VB-Audio Virtual Cable)"
        else:
            return f"{self.name}_sink"

    def get_virtual_mic_monitor_name(self) -> Optional[str]:
        """获取虚拟扬声器 monitor 源（用于捕获游戏声音）"""
        if self.is_windows:
            return "CABLE Output (VB-Audio Virtual Cable)"
        else:
            return f"{self.name}_sink.monitor"

    def cleanup(self) -> None:
        """清理虚拟音频设备"""
        if not self.is_windows:
            try:
                result = self._run("pactl", "list", "short", "modules")
                for line in result.stdout.splitlines():
                    if self.name in line:
                        module_id = line.split()[0]
                        self._run("pactl", "unload-module", module_id)
                logger.info(f"已清理虚拟音频设备 '{self.name}'")
            except FileNotFoundError:
                pass  # pactl 不存在，无需清理
            except Exception as e:
                logger.warning(f"清理虚拟音频设备失败: {e}")
