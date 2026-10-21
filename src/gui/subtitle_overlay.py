"""Desktop-lyrics style subtitle overlay: draggable, resizable, lockable."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import override

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QSize, Qt, QTimer, QVariantAnimation
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
    QGraphicsDropShadowEffect,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.models.enums import Direction

_MAX_HISTORY = 40
# Reference geometry: at this size text renders at the configured font size
_REF_WIDTH = 560
_REF_HEIGHT = 130
_MIN_SCALE = 0.5
_MAX_SCALE = 4.0
_MARGINS = (20, 14, 20, 16)
_SPACING = 4
OPACITY_PRESETS = (1.0, 0.85, 0.7, 0.5, 0.3, 0.15, 0.0)
# Reading time for a finished caption before the next one may replace it.
_DWELL_MIN_S = 1.2
_DWELL_MAX_S = 4.0
_CJK_CHARS_PER_S = 8.0
_LATIN_CHARS_PER_S = 17.0
# Captions waiting for their turn; older ones go straight to history beyond this.
_MAX_QUEUED = 3
# Engine placeholders that carry no text of their own.
_PLACEHOLDERS = frozenset({"…", "正在翻译…"})

DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_BG_COLOR = "#141418"
_DEFAULT_FAMILIES = ("SF Pro Display", "PingFang SC", "Helvetica Neue", "Microsoft YaHei UI")


def reading_time_s(text: str) -> float:
    """Seconds a viewer needs to read ``text`` (Chinese reads slower per character)."""
    cjk = sum(1 for ch in text if "\u3000" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    other = sum(1 for ch in text if ch.isalnum()) - cjk
    seconds = 0.4 + cjk / _CJK_CHARS_PER_S + max(0, other) / _LATIN_CHARS_PER_S
    return max(_DWELL_MIN_S, min(_DWELL_MAX_S, seconds))


def _parse_rgb(value: str, fallback: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b); invalid input uses ``fallback``."""
    for candidate in (value, fallback):
        raw = (candidate or "").strip().lstrip("#")
        if len(raw) == 6:
            try:
                return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
            except ValueError:
                continue
    return 255, 255, 255


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
        text_color: str = DEFAULT_TEXT_COLOR,
        background_color: str = DEFAULT_BG_COLOR,
        font_family: str = "",
        on_geometry_changed: Callable[[Direction, tuple[int, int, int, int]], None] | None = None,
        on_lock_changed: Callable[[Direction, bool], None] | None = None,
        on_font_size_requested: Callable[[int], None] | None = None,
        on_opacity_requested: Callable[[float], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._direction = direction
        self._text_rgb = _parse_rgb(text_color, DEFAULT_TEXT_COLOR)
        self._bg_rgb = _parse_rgb(background_color, DEFAULT_BG_COLOR)
        self._font_family = (font_family or "").strip()
        # Base size from settings; _font_size is the live size after window scaling
        self._base_font_size = max(12, min(72, int(font_size)))
        self._font_size = self._base_font_size
        self._scale = 1.0
        self._opacity = max(0.0, min(1.0, opacity))
        self._menu_style = ""
        self._locked = locked
        self._history_cap = max(0, min(_MAX_HISTORY, history_lines))
        self._show_original = show_original
        self._history: deque[tuple[str, str]] = deque(
            maxlen=max(1, self._history_cap) if self._history_cap else 1
        )
        self._current_orig = ""
        self._current_trans = ""
        self._was_final = True
        # Finished captions waiting until the current one has been on screen long enough.
        self._queue: deque[tuple[str, str]] = deque()
        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._advance_queue)
        self._dragging = False
        self._resizing = False
        self._drag_offset = QPoint()
        self._resize_start = QPoint()
        self._start_geometry = QRect()
        self._on_geometry_changed = on_geometry_changed
        self._on_lock_changed = on_lock_changed
        self._on_font_size_requested = on_font_size_requested
        self._on_opacity_requested = on_opacity_requested
        self._hover = False

        self._setup_window()
        self._setup_labels()
        self._sync_font_to_size(force=True)
        self.set_locked(locked)

    def _compute_scale(self) -> float:
        """Zoom factor vs. the reference window: grow both sides to magnify the text."""
        w = max(self.minimumWidth(), self.width())
        h = max(self.minimumHeight(), self.height())
        scale = min(w / float(_REF_WIDTH), h / float(_REF_HEIGHT))
        return max(_MIN_SCALE, min(_MAX_SCALE, scale))

    def _compute_font_size(self) -> int:
        return max(10, int(round(self._base_font_size * self._compute_scale())))

    def _sync_font_to_size(self, *, force: bool = False) -> bool:
        """Apply scaled font if it changed. Returns True when style was refreshed."""
        new_size = self._compute_font_size()
        if not force and new_size == self._font_size:
            return False
        self._font_size = new_size
        self._scale = self._compute_scale()
        self._apply_scaled_margins()
        self._apply_style()
        return True

    def _apply_scaled_margins(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        s = self._scale
        left, top, right, bottom = (max(6, int(round(v * s))) for v in _MARGINS)
        layout.setContentsMargins(left, top, right, bottom)
        self._body_layout.setSpacing(max(2, int(round(_SPACING * s))))

    def _history_px(self) -> int:
        return max(10, int(round(self._font_size * 0.65)))

    def _original_px(self) -> int:
        return max(11, int(round(self._font_size * 0.75)))

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
        layout.setContentsMargins(*_MARGINS)
        layout.setSpacing(0)
        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(_SPACING)
        layout.addWidget(self._body)

        self._history_label = QLabel("")
        self._original = QLabel("")
        self._translated = QLabel("等待语音…")
        # Speech still being recognized/translated; never replaces a finished caption.
        self._live = QLabel("")
        self._live.setVisible(False)
        self._history_label.setWordWrap(True)
        self._original.setWordWrap(True)
        self._translated.setWordWrap(True)
        self._live.setWordWrap(True)
        self._history_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
        )
        self._original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live.setAlignment(Qt.AlignmentFlag.AlignCenter)
        labels = (self._history_label, self._original, self._translated, self._live)

        # Keep labels content-sized so a tall window does not stretch line gaps
        tight = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        for lab in labels:
            lab.setSizePolicy(tight)

        # Per-label shadows keep text legible on a clear plate (Qt can't nest effects)
        self._shadows: list[QGraphicsDropShadowEffect] = []
        for lab in labels:
            shadow = QGraphicsDropShadowEffect(lab)
            shadow.setOffset(0, 1)
            lab.setGraphicsEffect(shadow)
            self._shadows.append(shadow)
        # Finalized lines fade in by animating the text alpha
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: self._set_translated_alpha(int(v)))

        # History + current packed at the bottom (lyric style)
        body = self._body_layout
        body.addStretch(1)
        body.addWidget(self._history_label, 0, Qt.AlignmentFlag.AlignBottom)
        body.addWidget(self._original, 0, Qt.AlignmentFlag.AlignBottom)
        body.addWidget(self._translated, 0, Qt.AlignmentFlag.AlignBottom)
        body.addWidget(self._live, 0, Qt.AlignmentFlag.AlignBottom)

    def _history_font(self) -> QFont:
        font = QFont()
        font.setFamilies(self._font_families())
        font.setPixelSize(self._history_px())
        return font

    def _content_width(self) -> int:
        layout = self.layout()
        m = layout.contentsMargins() if layout is not None else None
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
        spacing = self._body_layout.spacing()
        h = 0
        if self._show_original and self._current_orig:
            orig_font = QFont(self._translated.font())
            orig_font.setPixelSize(self._original_px())
            h += self._text_height(self._current_orig, orig_font, width) + spacing
        trans = self._current_trans or "等待语音…"
        h += self._text_height(trans, self._translated.font(), width)
        live = self._live.text()
        if live:
            live_font = QFont(self._translated.font())
            live_font.setPixelSize(self._original_px())
            h += spacing + self._text_height(live, live_font, width)
        return h

    def _history_budget_height(self) -> int:
        """Pixels available for archived lines (window height minus chrome + current)."""
        layout = self.layout()
        m = layout.contentsMargins() if layout else None
        spacing = self._body_layout.spacing()
        top = m.top() if m else _MARGINS[1]
        bottom = m.bottom() if m else _MARGINS[3]
        width = self._content_width()
        budget = self.height() - top - bottom
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
        # Text stays opaque; the opacity setting only affects the background plate
        self._history_label.setStyleSheet(
            f"color: {self._text_rgba(150)}; "
            f"font-size: {self._history_px()}px; background: transparent;"
        )
        self._original.setStyleSheet(
            f"color: {self._text_rgba(200)}; "
            f"font-size: {self._original_px()}px; background: transparent;"
        )
        self._original.setVisible(self._show_original)
        self._live.setStyleSheet(
            f"color: {self._text_rgba(140)}; font-style: italic; "
            f"font-size: {self._original_px()}px; background: transparent;"
        )
        self._set_translated_alpha(255 if self._was_final else 230)
        r, g, b = self._text_rgb
        dark_text = 0.299 * r + 0.587 * g + 0.114 * b < 128
        color = QColor(255, 255, 255, 170) if dark_text else QColor(0, 0, 0, 200)
        for shadow in self._shadows:
            shadow.setColor(color)
            shadow.setBlurRadius(max(4.0, 8 * self._scale))
        font = QFont()
        font.setFamilies(self._font_families())
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
        """Show a caption; partial text goes to the live line, finished text is queued.

        A finished caption stays on the main line for at least its reading time
        (``reading_time_s``) before the next one rolls it up into history, so a
        long passage translated sentence by sentence reads like roll-up captions
        instead of flashing past.
        """
        orig = (original or "").strip()
        trans = (translated or "").strip()
        if not is_final:
            live = trans if trans and trans not in _PLACEHOLDERS else orig
            self._set_live(live if live not in _PLACEHOLDERS else "")
            self._render_history()
            return
        self._set_live("")
        if not trans:
            return
        pair = (orig, trans)
        if pair == (self._current_orig, self._current_trans) or pair in self._queue:
            return
        if not self._dwell_timer.isActive():
            self._show_caption(pair)
            return
        self._queue.append(pair)
        while len(self._queue) > _MAX_QUEUED:
            # Falling behind the speaker: skip the main line, keep it readable in history.
            self._archive(self._queue.popleft())
        self._render_history()

    def _set_live(self, text: str) -> None:
        self._live.setText(text)
        self._live.setVisible(bool(text))

    def _archive(self, pair: tuple[str, str]) -> None:
        if self._history_cap > 0 and pair[1]:
            self._history.append(pair)

    def _show_caption(self, pair: tuple[str, str]) -> None:
        if self._current_trans:
            self._archive((self._current_orig, self._current_trans))
        self._current_orig, self._current_trans = pair
        self._original.setText(self._current_orig if self._show_original else "")
        self._translated.setText(self._current_trans)
        self._was_final = True
        self._anim.stop()
        self._anim.setStartValue(115)
        self._anim.setEndValue(255)
        self._anim.start()
        dwell = reading_time_s(self._current_trans)
        if self._queue:
            # Catch up while captions are waiting, but never below a glance.
            dwell = max(0.6, dwell / (1 + len(self._queue)))
        self._dwell_timer.start(int(dwell * 1000))
        self._render_history()

    def _advance_queue(self) -> None:
        if self._queue:
            self._show_caption(self._queue.popleft())

    def _set_translated_alpha(self, alpha: int) -> None:
        weight = 600 if self._was_final else 500
        self._translated.setStyleSheet(
            f"color: {self._text_rgba(alpha)}; "
            f"font-size: {self._font_size}px; font-weight: {weight}; background: transparent;"
        )

    def set_font_size(self, size: int) -> None:
        self._base_font_size = max(12, min(72, int(size)))
        self._sync_font_to_size(force=True)
        self._render_history()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def set_menu_style(self, stylesheet: str) -> None:
        """Theme QSS for the right-click menu (the overlay is a separate top-level window)."""
        self._menu_style = stylesheet

    def set_appearance(self, *, text_color: str, background_color: str, font_family: str) -> None:
        self._text_rgb = _parse_rgb(text_color, DEFAULT_TEXT_COLOR)
        self._bg_rgb = _parse_rgb(background_color, DEFAULT_BG_COLOR)
        self._font_family = (font_family or "").strip()
        self._apply_style()
        self._render_history()
        self.update()

    def _text_rgba(self, alpha: int) -> str:
        r, g, b = self._text_rgb
        return f"rgba({r},{g},{b},{max(0, min(255, int(alpha)))})"

    def _font_families(self) -> list[str]:
        families = list(_DEFAULT_FAMILIES)
        if self._font_family:
            families.insert(0, self._font_family)
        return families

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
        if self._menu_style:
            menu.setStyleSheet(self._menu_style)
        lock_action = menu.addAction("锁定（点击穿透）")
        menu.addSeparator()
        larger = menu.addAction("字体更大")
        smaller = menu.addAction("字体更小")
        opacity_menu = menu.addMenu("背景不透明度")
        opacity_actions: dict[object, float] = {}
        if opacity_menu is not None:
            for value in OPACITY_PRESETS:
                action = opacity_menu.addAction(f"{round(value * 100)}%")
                if action is not None:
                    action.setCheckable(True)
                    action.setChecked(abs(value - self._opacity) < 0.025)
                    opacity_actions[action] = value
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == lock_action:
            self.toggle_lock()
        elif chosen in (larger, smaller):
            step = 2 if chosen == larger else -2
            self._request_font_size(self._base_font_size + step)
        elif chosen in opacity_actions:
            self._request_opacity(opacity_actions[chosen])

    def _request_font_size(self, size: int) -> None:
        size = max(12, min(72, size))
        if self._on_font_size_requested is not None:
            self._on_font_size_requested(size)
        else:
            self.set_font_size(size)

    def _request_opacity(self, opacity: float) -> None:
        if self._on_opacity_requested is not None:
            self._on_opacity_requested(opacity)
        else:
            self.set_opacity(opacity)

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

        alpha = int(round(self._opacity * 255))
        border = int(round(self._opacity * 28))
        if self._hover and not self._locked:
            # Keep a fully transparent plate grabbable while the pointer is over it
            alpha = max(alpha, 70)
            border = max(border, 60)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*self._bg_rgb, alpha))
        painter.drawRoundedRect(rect, 16, 16)

        if border:
            painter.setPen(QColor(255, 255, 255, border))
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
    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent | None) -> None:
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
            if (
                new_geom.width() >= self.minimumWidth()
                and new_geom.height() >= self.minimumHeight()
            ):
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
