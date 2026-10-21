"""Token-based multi-theme stylesheets (Nola-inspired sidebar shell)."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from src.models.config import THEME_KEYS, ThemeKey, normalize_theme_key


@dataclass(frozen=True)
class Theme:
    key: ThemeKey
    label: str
    dark: bool
    bg: str
    sidebar: str
    surface: str
    surface_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_end: str
    accent_text: str
    accent_soft: str
    field: str
    preview: str
    scroll: str
    success: str
    danger: str
    danger_hover: str
    overlay_text: str
    overlay_bg: str


THEMES: dict[ThemeKey, Theme] = {
    t.key: t
    for t in (
        Theme(
            key="nola_light",
            label="晨雾蓝 · 浅色",
            dark=False,
            bg="#F3F6FC",
            sidebar="#EAF0FA",
            surface="#FFFFFF",
            surface_alt="#F1F5FC",
            border="#E0E7F3",
            text="#16233B",
            muted="#66748F",
            accent="#2F6BFF",
            accent_end="#5B8CFF",
            accent_text="#FFFFFF",
            accent_soft="rgba(47, 107, 255, 0.10)",
            field="#FFFFFF",
            preview="#F7F9FD",
            scroll="#C9D3E6",
            success="#16B364",
            danger="#E5484D",
            danger_hover="#CF3B40",
            overlay_text="#16233B",
            overlay_bg="#F5F8FF",
        ),
        Theme(
            key="nola_dark",
            label="夜空蓝 · 深色",
            dark=True,
            bg="#111A2E",
            sidebar="#0D1526",
            surface="#18233A",
            surface_alt="#1F2B45",
            border="#2A3856",
            text="#EEF3FF",
            muted="#97A5C3",
            accent="#6EA0FF",
            accent_end="#8DB5FF",
            accent_text="#0B1630",
            accent_soft="rgba(110, 160, 255, 0.14)",
            field="#1C2840",
            preview="#0F182B",
            scroll="#34446A",
            success="#3DD68C",
            danger="#FF6B6B",
            danger_hover="#E85A5A",
            overlay_text="#EEF3FF",
            overlay_bg="#0D1526",
        ),
        Theme(
            key="mint_dark",
            label="薄荷夜 · 深色",
            dark=True,
            bg="#0F1316",
            sidebar="#0B0E11",
            surface="#171C21",
            surface_alt="#1E252B",
            border="#2A333B",
            text="#EEF5F1",
            muted="#8D9A94",
            accent="#3DDB84",
            accent_end="#7AF0B1",
            accent_text="#06210F",
            accent_soft="rgba(61, 219, 132, 0.13)",
            field="#1C2228",
            preview="#0C1013",
            scroll="#34404A",
            success="#3DDB84",
            danger="#F0605D",
            danger_hover="#D9504D",
            overlay_text="#FFFFFF",
            overlay_bg="#141418",
        ),
        Theme(
            key="violet",
            label="霓虹紫 · 深色",
            dark=True,
            bg="#120F1F",
            sidebar="#0D0B17",
            surface="#1B1730",
            surface_alt="#241E3D",
            border="#332A55",
            text="#F3EEFF",
            muted="#A49BC4",
            accent="#A66BFF",
            accent_end="#FF6FD8",
            accent_text="#FFFFFF",
            accent_soft="rgba(166, 107, 255, 0.16)",
            field="#201A38",
            preview="#0F0C1C",
            scroll="#3E3466",
            success="#4ADE9D",
            danger="#FF5C7A",
            danger_hover="#E84D6A",
            overlay_text="#F6F0FF",
            overlay_bg="#140F24",
        ),
        Theme(
            key="sand",
            label="暖砂 · 浅色",
            dark=False,
            bg="#F7F3EE",
            sidebar="#EFE8DF",
            surface="#FFFDFA",
            surface_alt="#F6F0E8",
            border="#E6DCCF",
            text="#2B2118",
            muted="#857565",
            accent="#E07A2E",
            accent_end="#F2A65A",
            accent_text="#FFFFFF",
            accent_soft="rgba(224, 122, 46, 0.12)",
            field="#FFFDFA",
            preview="#FBF7F2",
            scroll="#D8C9B6",
            success="#3E9E5C",
            danger="#D9473F",
            danger_hover="#C23C35",
            overlay_text="#2B2118",
            overlay_bg="#FFF8EF",
        ),
        Theme(
            key="graphite",
            label="石墨 · 高对比",
            dark=True,
            bg="#0A0A0B",
            sidebar="#000000",
            surface="#151517",
            surface_alt="#1D1D20",
            border="#2E2E33",
            text="#FFFFFF",
            muted="#A1A1AA",
            accent="#FACC15",
            accent_end="#FDE047",
            accent_text="#111111",
            accent_soft="rgba(250, 204, 21, 0.14)",
            field="#19191C",
            preview="#09090A",
            scroll="#3A3A40",
            success="#22C55E",
            danger="#EF4444",
            danger_hover="#DC3535",
            overlay_text="#FFF7CC",
            overlay_bg="#000000",
        ),
    )
}

SYSTEM_LIGHT: ThemeKey = "nola_light"
SYSTEM_DARK: ThemeKey = "nola_dark"


def theme_choices() -> list[tuple[str, ThemeKey]]:
    """(label, key) pairs in display order, starting with "follow system"."""
    choices: list[tuple[str, ThemeKey]] = [("跟随系统", "system")]
    choices.extend((THEMES[k].label, k) for k in THEME_KEYS if k in THEMES)
    return choices


def system_prefers_dark() -> bool:
    """Best-effort OS colour scheme; unknown schemes fall back to the palette."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if not isinstance(app, QGuiApplication):
        return True
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return QGuiApplication.palette().window().color().lightness() < 128


