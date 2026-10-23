"""Embedded page: type text → translate → play TTS."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.typed_translate import run_async, translate_and_synthesize

_LANGS = [
    ("中文 zh", "zh"),
    ("English en", "en"),
    ("日本語 ja", "ja"),
    ("한국어 ko", "ko"),
]


class _WorkerBridge(QObject):
    succeeded = pyqtSignal(str, object)  # translated, bytes|None
    failed = pyqtSignal(str)


class TypedTranslatePanel(QWidget):
    translated = pyqtSignal(str, str)  # original, translated

    def __init__(
        self,
        *,
        source: str = "zh",
        target: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._audio_bytes: QByteArray | None = None
        self._audio_buffer: QBuffer | None = None
        self._busy = False
        self._bridge = _WorkerBridge(self)
        self._bridge.succeeded.connect(self._on_ok)
        self._bridge.failed.connect(self._on_fail)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        tip = QLabel(
            "输入文字 → 翻译 → 合成语音播放。"
            "同语言会跳过翻译直接播报。文本翻译使用在线轻量接口（非火山同传）。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("appSubtitle")
        root.addWidget(tip)

        lang_row = QHBoxLayout()
        self._cmb_src = QComboBox()
        self._cmb_tgt = QComboBox()
        for label, code in _LANGS:
            self._cmb_src.addItem(label, code)
            self._cmb_tgt.addItem(label, code)
        self.set_languages(source, target)
        lang_row.addWidget(QLabel("原文"))
        lang_row.addWidget(self._cmb_src, 1)
        lang_row.addWidget(QLabel("译文"))
        lang_row.addWidget(self._cmb_tgt, 1)
        root.addLayout(lang_row)

        self._input = QTextEdit()
        # Pasted rich text keeps its source colour (often black on a dark theme).
        self._input.setAcceptRichText(False)
        self._input.setPlaceholderText("在这里输入要翻译并朗读的内容…")
        self._input.setMinimumHeight(120)
        root.addWidget(self._input, 2)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("译文将显示在这里")
        self._output.setMinimumHeight(80)
        root.addWidget(self._output, 1)

        btn_row = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("fieldLabel")
        self._status.setWordWrap(True)
        btn_row.addWidget(self._status, 1)
        self._btn_stop = QPushButton("停止朗读")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._release_audio)
        btn_row.addWidget(self._btn_stop)
        self._btn_run = QPushButton("翻译并朗读")
        self._btn_run.setObjectName("primaryButton")
        self._btn_run.clicked.connect(self._on_run)
        btn_row.addWidget(self._btn_run)
        root.addLayout(btn_row)

    def set_languages(self, source: str, target: str) -> None:
        self._select(self._cmb_src, source)
        self._select(self._cmb_tgt, target)

    @staticmethod
    def _select(combo: QComboBox, code: str) -> None:
        index = combo.findData(code)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_run(self) -> None:
        if self._busy:
            return
        text = self._input.toPlainText().strip()
        if not text:
            self._status.setText("请先输入文字。")
            self._input.setFocus()
            return
        src = self._cmb_src.currentData()
        tgt = self._cmb_tgt.currentData()
        self._busy = True
        self._btn_run.setEnabled(False)
        self._status.setText("处理中…")

        bridge = self._bridge

        def worker() -> None:
            try:
                translated, audio = run_async(
                    translate_and_synthesize(text, source=src, target=tgt)
                )
                bridge.succeeded.emit(translated, audio)
            except Exception as exc:
                bridge.failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True, name="typed-translate").start()

    def _on_ok(self, translated: str, audio: object) -> None:
        self._busy = False
        self._release_audio()
        self._output.setPlainText(translated)
        self._status.setText("完成")
        self._btn_run.setEnabled(True)
        original = self._input.toPlainText().strip()
        self.translated.emit(original, translated or "")
        if isinstance(audio, bytes) and audio:
            self._audio_bytes = QByteArray(audio)
            self._audio_buffer = QBuffer(self)
            self._audio_buffer.setData(self._audio_bytes)
            self._audio_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self._player.setSourceDevice(self._audio_buffer, QUrl("memory://typed-tts.mp3"))
            self._audio.setVolume(1.0)
            self._player.play()
            self._btn_stop.setEnabled(True)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in {
            QMediaPlayer.MediaStatus.EndOfMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        }:
            self._release_audio()

    def _release_audio(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        if self._audio_buffer is not None:
            self._audio_buffer.close()
            self._audio_buffer.deleteLater()
        self._audio_buffer = None
        self._audio_bytes = None
        self._btn_stop.setEnabled(False)

    def shutdown(self) -> None:
        """Stop playback before the window closes."""
        self._release_audio()

    def _on_fail(self, message: str) -> None:
        self._busy = False
        self._status.setText(f"失败：{message}")
        self._btn_run.setEnabled(True)
