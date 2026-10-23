"""Floating quick-phrase palette: click a phrase to type it into the focused input."""

from __future__ import annotations

from typing import override

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.subtitle_overlay import OPACITY_PRESETS
from src.models.config import QuickPhrase, QuickPhraseSettings

_HEADER_H = 26
_CORNER = 16
_MIN_W, _MIN_H = 180, 90
_DEFAULT_GEOMETRY = (60, 260, 300, 340)


def _families() -> list[str]:
    return ["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI"]


class PhraseChip(QWidget):
    """One clickable phrase; text that does not fit ends in "…"."""

    clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.phrase_id = ""
        self._main = ""
        self._sub = ""
        self._auto_enter = False
        self._font_size = 15
        self._opacity = 0.55
        self._hover = False
        self._pressed = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_phrase(
        self,
        phrase: QuickPhrase,
        *,
        translate: bool,
        font_size: int,
        opacity: float,
        hotkey: str = "",
    ) -> None:
        self.phrase_id = phrase.id
        self._main = phrase.output(translate=translate)
        self._sub = phrase.text if translate and phrase.translation else ""
        self._auto_enter = phrase.auto_enter
        self._font_size = font_size
        self._opacity = opacity
        tip = phrase.text if not self._sub else f"{phrase.translation}\n{phrase.text}"
        if phrase.auto_enter:
            tip += "\n（输入后自动回车）"
        if hotkey:
            tip += f"\n快捷键：{hotkey}"
        self.setToolTip(tip)
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def _fonts(self) -> tuple[QFont, QFont]:
        main = QFont()
        main.setFamilies(_families())
        main.setPixelSize(self._font_size)
        sub = QFont(main)
        sub.setPixelSize(max(9, round(self._font_size * 0.75)))
        return main, sub

    @override
    def sizeHint(self) -> QSize:
        main, sub = self._fonts()
        h = QFontMetrics(main).height() + 12
        if self._sub:
            h += QFontMetrics(sub).height() + 1
        return QSize(160, h)

    def display_texts(self) -> tuple[str, str]:
        """Main and secondary line exactly as painted (after eliding)."""
        main_font, sub_font = self._fonts()
        width = max(10, self._text_rect().width())
        main = QFontMetrics(main_font).elidedText(self._main, Qt.TextElideMode.ElideRight, width)
        sub = QFontMetrics(sub_font).elidedText(self._sub, Qt.TextElideMode.ElideRight, width)
        return main, sub

    def _text_rect(self) -> QRect:
        right = 10 + (14 if self._auto_enter else 0)
        return self.rect().adjusted(10, 6, -right, -6)

    @override
    def paintEvent(self, event: QPaintEvent | None) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = self._opacity + (0.2 if self._hover else 0.0) + (0.15 if self._pressed else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 24, round(min(1.0, alpha) * 255)))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        if self._hover:
            p.setPen(QColor(255, 255, 255, 90))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

        main_font, sub_font = self._fonts()
        main, sub = self.display_texts()
        rect = self._text_rect()
        p.setFont(main_font)
        p.setPen(QColor(255, 255, 255, 240))
        main_h = QFontMetrics(main_font).height()
        p.drawText(
            QRect(rect.left(), rect.top(), rect.width(), main_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            main,
        )
        if sub:
            p.setFont(sub_font)
            p.setPen(QColor(255, 255, 255, 140))
            p.drawText(
                QRect(rect.left(), rect.top() + main_h + 1, rect.width(), rect.height() - main_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                sub,
            )
        if self._auto_enter:
            p.setFont(sub_font)
            p.setPen(QColor(255, 255, 255, 110))
            p.drawText(
                self.rect().adjusted(0, 0, -8, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "⏎",
            )

    @override
    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: object) -> None:
        self._hover = self._pressed = False
        self.update()

    @override
    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            return
        super().mousePressEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_pressed, self._pressed = self._pressed, False
        self.update()
        if was_pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.phrase_id)


class PhraseOverlay(QWidget):
    """Always-on-top palette that never takes keyboard focus from the game."""

    phrase_clicked = pyqtSignal(str)
    group_changed = pyqtSignal(str)
    geometry_changed = pyqtSignal(tuple)
    lock_changed = pyqtSignal(bool)
    font_size_requested = pyqtSignal(int)
    opacity_requested = pyqtSignal(float)
    hide_requested = pyqtSignal()

    def __init__(self, settings: QuickPhraseSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        # Display labels of the "send phrase N" hotkeys, by position in the group
        self._hotkeys: list[str] = []
        self._locked = settings.overlay_locked
        self._hover = False
        self._dragging = False
        self._resizing = False
        self._drag_offset = QPoint()
        self._resize_start = QPoint()
        self._start_geometry = QRect()
        self._chips: list[PhraseChip] = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build()
        geometry = settings.overlay_geometry or _DEFAULT_GEOMETRY
        self.setGeometry(*geometry)
        self.set_settings(settings)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 8)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        header.setSpacing(2)
        self._btn_prev = self._arrow("‹", -1)
        self._btn_next = self._arrow("›", 1)
        self._title = QLabel()
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._title.setStyleSheet(
            "color: rgba(255,255,255,210); background: transparent; font-weight: 600;"
        )
        header.addWidget(self._btn_prev)
        header.addWidget(self._title, 1)
        header.addWidget(self._btn_next)
        root.addLayout(header)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,70); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
        )
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._list = QVBoxLayout(body)
        self._list.setContentsMargins(0, 0, 4, 0)
        self._list.setSpacing(4)
        self._list.addStretch(1)
        self._empty = QLabel("还没有常用语\n在主面板「锦囊」页添加")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: rgba(255,255,255,150); background: transparent;")
        self._list.insertWidget(0, self._empty)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

    def _arrow(self, text: str, step: int) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.setToolTip("切换分组")
        button.setStyleSheet(
            "QToolButton { color: rgba(255,255,255,200); background: transparent; border: none;"
            " font-size: 16px; padding: 0 6px; }"
            "QToolButton:hover { color: white; }"
        )
        button.clicked.connect(lambda: self._step_group(step))
        return button

    # ------------------------------------------------------------------ state
    def set_hotkeys(self, labels: list[str]) -> None:
        self._hotkeys = list(labels)
        self._sync_chips(self._settings.active_group().phrases)

    def set_settings(self, settings: QuickPhraseSettings) -> None:
        self._settings = settings
        group = settings.active_group()
        many = len(settings.groups) > 1
        self._btn_prev.setVisible(many)
        self._btn_next.setVisible(many)
        self._refresh_title()
        self._sync_chips(group.phrases)
        self.update()

    def _refresh_title(self) -> None:
        group = self._settings.active_group()
        lock = "🔒 " if self._locked else ""
        self._title.setText(f"{lock}{group.name} · {len(group.phrases)}")

    def _sync_chips(self, phrases: list[QuickPhrase]) -> None:
        while len(self._chips) < len(phrases):
            chip = PhraseChip()
            chip.clicked.connect(self.phrase_clicked)
            self._list.insertWidget(self._list.count() - 1, chip)
            self._chips.append(chip)
        while len(self._chips) > len(phrases):
            chip = self._chips.pop()
            self._list.removeWidget(chip)
            chip.deleteLater()
        s = self._settings
        for index, (chip, phrase) in enumerate(zip(self._chips, phrases, strict=True)):
            chip.set_phrase(
                phrase,
                translate=s.translate,
                font_size=s.font_size,
                opacity=s.chip_opacity,
                hotkey=self._hotkeys[index] if index < len(self._hotkeys) else "",
            )
        self._empty.setVisible(not phrases)

    def chips(self) -> list[PhraseChip]:
        return list(self._chips)

    def _step_group(self, step: int) -> None:
        names = [g.name for g in self._settings.groups]
        if len(names) < 2:
            return
        current = self._settings.active_group().name
        index = (names.index(current) + step) % len(names)
        self.group_changed.emit(names[index])

    def is_locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        if locked == self._locked:
            return
        self._locked = locked
        self._refresh_title()
        self.update()
        self.lock_changed.emit(locked)

    def get_geometry(self) -> tuple[int, int, int, int]:
        g = self.geometry()
        return g.x(), g.y(), g.width(), g.height()

    # ------------------------------------------------------------------ input
    def _in_header(self, pos: QPoint) -> bool:
        return pos.y() <= _HEADER_H

    def _in_corner(self, pos: QPoint) -> bool:
        r = self.rect()
        return pos.x() >= r.width() - _CORNER and pos.y() >= r.height() - _CORNER

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        lock = menu.addAction("解锁（可拖动/缩放）" if self._locked else "锁定位置和大小")
        menu.addSeparator()
        larger = menu.addAction("字体更大")
        smaller = menu.addAction("字体更小")
        opacity_menu = menu.addMenu("底色不透明度")
        opacity_actions: dict[object, float] = {}
        if opacity_menu is not None:
            for value in OPACITY_PRESETS:
                action = opacity_menu.addAction(f"{round(value * 100)}%")
                if action is not None:
                    action.setCheckable(True)
                    action.setChecked(abs(value - self._settings.chip_opacity) < 0.025)
                    opacity_actions[action] = value
        menu.addSeparator()
        hide = menu.addAction("隐藏悬浮窗")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == lock:
            self.set_locked(not self._locked)
        elif chosen in (larger, smaller):
            step = 1 if chosen == larger else -1
            self.font_size_requested.emit(max(10, min(40, self._settings.font_size + step)))
        elif chosen in opacity_actions:
            self.opacity_requested.emit(opacity_actions[chosen])
        elif chosen == hide:
            self.hide_requested.emit()

    @override
    def contextMenuEvent(self, event: object) -> None:
        self._show_menu(QCursor.pos())

    @override
    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton or self._locked:
            return
        pos = event.position().toPoint()
        if self._in_corner(pos):
            self._resizing = True
            self._resize_start = event.globalPosition().toPoint()
            self._start_geometry = self.geometry()
        elif self._in_header(pos):
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    @override
    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        elif self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start
            g = self._start_geometry.adjusted(0, 0, delta.x(), delta.y())
            self.resize(max(_MIN_W, g.width()), max(_MIN_H, g.height()))
        elif not self._locked:
            pos = event.position().toPoint()
            if self._in_corner(pos):
                self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            elif self._in_header(pos):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    @override
    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        del event
        if self._dragging or self._resizing:
            self.geometry_changed.emit(self.get_geometry())
        self._dragging = self._resizing = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and self._in_header(event.position().toPoint()):
            self.set_locked(not self._locked)

    @override
    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.update()

    @override
    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        for chip in self._chips:
            chip.update()

    @override
    def paintEvent(self, event: QPaintEvent | None) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        # Fully transparent pixels would let clicks fall through to the game, so the
        # header keeps a faint plate as a grab handle.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 24, 150 if self._hover else 90))
        p.drawRoundedRect(QRect(rect.left(), rect.top(), rect.width(), _HEADER_H), 8, 8)
        if self._hover and not self._locked:
            p.setPen(QColor(255, 255, 255, 70))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 110))
            p.drawRoundedRect(
                rect.right() - _CORNER + 3,
                rect.bottom() - _CORNER + 3,
                _CORNER - 5,
                _CORNER - 5,
                3,
                3,
            )


__all__ = ["PhraseChip", "PhraseOverlay"]