def resolve_theme(mode: object, *, system_dark: bool | None = None) -> Theme:
    key = normalize_theme_key(mode)
    if key == "system":
        dark = system_prefers_dark() if system_dark is None else system_dark
        key = SYSTEM_DARK if dark else SYSTEM_LIGHT
    return THEMES[key]


@cache
def _theme_assets(theme: Theme) -> dict[str, str]:
    """Write per-theme SVG glyphs Qt stylesheets can reference by file URL."""
    root = Path(tempfile.gettempdir()) / "translator_intime_theme" / theme.key
    root.mkdir(parents=True, exist_ok=True)
    svgs = {
        "arrow": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
            f'<path d="M1 1.5l5 5 5-5" fill="none" stroke="{theme.muted}" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow_up": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
            f'<path d="M1 5l4-4 4 4" fill="none" stroke="{theme.muted}" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow_down": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
            f'<path d="M1 1l4 4 4-4" fill="none" stroke="{theme.muted}" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "switch_on": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20">'
            f'<rect width="36" height="20" rx="10" fill="{theme.accent}"/>'
            f'<circle cx="26" cy="10" r="7" fill="{theme.accent_text}"/></svg>'
        ),
        "switch_off": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20">'
            f'<rect x="0.5" y="0.5" width="35" height="19" rx="9.5" '
            f'fill="{theme.surface_alt}" stroke="{theme.border}"/>'
            f'<circle cx="10" cy="10" r="7" fill="{theme.muted}"/></svg>'
        ),
        "switch_disabled": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20" opacity="0.45">'
            f'<rect x="0.5" y="0.5" width="35" height="19" rx="9.5" '
            f'fill="{theme.surface_alt}" stroke="{theme.border}"/>'
            f'<circle cx="10" cy="10" r="7" fill="{theme.muted}"/></svg>'
        ),
        # Locked while a channel runs: still visibly "on", just dimmed.
        "switch_on_disabled": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="20" opacity="0.5">'
            f'<rect width="36" height="20" rx="10" fill="{theme.accent}"/>'
            f'<circle cx="26" cy="10" r="7" fill="{theme.accent_text}"/></svg>'
        ),
    }
    paths: dict[str, str] = {}
    for name, body in svgs.items():
        path = root / f"{name}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
        paths[name] = path.as_posix()
    return paths


def _font_stack() -> str:
    return (
        'font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", '
        '"Noto Sans CJK SC", "Helvetica Neue", sans-serif;'
    )


