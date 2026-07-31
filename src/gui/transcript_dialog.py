"""Search and export locally archived translation records."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.utils.logger import (
    TranscriptLine,
    format_transcript_markdown,
    list_subtitle_logs,
    read_transcript_file,
)


class TranscriptDialog(QDialog):
    """Browse one subtitle archive at a time with text filtering and export."""

    def __init__(self, log_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("翻译记录")
        self.setMinimumSize(760, 480)
        self._log_dir = log_dir.expanduser()
        self._rows: list[TranscriptLine] = []
        self._filtered_rows: list[TranscriptLine] = []

        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self._cmb_file = QComboBox()
        self._cmb_file.setToolTip("选择一个会话记录或按日归档")
        self._txt_search = QLineEdit()
        self._txt_search.setPlaceholderText("搜索原文或译文")
        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("将当前筛选结果导出为 Markdown")
        toolbar.addWidget(QLabel("记录"))
        toolbar.addWidget(self._cmb_file, 1)
        toolbar.addWidget(self._txt_search, 1)
        toolbar.addWidget(self._btn_export)
        root.addLayout(toolbar)

        self._summary = QLabel("")
        self._summary.setObjectName("fieldLabel")
        root.addWidget(self._summary)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["时间", "通道", "原文", "译文"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setWordWrap(True)
        header = self._table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(True)
        header.resizeSection(0, 150)
        header.resizeSection(1, 70)
        header.resizeSection(2, 240)
        root.addWidget(self._table, 1)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._cmb_file.currentIndexChanged.connect(self._load_selected_file)
        self._txt_search.textChanged.connect(self._apply_filter)
        self._btn_export.clicked.connect(self._export_current)
        self._populate_files()

    def _populate_files(self) -> None:
        files = list_subtitle_logs(self._log_dir)
        self._cmb_file.blockSignals(True)
        self._cmb_file.clear()
        for path in files:
            self._cmb_file.addItem(path.name, str(path))
        self._cmb_file.blockSignals(False)
        self._cmb_file.setEnabled(bool(files))
        self._btn_export.setEnabled(bool(files))
        if files:
            self._load_selected_file()
        else:
            self._rows = []
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
        self._render_rows(self._filtered_rows)
        self._summary.setText(f"显示 {len(self._filtered_rows)} 条 / 共 {len(self._rows)} 条")

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
        if not self._filtered_rows:
            QMessageBox.information(self, "导出", "当前没有可导出的记录。")
            return
        default_path = self._log_dir / "翻译记录.md"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出翻译记录",
            str(default_path),
            "Markdown (*.md);;Text (*.txt)",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(
                format_transcript_markdown(self._filtered_rows),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {len(self._filtered_rows)} 条记录。")
