"""Dialog: type text → translate → play TTS to selected output."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
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
    succeeded = pyqtSignal(str, object)  # translated, Path|None
    failed = pyqtSignal(str)


class TypedTranslateDialog(QDialog):
    translated = pyqtSignal(str, str)  # original, translated

    def __init__(
        self,
        *,
        source: str = "zh",
        target: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("打字翻译")
        self.setMinimumSize(480, 360)
        self._mp3_path: Path | None = None
        self._bridge = _WorkerBridge(self)
        self._bridge.succeeded.connect(self._on_ok)
        self._bridge.failed.connect(self._on_fail)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)

        root = QVBoxLayout(self)
        tip = QLabel(
            "输入文字 → 翻译 → 合成语音播放。\n"
            "同语言会跳过翻译直接播报。文本翻译使用在线轻量接口（非火山同传）。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("fieldLabel")
        root.addWidget(tip)

        lang_row = QHBoxLayout()
        self._cmb_src = QComboBox()
        self._cmb_tgt = QComboBox()
        for label, code in _LANGS:
            self._cmb_src.addItem(label, code)
            self._cmb_tgt.addItem(label, code)
        self._select(self._cmb_src, source)
        self._select(self._cmb_tgt, target)
        lang_row.addWidget(QLabel("原文"))
        lang_row.addWidget(self._cmb_src, 1)
        lang_row.addWidget(QLabel("译文"))
        lang_row.addWidget(self._cmb_tgt, 1)
        root.addLayout(lang_row)

        self._input = QTextEdit()
        self._input.setPlaceholderText("在这里输入要翻译并朗读的内容…")
        root.addWidget(self._input, 1)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("译文将显示在这里")
        self._output.setMaximumHeight(100)
        root.addWidget(self._output)

        btn_row = QHBoxLayout()
        self._btn_run = QPushButton("翻译并朗读")
        self._btn_run.setObjectName("primaryButton")
        self._btn_run.clicked.connect(self._on_run)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_run)
        root.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._status = QLabel("")
        self._status.setObjectName("fieldLabel")
        root.addWidget(self._status)

    @staticmethod
    def _select(combo: QComboBox, code: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == code:
                combo.setCurrentIndex(i)
                return

    def _on_run(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "打字翻译", "请先输入文字。")
            return
        src = self._cmb_src.currentData()
        tgt = self._cmb_tgt.currentData()
        self._btn_run.setEnabled(False)
        self._status.setText("处理中…")

        bridge = self._bridge

        def worker() -> None:
            try:
                translated, path = run_async(
                    translate_and_synthesize(text, source=src, target=tgt)
                )
                bridge.succeeded.emit(translated, path)
            except Exception as exc:
                bridge.failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ok(self, translated: str, path: object) -> None:
        self._mp3_path = path if isinstance(path, Path) else None
        self._output.setPlainText(translated)
        self._status.setText("完成")
        self._btn_run.setEnabled(True)
        original = self._input.toPlainText().strip()
        self.translated.emit(original, translated or "")
        if self._mp3_path is not None:
            self._player.setSource(QUrl.fromLocalFile(str(self._mp3_path)))
            self._audio.setVolume(1.0)
            self._player.play()

    def _on_fail(self, message: str) -> None:
        self._status.setText("失败")
        self._btn_run.setEnabled(True)
        QMessageBox.warning(self, "打字翻译失败", message)