def build_stylesheet(t: Theme) -> str:
    svg = _theme_assets(t)
    return f"""
QWidget {{
    background-color: transparent;
    color: {t.text};
    {_font_stack()}
    font-size: 13px;
}}

QMainWindow, QDialog, QMessageBox, QColorDialog, QFileDialog, QInputDialog {{
    background-color: {t.bg};
    color: {t.text};
}}

QWidget#appRoot {{
    background-color: {t.bg};
}}

QFrame#sidebar {{
    background-color: {t.sidebar};
    border: none;
    border-right: 1px solid {t.border};
}}

QLabel#logoMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t.accent}, stop:1 {t.accent_end});
    color: {t.accent_text};
    border-radius: 10px;
    font-size: 15px;
    font-weight: 900;
}}

QLabel#appTitle {{
    font-size: 17px;
    font-weight: 800;
    color: {t.text};
}}

QLabel#appSubtitle {{
    font-size: 12px;
    color: {t.muted};
}}

QLabel#navGroup {{
    color: {t.muted};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 12px 12px 4px 12px;
}}

QPushButton#navButton {{
    text-align: left;
    background-color: transparent;
    color: {t.muted};
    border: none;
    border-radius: 10px;
    padding: 9px 12px;
    font-weight: 600;
}}

QPushButton#navButton:hover {{
    background-color: {t.surface_alt};
    color: {t.text};
}}

QPushButton#navButton:disabled {{
    background-color: transparent;
    border: none;
    color: {t.scroll};
}}

QPushButton#navButton:checked {{
    background-color: {t.accent_soft};
    color: {t.accent};
}}

QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 800;
    color: {t.text};
}}

QLabel#sectionTitle {{
    font-size: 15px;
    font-weight: 700;
    color: {t.text};
}}

QLabel#fieldLabel {{
    font-size: 12px;
    font-weight: 600;
    color: {t.muted};
}}

QLabel#statusLabel {{
    font-size: 12px;
    color: {t.muted};
}}

QLabel#pill {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 13px;
    padding: 4px 12px;
    font-size: 12px;
    color: {t.muted};
}}

QLabel#badgeLabel {{
    background-color: {t.accent_soft};
    color: {t.accent};
    border-radius: 13px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 700;
}}

QLabel#sourceLabel {{
    color: {t.muted};
    font-size: 11px;
    background: {t.surface_alt};
    border-radius: 6px;
    padding: 5px 8px;
}}

QLabel#subTitle {{
    font-size: 13px;
    font-weight: 700;
    color: {t.text};
    padding-top: 6px;
}}

QLabel#kindLocal, QLabel#kindCloud, QLabel#usageOn, QLabel#usageOff {{
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 700;
}}

QLabel#kindLocal {{
    background-color: {t.success};
    color: {t.accent_text};
}}

QLabel#kindCloud {{
    background-color: {t.accent};
    color: {t.accent_text};
}}

QLabel#usageOn {{
    background-color: {t.accent_soft};
    color: {t.accent};
}}

QLabel#usageOff {{
    border: 1px solid {t.border};
    color: {t.muted};
}}

QFrame#segBar {{
    background-color: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 12px;
}}

QPushButton#segButton {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 7px 18px;
    font-weight: 600;
    color: {t.muted};
}}

QPushButton#segButton:hover {{
    color: {t.text};
}}

QPushButton#segButton:checked {{
    background-color: {t.surface};
    color: {t.accent};
}}

QFrame#card, QFrame#logSidePanel {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 16px;
}}

QFrame#metricTile {{
    background-color: {t.surface_alt};
    border: none;
    border-radius: 12px;
}}

QLabel#metricValue {{
    font-size: 17px;
    font-weight: 800;
    color: {t.text};
}}

QLabel#metricSmall {{
    font-size: 12px;
    font-weight: 600;
    color: {t.text};
}}

QStackedWidget#pages, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

QFrame#previewBox {{
    background-color: {t.preview};
    border: 1px solid {t.border};
    border-radius: 14px;
}}

QLabel#previewText {{
    color: {t.muted};
    font-size: 14px;
    padding: 8px;
}}

QCheckBox {{
    spacing: 8px;
    font-size: 12px;
    color: {t.text};
}}

QCheckBox::indicator {{
    width: 36px;
    height: 20px;
    image: url({svg["switch_off"]});
}}

QCheckBox::indicator:checked {{
    image: url({svg["switch_on"]});
}}

QCheckBox::indicator:disabled {{
    image: url({svg["switch_disabled"]});
}}

QCheckBox::indicator:checked:disabled {{
    image: url({svg["switch_on_disabled"]});
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {t.field};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}

QComboBox {{
    combobox-popup: 0;
}}

QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{
    border-color: {t.accent};
}}

QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {t.muted};
    background-color: {t.surface_alt};
}}

QComboBox::drop-down {{
    border: none;
    width: 28px;
}}

QComboBox::down-arrow {{
    image: url({svg["arrow"]});
    width: 12px;
    height: 8px;
}}

QSpinBox, QDoubleSpinBox {{
    padding-right: 22px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    border: none;
    background: transparent;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    margin-top: 3px;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin-bottom: 3px;
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {t.accent_soft};
    border-radius: 4px;
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({svg["arrow_up"]});
    width: 10px;
    height: 6px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({svg["arrow_down"]});
    width: 10px;
    height: 6px;
}}

QComboBoxPrivateContainer {{
    background: transparent;
    border: none;
}}

QComboBox QAbstractItemView {{
    background-color: {t.surface};
    color: {t.text};
    selection-background-color: {t.accent_soft};
    selection-color: {t.accent};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 4px;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 2px 10px;
    border-radius: 6px;
    color: {t.text};
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: {t.surface_alt};
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {t.accent_soft};
    color: {t.accent};
}}

QPushButton {{
    background-color: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 7px 14px;
    font-weight: 600;
}}

QPushButton:hover {{
    border-color: {t.accent};
}}

QPushButton:disabled {{
    color: {t.muted};
    background-color: {t.surface_alt};
    border-color: {t.border};
}}

QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent}, stop:1 {t.accent_end});
    color: {t.accent_text};
    border: none;
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 800;
}}

QPushButton#primaryButton:hover {{
    background: {t.accent};
}}

QPushButton#primaryButton:disabled {{
    background: {t.surface_alt};
    color: {t.muted};
}}

QPushButton#dangerButton {{
    background-color: {t.danger};
    color: #FFFFFF;
    border: none;
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 800;
}}

QPushButton#dangerButton:hover {{
    background-color: {t.danger_hover};
}}

QPushButton#dangerButton:disabled {{
    background-color: {t.surface_alt};
    color: {t.muted};
}}

QPushButton#ghostButton {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 7px 12px;
}}

QPushButton#ghostButton:hover {{
    border-color: {t.accent};
    color: {t.accent};
}}

QPushButton#accentButton {{
    background-color: {t.accent_soft};
    color: {t.accent};
    border: none;
    border-radius: 10px;
    padding: 7px 14px;
    font-weight: 700;
}}

QPushButton#accentButton:hover {{
    background-color: {t.accent};
    color: {t.accent_text};
}}

QPushButton#iconButton {{
    background-color: {t.surface};
    color: {t.muted};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 6px 10px;
    min-width: 34px;
}}

QPushButton#iconButton:hover, QPushButton#iconButton:checked,
QPushButton#iconButton[active="true"] {{
    background-color: {t.accent_soft};
    color: {t.accent};
    border-color: {t.accent};
}}

QProgressBar {{
    border: none;
    background: {t.surface_alt};
    border-radius: 2px;
    max-height: 4px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent}, stop:1 {t.accent_end});
    border-radius: 2px;
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {t.surface_alt};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {t.accent};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {t.accent};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {t.scroll};
    border-radius: 4px;
    min-height: 30px;
    min-width: 30px;
}}

QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    height: 0;
    width: 0;
}}

QTextEdit, QListWidget, QTableWidget, QTreeWidget, QTableView {{
    background-color: {t.preview};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 12px;
    selection-background-color: {t.accent_soft};
    selection-color: {t.accent};
}}

QHeaderView::section {{
    background-color: {t.surface_alt};
    color: {t.muted};
    border: none;
    padding: 6px 8px;
    font-weight: 600;
}}

QTextEdit#logView {{
    color: {t.muted};
    padding: 8px;
    font-family: "Cascadia Code", "JetBrains Mono", "Noto Sans Mono", "Consolas", monospace;
    font-size: 11px;
}}

QMenu {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background-color: {t.accent_soft};
    color: {t.accent};
}}

QToolTip {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 6px 8px;
}}
"""


