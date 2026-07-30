"""Desktop-lyrics style subtitle overlay: draggable, resizable, lockable."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import override

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.models.enums import Direction

_TITLES = {
    Direction.OUTBOUND: "麦克风",
    Direction.INBOUND: "游戏语音",
}

_MAX_HISTORY = 40
# Reference geometry for auto font scaling (matches default window size)
_REF_WIDTH = 560
_REF_HEIGHT = 130


class SubtitleOverlay(QWidget):
    """A translucent, always-on-top lyric window for one translation direction."""

    _CORNER_SIZE = 18
    _EDGE_MARGIN = 8

    def __init__(
        self,
        direction: Direction,
        *,
        font_size: int = 22,
        opacity: float = 0.88,
        locked: bool = False,
        history_lines: int = 2,
        show_original: bool = True,
        on_geometry_changed: Callable[[Direction, tuple[int, int, int, int]], None] | None = None,
        on_lock_changed: Callable[[Direction, bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._direction = direction
        # Base size from settings; _font_size is the live size after window scaling
        self._base_font_size = max(12, min(64, int(font_size)))
        self._font_size = self._base_font_size
        self._opacity = max(0.3, min(1.0, opacity))
        self._locked = locked
        self._history_cap = max(0, min(_MAX_HISTORY, history_lines))
        self._show_original = show_original
        self._history: deque[tuple[str, str]] = deque(
            maxlen=max(1, self._history_cap) if self._history_cap else 1
        )
        self._current_orig = ""
        self._current_trans = ""
        self._was_final = True
        self._pending_final_pair: tuple[str, str] | None = None
        self._finalize_timer = QTimer(self)
        self._finalize_timer.setSingleShot(True)
        self._finalize_timer.setInterval(800)
        self._finalize_timer.timeout.connect(self._confirm_pending_final)
        self._dragging = False
        self._resizing = False
        self._drag_offset = QPoint()
        self._resize_start = QPoint()
        self._start_geometry = QRect()
        self._on_geometry_changed = on_geometry_changed
        self._on_lock_changed = on_lock_changed
        self._hover = False

        self._setup_window()
        self._setup_labels()
        self._sync_font_to_size(force=True)
        self.set_locked(locked)

    def _compute_font_size(self) -> int:
        """Scale base font with window size (width-led so tall history doesn't explode)."""
        w = max(self.minimumWidth(), self.width())
        h = max(self.minimumHeight(), self.height())
        w_scale = w / float(_REF_WIDTH)
        # Height only mildly affects size; tall windows prefer more history lines
        h_scale = min(h / float(_REF_HEIGHT), 1.6)
        scale = max(0.55, min(2.5, 0.82 * w_scale + 0.18 * h_scale))
        return max(12, min(72, int(round(self._base_font_size * scale))))

    def _sync_font_to_size(self, *, force: bool = False) -> bool:
        """Apply scaled font if it changed. Returns True when style was refreshed."""
        new_size = self._compute_font_size()
        if not force and new_size == self._font_size:
            return False
        self._font_size = new_size
        self._apply_style()
        return True

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumSize(280, 80)

        # Default placement: mic top-center, game bottom-center-ish
        if self._direction == Direction.OUTBOUND:
            self.setGeometry(420, 80, 560, 130)
        else:
            self.setGeometry(420, 720, 560, 130)

    def _setup_labels(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(4)

        self._badge = QLabel(_TITLES.get(self._direction, ""))
        self._history_label = QLabel("")
        self._original = QLabel("")
        self._translated = QLabel("等待语音…")
        self._history_label.setWordWrap(True)
        self._original.setWordWrap(True)
        self._translated.setWordWrap(True)
        self._history_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Keep labels content-sized so a tall window does not stretch line gaps
        tight = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        for lab in (self._badge, self._history_label, self._original, self._translated):
            lab.setSizePolicy(tight)

        self._fade = QGraphicsOpacityEffect(self._translated)
        self._translated.setGraphicsEffect(self._fade)
        self._anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Badge top; stretch; history + current packed at bottom (lyric style)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        layout.addWidget(self._history_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(self._original, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(self._translated, 0, Qt.AlignmentFlag.AlignBottom)

    def _history_font(self) -> QFont:
        font = QFont()
        font.setFamilies(["SF Pro Display", "PingFang SC", "Helvetica Neue", "Microsoft YaHei UI"])
        font.setPixelSize(max(11, self._font_size - 8))
        return font

    def _content_width(self) -> int:
        m = self.layout().contentsMargins() if self.layout() else None
        left = m.left() if m else 20
        right = m.right() if m else 20
        return max(40, self.width() - left - right)

    def _text_height(self, text: str, font: QFont, width: int) -> int:
        if not text:
            return 0
        fm = QFontMetrics(font)
        return fm.boundingRect(0, 0, width, 10_000, Qt.TextFlag.TextWordWrap, text).height()

    def _entry_height(self, orig: str, trans: str, width: int) -> int:
        hist_font = self._history_font()
        h = 0
        if self._show_original and orig:
            h += self._text_height(orig, hist_font, width)
        if trans:
            h += self._text_height(trans, hist_font, width)
        return h + 6  # gap between archived utterances

    def _current_block_height(self, width: int) -> int:
        spacing = self.layout().spacing() if self.layout() else 4
        h = 0
        if self._show_original and self._current_orig:
            orig_font = QFont(self._translated.font())
            orig_font.setPixelSize(max(12, self._font_size - 6))
            h += self._text_height(self._current_orig, orig_font, width) + spacing
        trans = self._current_trans or "等待语音…"
        h += self._text_height(trans, self._translated.font(), width)
        return h

    def _history_budget_height(self) -> int:
        """Pixels available for archived lines (window height minus chrome + current)."""
        layout = self.layout()
        m = layout.contentsMargins() if layout else None
        spacing = layout.spacing() if layout else 4
        top = m.top() if m else 14
        bottom = m.bottom() if m else 16
        width = self._content_width()
        budget = self.height() - top - bottom
        budget -= self._badge.sizeHint().height() + spacing
        budget -= self._current_block_height(width) + spacing
        return max(0, budget)

    def _visible_history_entries(self) -> list[tuple[str, str]]:
        if self._history_cap <= 0 or not self._history:
            return []
        width = self._content_width()
        budget = self._history_budget_height()
        selected: list[tuple[str, str]] = []
        used = 0
        for orig, trans in reversed(self._history):
            need = self._entry_height(orig, trans, width)
            if selected and used + need > budget:
                break
            if not selected and need > budget:
                # Too short for even one archived line
                break
            selected.append((orig, trans))
            used += need
            if len(selected) >= self._history_cap:
                break
        selected.reverse()
        return selected

    def _apply_style(self) -> None:
        badge_color = "#3DDB84" if self._direction == Direction.INBOUND else "#64D2FF"
        self._badge.setStyleSheet(
            f"color: {badge_color}; font-size: 11px; font-weight: 600; "
            "letter-spacing: 1px; background: transparent;"
        )
        self._history_label.setStyleSheet(
            f"color: rgba(255,255,255,{int(self._opacity * 110)}); "
            f"font-size: {max(11, self._font_size - 8)}px; background: transparent;"
        )
        self._original.setStyleSheet(
            f"color: rgba(255,255,255,{int(self._opacity * 180)}); "
            f"font-size: {max(12, self._font_size - 6)}px; background: transparent;"
        )
        self._original.setVisible(self._show_original)
        self._translated.setStyleSheet(
            f"color: rgba(255,255,255,{int(self._opacity * 255)}); "
            f"font-size: {self._font_size}px; font-weight: 600; background: transparent;"
        )
        font = QFont()
        font.setFamilies(["SF Pro Display", "PingFang SC", "Helvetica Neue", "Microsoft YaHei UI"])
        font.setPixelSize(self._font_size)
        font.setWeight(QFont.Weight.DemiBold)
        self._translated.setFont(font)
        self._history_label.setFont(self._history_font())

    def set_history_lines(self, n: int) -> None:
        self._history_cap = max(0, min(_MAX_HISTORY, int(n)))
        kept = list(self._history)
        self._history = deque(
            kept[-self._history_cap :] if self._history_cap else [],
            maxlen=max(1, self._history_cap) if self._history_cap else 1,
        )
        if self._history_cap == 0:
            self._history.clear()
        self._render_history()

    def set_show_original(self, show: bool) -> None:
        self._show_original = bool(show)
        self._original.setVisible(self._show_original)
        self._render_history()

    def _render_history(self) -> None:
        entries = self._visible_history_entries()
        if not entries:
            self._history_label.setText("")
            self._history_label.setVisible(False)
            return
        lines: list[str] = []
        for orig, trans in entries:
            if self._show_original and orig:
                lines.append(f"{orig}\n{trans}")
            else:
                lines.append(trans)
        self._history_label.setVisible(True)
        self._history_label.setText("\n".join(lines))

    def set_text(self, original: str, translated: str, *, is_final: bool = True) -> None:
        orig = original or ""
        trans = translated or ""
        incoming_pair = (orig, trans)
        if (
            self._finalize_timer.isActive()
            and self._pending_final_pair is not None
            and self._pending_final_pair != incoming_pair
        ):
            self._confirm_pending_final()
        # After a finalized line, a new utterance archives the previous one
        if (
            self._history_cap > 0
            and self._was_final
            and self._current_trans
            and (orig != self._current_orig or trans != self._current_trans)
        ):
            self._history.append((self._current_orig, self._current_trans))
            self._render_history()

        self._current_orig = orig
        self._current_trans = trans
        self._original.setText(orig if self._show_original else "")
        self._translated.setText(trans)
        # A translated pair stays visually "in progress" for a short reading
        # window before the final confirmation animation and history rollover.
        if is_final and self._pending_final_pair != incoming_pair:
            self._pending_final_pair = incoming_pair
            self._was_final = False
            self._apply_interim_style()
            self._finalize_timer.start()
        elif not is_final:
            self._pending_final_pair = None
            self._finalize_timer.stop()
            self._was_final = False
            self._apply_interim_style()
        # Re-fit history if current block height changed
        self._render_history()

    def _apply_interim_style(self) -> None:
        alpha = int(self._opacity * 200)
        self._translated.setStyleSheet(
            f"color: rgba(255,255,255,{alpha}); "
            f"font-size: {self._font_size}px; font-weight: 500; background: transparent;"
        )
        self._anim.stop()
        self._fade.setOpacity(0.92)

    def _confirm_pending_final(self) -> None:
        pair = self._pending_final_pair
        if pair is None or pair != (self._current_orig, self._current_trans):
            return
        self._pending_final_pair = None
        self._finalize_timer.stop()
        self._was_final = True
        alpha = int(self._opacity * 255)
        self._translated.setStyleSheet(
            f"color: rgba(255,255,255,{alpha}); "
            f"font-size: {self._font_size}px; font-weight: 600; background: transparent;"
        )
        self._anim.stop()
        self._fade.setOpacity(0.45)
        self._anim.setStartValue(0.45)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._render_history()

    def set_font_size(self, size: int) -> None:
        self._base_font_size = max(12, min(64, int(size)))
        self._sync_font_to_size(force=True)
        self._render_history()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.3, min(1.0, opacity))
        self._apply_style()
        self.update()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        # Locked = click-through for gaming
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        if self._on_lock_changed:
            self._on_lock_changed(self._direction, locked)
        self.update()

    def is_locked(self) -> bool:
        return self._locked

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    def restore_geometry_tuple(self, geom: tuple[int, int, int, int] | list[int]) -> None:
        if len(geom) != 4:
            return
        x, y, w, h = (int(v) for v in geom)
        if w >= 200 and h >= 60 and x >= 0 and y >= 0:
            self.setGeometry(x, y, w, h)
            self._sync_font_to_size()
            self._render_history()

    def get_geometry(self) -> tuple[int, int, int, int]:
        g = self.geometry()
        return g.x(), g.y(), g.width(), g.height()

    def _notify_geometry(self) -> None:
        if self._on_geometry_changed:
            self._on_geometry_changed(self._direction, self.get_geometry())

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2c2c2e; color: white; border-radius: 8px; padding: 6px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background: #0a84ff; }"
        )
        lock_action = menu.addAction("解锁位置" if self._locked else "锁定（点击穿透）")
        larger = menu.addAction("字体更大")
        smaller = menu.addAction("字体更小")
        chosen = menu.exec(global_pos)
        if chosen == lock_action:
            self.toggle_lock()
        elif chosen == larger:
            self.set_font_size(self._base_font_size + 2)
        elif chosen == smaller:
            self.set_font_size(self._base_font_size - 2)

    @override
    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        self._sync_font_to_size()
        self._render_history()

    @override
    def sizeHint(self) -> QSize:
        return QSize(560, 130)

    @override
    def paintEvent(self, event: QPaintEvent | None) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        # Frosted dark lyric plate
        alpha = int(self._opacity * 170) if not self._hover or self._locked else int(self._opacity * 200)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, alpha))
        painter.drawRoundedRect(rect, 16, 16)

        # Soft border
        painter.setPen(QColor(255, 255, 255, 28))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 16, 16)

        if not self._locked:
            # Resize handle
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 90))
            painter.drawRoundedRect(
                rect.right() - self._CORNER_SIZE + 2,
                rect.bottom() - self._CORNER_SIZE + 2,
                self._CORNER_SIZE - 4,
                self._CORNER_SIZE - 4,
                4,
                4,
            )

    @override
    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    @override
    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self._locked:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            rect = self.rect()
            in_corner = (
                pos.x() >= rect.width() - self._CORNER_SIZE
                and pos.y() >= rect.height() - self._CORNER_SIZE
            )
            if in_corner:
                self._resizing = True
                self._resize_start = event.globalPosition().toPoint()
                self._start_geometry = self.geometry()
                self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
            else:
                self._dragging = True
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))

    @override
    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self._locked:
            return
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        elif self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start
            new_geom = self._start_geometry.adjusted(0, 0, delta.x(), delta.y())
            if new_geom.width() >= self.minimumWidth() and new_geom.height() >= self.minimumHeight():
                self.setGeometry(new_geom)
        else:
            pos = event.pos()
            rect = self.rect()
            in_corner = (
                pos.x() >= rect.width() - self._CORNER_SIZE
                and pos.y() >= rect.height() - self._CORNER_SIZE
            )
            self.setCursor(
                QCursor(Qt.CursorShape.SizeFDiagCursor)
                if in_corner
                else QCursor(Qt.CursorShape.OpenHandCursor)
            )

    @override
    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        del event
        if self._dragging or self._resizing:
            self._notify_geometry()
            self._render_history()
        self._dragging = False
        self._resizing = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self._locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_lock()
