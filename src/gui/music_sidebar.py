"""Transparent sidebar for one-click music track switching while sharing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MusicSidebarOverlay(QWidget):
    """Right-edge translucent playlist for in-game music switching."""

    def __init__(
        self,
        *,
        on_select: Callable[[int], None] | None = None,
        on_prev: Callable[[], None] | None = None,
        on_next: Callable[[], None] | None = None,
        on_pause: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_select = on_select
        self._on_prev = on_prev
        self._on_next = on_next
        self._on_pause = on_pause
        self._on_stop = on_stop
        self._syncing = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumWidth(220)
        self.setMaximumWidth(300)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 12, 10, 12)
        root.setSpacing(8)

        self._panel = QWidget(self)
        self._panel.setObjectName("musicSidebarPanel")
        self._panel.setStyleSheet(
            "#musicSidebarPanel {"
            "  background: rgba(18, 18, 22, 170);"
            "  border-radius: 14px;"
            "  border: 1px solid rgba(255,255,255,0.12);"
            "}"
            "QLabel { color: rgba(255,255,255,0.92); background: transparent; }"
            "QListWidget {"
            "  background: transparent; color: rgba(255,255,255,0.88);"
            "  border: none; outline: none;"
            "}"
            "QListWidget::item {"
            "  padding: 8px 10px; border-radius: 8px; margin: 1px 0;"
            "}"
            "QListWidget::item:selected {"
            "  background: rgba(10, 132, 255, 0.55);"
            "}"
            "QListWidget::item:hover {"
            "  background: rgba(255,255,255,0.10);"
            "}"
            "QPushButton {"
            "  background: rgba(255,255,255,0.10); color: white;"
            "  border: none; border-radius: 6px; padding: 5px 4px;"
            "  font-size: 11px; min-height: 26px;"
            "}"
            "QPushButton:hover { background: rgba(255,255,255,0.18); }"
        )
        root.addWidget(self._panel)

        lay = QVBoxLayout(self._panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("音乐分享")
        font = QFont()
        font.setFamilies(["SF Pro Display", "PingFang SC", "Microsoft YaHei UI", "sans-serif"])
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        lay.addWidget(title)

        self._now = QLabel("未播放")
        self._now.setWordWrap(True)
        self._now.setStyleSheet("color: rgba(255,255,255,0.65); font-size: 12px;")
        lay.addWidget(self._now)

        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list, 1)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        btn_prev = QPushButton("上一首")
        btn_next = QPushButton("下一首")
        btn_pause = QPushButton("暂停")
        btn_stop = QPushButton("停止")
        btn_prev.clicked.connect(lambda: self._on_prev and self._on_prev())
        btn_pause.clicked.connect(lambda: self._on_pause and self._on_pause())
        btn_next.clicked.connect(lambda: self._on_next and self._on_next())
        btn_stop.clicked.connect(lambda: self._on_stop and self._on_stop())
        row1.addWidget(btn_prev)
        row1.addWidget(btn_next)
        row2.addWidget(btn_pause)
        row2.addWidget(btn_stop)
        lay.addLayout(row1)
        lay.addLayout(row2)

        tip = QLabel("点击曲目即可切换")
        tip.setStyleSheet("color: rgba(255,255,255,0.45); font-size: 11px;")
        lay.addWidget(tip)

    def set_tracks(self, paths: list[Path], current_index: int = -1) -> None:
        self._syncing = True
        self._list.clear()
        for path in paths:
            item = QListWidgetItem(path.stem)
            item.setToolTip(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._list.addItem(item)
        if 0 <= current_index < self._list.count():
            self._list.setCurrentRow(current_index)
            self._now.setText(paths[current_index].stem)
        self._syncing = False

    def set_current_index(self, index: int, *, name: str = "") -> None:
        self._syncing = True
        if 0 <= index < self._list.count():
            self._list.setCurrentRow(index)
        if name:
            self._now.setText(name)
        elif 0 <= index < self._list.count():
            item = self._list.item(index)
            if item is not None:
                self._now.setText(item.text())
        self._syncing = False

    def place_right_edge(self, *, width: int = 260, margin: int = 12) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(width, 420)
            return
        geo = screen.availableGeometry()
        h = min(520, max(320, geo.height() - 80))
        self.setGeometry(geo.right() - width - margin, geo.top() + 60, width, h)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if self._syncing or self._on_select is None:
            return
        row = self._list.row(item)
        if row >= 0:
            self._on_select(row)