def polish_combo_popup(combo: Any) -> None:
    """Frameless, translucent popup so the rounded list has no native (black) rim."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QStyledItemDelegate

    if combo.property("_popupStyled"):
        return
    combo.setProperty("_popupStyled", True)
    combo.setItemDelegate(QStyledItemDelegate(combo))
    view = combo.view()
    if view is None:
        return
    container = view.window()
    container.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


def _combo_popup_filter() -> Any:
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtWidgets import QComboBox

    class ComboPopupFilter(QObject):
        def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
            if (
                isinstance(obj, QComboBox)
                and event is not None
                and event.type() == QEvent.Type.Polish
            ):
                polish_combo_popup(obj)
            return False

    return ComboPopupFilter


def install_combo_popup_styling(app: Any) -> Any:
    """Install once on the QApplication; returns the filter so callers keep it alive."""
    styler = _combo_popup_filter()(app)
    app.installEventFilter(styler)
    return styler


def menu_stylesheet(t: Theme) -> str:
    """Standalone QMenu QSS for top-level windows that don't inherit the main stylesheet."""
    return f"""
QMenu {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 4px;
    {_font_stack()}
    font-size: 13px;
}}
QMenu::item {{
    padding: 6px 22px 6px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {t.accent_soft};
    color: {t.accent};
}}
QMenu::separator {{
    height: 1px;
    background: {t.border};
    margin: 4px 8px;
}}
"""


def get_stylesheet(theme: Theme | str = "nola_dark") -> str:
    resolved = theme if isinstance(theme, Theme) else resolve_theme(theme)
    return build_stylesheet(resolved)


__all__ = [
    "SYSTEM_DARK",
    "SYSTEM_LIGHT",
    "THEMES",
    "Theme",
    "build_stylesheet",
    "get_stylesheet",
    "resolve_theme",
    "system_prefers_dark",
    "theme_choices",
]
