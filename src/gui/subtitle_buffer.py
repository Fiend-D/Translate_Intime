"""
字幕缓冲器 — 流式文本按句子聚合，避免碎片化显示
参考 SayHey 的 subtitle_buffer.py 设计
"""
import time
from dataclasses import dataclass, field
from typing import Optional


# 句子结束标点（中英文）
_SENTENCE_ENDS = frozenset("。！？!?.\n")


@dataclass
class SubtitleBuffer:
    """
    累积流式 ASR/翻译文本，在句末完整输出。

    用法:
        buf = SubtitleBuffer()
        for chunk in streaming_chunks:
            result = buf.feed(chunk)
            if result:
                display(result)  # 完整句子
    """

    _buffer: str = ""
    _last_activity: float = field(default_factory=time.monotonic)
    _flush_timeout: float = 0.7  # 0.7秒无新文本就刷出
    _max_len: int = 200  # 超长兜底

    def feed(self, text: str) -> Optional[str]:
        """
        喂入新文本，返回可显示的完整句子（可能为 None）。
        """
        if not text:
            return self._check_timeout()

        text = text.strip()
        self._last_activity = time.monotonic()

        # 和上次完全一样？跳过
        if text == self._buffer.strip():
            return None

        # 新文本是上次的扩展？
        if text.startswith(self._buffer.strip()):
            self._buffer = text
        else:
            # 引擎重置（新句子开始），先刷出旧缓冲
            flushed = self._flush()
            self._buffer = text
            return flushed

        return self._try_split()

    def _try_split(self) -> Optional[str]:
        """如果有完整句子则返回"""
        for i, ch in enumerate(self._buffer):
            if ch in _SENTENCE_ENDS:
                sentence = self._buffer[:i + 1].strip()
                self._buffer = self._buffer[i + 1:].lstrip()
                return sentence
        # 超长兜底：在逗号处分段
        if len(self._buffer) > self._max_len:
            comma_idx = self._buffer.find("，", self._max_len // 2)
            if comma_idx > 0:
                sentence = self._buffer[:comma_idx + 1].strip()
                self._buffer = self._buffer[comma_idx + 1:].lstrip()
                return sentence
        return None

    def _check_timeout(self) -> Optional[str]:
        """超时刷出剩余内容"""
        if self._buffer and time.monotonic() - self._last_activity > self._flush_timeout:
            return self._flush()
        return None

    def _flush(self) -> Optional[str]:
        """强制输出全部缓冲"""
        if not self._buffer.strip():
            return None
        text = self._buffer.strip()
        self._buffer = ""
        return text

    def reset(self) -> None:
        self._buffer = ""
        self._last_activity = time.monotonic()
