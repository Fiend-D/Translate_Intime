"""Search and export locally archived translation records (embedded page)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.utils.logger import (
    SubtitleText,
    TranscriptLine,
    format_transcript,
    list_subtitle_logs,
    read_transcript_file,
)

_MAX_RENDER_ROWS = 2000
_EXPORT_FILTERS = "SRT 字幕 (*.srt);;WebVTT 字幕 (*.vtt);;纯文本 (*.txt);;Markdown (*.md)"


def _suffix_for_filter(selected: str) -> str:
    for suffix in (".srt", ".vtt", ".txt", ".md"):
        if suffix in selected:
            return suffix
    return ".srt"


class TranscriptPanel(QWidget):
    """Browse one subtitle archive at a time with text filtering and export."""

    def __init__(self, log_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_dir = log_dir.expanduser()
        self._rows: list[TranscriptLine] = []
        self._filtered_rows: list[TranscriptLine] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        toolbar = QHBoxLayout()
        self._cmb_file = QComboBox()
        self._cmb_file.setToolTip("选择一个会话记录或按日归档")
        self._txt_search = QLineEdit()
        self._txt_search.setPlaceholderText("搜索原文或译文")
        self._cmb_cue_text = QComboBox()
        self._cmb_cue_text.addItem("双语字幕", "both")
        self._cmb_cue_text.addItem("仅原文", "original")
        self._cmb_cue_text.addItem("仅译文", "translated")
        self._cmb_cue_text.setToolTip("导出 SRT / WebVTT 时每条字幕包含的内容")
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setToolTip("重新读取留档目录（新会话、新内容）")
        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("导出当前筛选结果：SRT / WebVTT / TXT / Markdown")
        toolbar.addWidget(QLabel("记录"))
        toolbar.addWidget(self._cmb_file, 1)
        toolbar.addWidget(self._txt_search, 1)
        toolbar.addWidget(self._cmb_cue_text)
        toolbar.addWidget(self._btn_refresh)
        toolbar.addWidget(self._btn_export)
        root.addLayout(toolbar)

        self._summary = QLabel("")
        self._summary.setObjectName("fieldLabel")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["时间", "通道", "原文", "译文"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setWordWrap(True)
        self._table.setMinimumHeight(240)
        header = self._table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(True)
        header.resizeSection(0, 150)
        header.resizeSection(1, 70)
        header.resizeSection(2, 240)
        root.addWidget(self._table, 1)

        self._cmb_file.currentIndexChanged.connect(self._load_selected_file)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._apply_filter)
        self._txt_search.textChanged.connect(self._search_timer.start)
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_export.clicked.connect(self._export_current)
        self.refresh()

    def set_log_dir(self, log_dir: Path) -> None:
        self._log_dir = log_dir.expanduser()
        self.refresh()

    def refresh(self) -> None:
        """Re-scan the archive folder, keeping the selected file when it still exists."""
        current = self._cmb_file.currentData()
        files = list_subtitle_logs(self._log_dir)
        self._cmb_file.blockSignals(True)
        self._cmb_file.clear()
        for path in files:
            self._cmb_file.addItem(path.name, str(path))
        index = self._cmb_file.findData(current) if current else -1
        if index >= 0:
            self._cmb_file.setCurrentIndex(index)
        self._cmb_file.blockSignals(False)
        self._cmb_file.setEnabled(bool(files))
        self._btn_export.setEnabled(bool(files))
        if files:
            self._load_selected_file()
        else:
            self._rows = []
            self._filtered_rows = []
            self._render_rows([])
            self._summary.setText("暂无翻译记录")

    def _load_selected_file(self, *_args: object) -> None:
        raw_path = self._cmb_file.currentData()
        self._rows = read_transcript_file(Path(raw_path)) if raw_path else []
        self._apply_filter()

    def _apply_filter(self, *_args: object) -> None:
        query = self._txt_search.text().strip().casefold()
        if query:
            self._filtered_rows = [
                row
                for row in self._rows
                if query in row.original.casefold()
                or query in row.translated.casefold()
                or query in row.direction_label.casefold()
            ]
        else:
            self._filtered_rows = list(self._rows)
        shown = self._filtered_rows[-_MAX_RENDER_ROWS:]
        self._render_rows(shown)
        summary = f"匹配 {len(self._filtered_rows)} 条 / 共 {len(self._rows)} 条"
        if len(shown) < len(self._filtered_rows):
            summary += f"（仅显示最近 {len(shown)} 条，可搜索缩小范围；导出包含全部匹配）"
        self._summary.setText(summary)

    def _render_rows(self, rows: list[TranscriptLine]) -> None:
        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                row.direction_label,
                row.original,
                row.translated,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                )
                self._table.setItem(index, column, item)
        self._table.resizeRowsToContents()

    def _export_current(self) -> None:
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._apply_filter()
        if not self._filtered_rows:
            self._summary.setText("当前没有可导出的记录。")
            return
        default_path = self._log_dir / "翻译记录.srt"
        filename, selected = QFileDialog.getSaveFileName(
            self,
            "导出翻译记录",
            str(default_path),
            _EXPORT_FILTERS,
        )
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(_suffix_for_filter(selected))
        selected_text = self._cmb_cue_text.currentData()
        cue_text: SubtitleText = (
            selected_text if selected_text in ("original", "translated") else "both"
        )
        try:
            path.write_text(
                format_transcript(self._filtered_rows, path.suffix, text=cue_text),
                encoding="utf-8",
            )
        except OSError as exc:
            self._summary.setText(f"导出失败：{exc}")
            return
        self._summary.setText(f"已导出 {len(self._filtered_rows)} 条记录 → {path}")
