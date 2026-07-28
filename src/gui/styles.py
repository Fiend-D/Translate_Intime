"""SayHey-inspired dual theme stylesheets (dark mint / light mint)."""

from __future__ import annotations

from typing import Literal

ThemeMode = Literal["dark", "light"]

# Shared accent — mint / seafoam green from SayHey
_ACCENT = "#3DDB84"
_ACCENT_HOVER = "#32C574"
_ACCENT_PRESSED = "#28A862"
_ACCENT_TEXT = "#0B1F14"


def _common_font() -> str:
    return (
        'font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", '
        '"Helvetica Neue", sans-serif;'
    )


def _build_theme(
    *,
    bg: str,
    panel: str,
    panel_alt: str,
    border: str,
    text: str,
    text_muted: str,
    input_bg: str,
    input_border: str,
    preview_bg: str,
    badge_bg: str,
    badge_text: str,
    secondary_btn: str,
    secondary_btn_hover: str,
    danger: str,
    danger_hover: str,
    scroll: str,
) -> str:
    return f"""
QMainWindow {{
    background-color: {bg};
    color: {text};
}}

QWidget {{
    background-color: transparent;
    color: {text};
    {_common_font()}
    font-size: 13px;
}}

QLabel#appTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {text};
    letter-spacing: 0.2px;
}}

QLabel#appSubtitle {{
    font-size: 12px;
    color: {text_muted};
}}

QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {text};
}}

QLabel#fieldLabel {{
    font-size: 12px;
    color: {text_muted};
}}

QLabel#statusLabel {{
    font-size: 12px;
    color: {text_muted};
}}

QLabel#badgeLabel {{
    background-color: {badge_bg};
    color: {badge_text};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QFrame#card {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 12px;
}}

QFrame#headerBar {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 12px;
}}

QTabWidget#mainTabs::pane {{
    border: 1px solid {border};
    border-radius: 12px;
    top: -1px;
    background: {panel};
    padding: 4px;
}}

QTabWidget#mainTabs > QTabBar::tab {{
    background: {panel_alt};
    color: {text_muted};
    border: 1px solid {border};
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    padding: 8px 16px;
    margin-right: 4px;
    min-width: 72px;
}}

QTabWidget#mainTabs > QTabBar::tab:selected {{
    background: {panel};
    color: {text};
    font-weight: 600;
}}

QTabWidget#mainTabs > QTabBar::tab:hover {{
    color: {text};
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QFrame#previewBox {{
    background-color: {preview_bg};
    border: 1px solid {border};
    border-radius: 10px;
}}

QLabel#previewText {{
    color: {text_muted};
    font-size: 13px;
    padding: 8px;
}}

QCheckBox {{
    spacing: 8px;
    font-size: 12px;
    color: {text};
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {input_border};
    background: {input_bg};
}}

QCheckBox::indicator:checked {{
    background: {_ACCENT};
    border-color: {_ACCENT};
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {input_bg};
    color: {text};
    border: 1px solid {input_border};
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {_ACCENT};
    selection-color: {_ACCENT_TEXT};
}}

QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {_ACCENT};
}}

QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {_ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {panel_alt};
    color: {text};
    selection-background-color: {_ACCENT};
    selection-color: {_ACCENT_TEXT};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}

QPushButton {{
    background-color: {secondary_btn};
    color: {text};
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {secondary_btn_hover};
}}

QPushButton:disabled {{
    color: {text_muted};
    background-color: {panel_alt};
}}

QPushButton#primaryButton {{
    background-color: {_ACCENT};
    color: {_ACCENT_TEXT};
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 700;
}}

QPushButton#primaryButton:hover {{
    background-color: {_ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background-color: {_ACCENT_PRESSED};
}}

QPushButton#primaryButton:disabled {{
    background-color: {panel_alt};
    color: {text_muted};
}}

QPushButton#dangerButton {{
    background-color: {danger};
    color: white;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    font-weight: 700;
}}

QPushButton#dangerButton:hover {{
    background-color: {danger_hover};
}}

QPushButton#ghostButton {{
    background-color: transparent;
    color: {_ACCENT};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 7px 12px;
}}

QPushButton#ghostButton:hover {{
    border-color: {_ACCENT};
    background-color: rgba(61, 219, 132, 0.10);
}}

QPushButton#iconButton {{
    background-color: {secondary_btn};
    color: {text};
    border-radius: 8px;
    padding: 6px 10px;
    min-width: 34px;
}}

QPushButton#iconButton:hover {{
    background-color: {secondary_btn_hover};
}}

QPushButton#iconButton[active="true"] {{
    background-color: rgba(61, 219, 132, 0.18);
    color: {_ACCENT};
    border: 1px solid {_ACCENT};
}}

QProgressBar {{
    border: none;
    background: {panel_alt};
    border-radius: 3px;
    max-height: 5px;
}}

QProgressBar::chunk {{
    background-color: {_ACCENT};
    border-radius: 3px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {scroll};
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QTextEdit#logView {{
    background-color: {preview_bg};
    color: {text_muted};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 8px;
    font-family: "Cascadia Code", "Consolas", "SF Mono", monospace;
    font-size: 11px;
}}

QFrame#logSidePanel {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 14px;
}}

QToolTip {{
    background-color: {panel_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 8px;
}}
"""


DARK_STYLES = _build_theme(
    bg="#12151C",
    panel="#1A1F2A",
    panel_alt="#222836",
    border="#2C3444",
    text="#F2F4F8",
    text_muted="#8B93A7",
    input_bg="#151A24",
    input_border="#343C4F",
    preview_bg="#0F131A",
    badge_bg="rgba(61, 219, 132, 0.16)",
    badge_text=_ACCENT,
    secondary_btn="#2A3140",
    secondary_btn_hover="#343C4F",
    danger="#E05252",
    danger_hover="#C94444",
    scroll="#3A4358",
)

LIGHT_STYLES = _build_theme(
    bg="#F3F5F8",
    panel="#FFFFFF",
    panel_alt="#F0F2F6",
    border="#DCE1EA",
    text="#1A1F2A",
    text_muted="#6B7385",
    input_bg="#FFFFFF",
    input_border="#D0D6E1",
    preview_bg="#F7F8FB",
    badge_bg="rgba(40, 168, 98, 0.12)",
    badge_text="#1F8F55",
    secondary_btn="#E8ECF2",
    secondary_btn_hover="#DCE1EA",
    danger="#E05252",
    danger_hover="#C94444",
    scroll="#C5CCD8",
)


def get_stylesheet(theme: ThemeMode = "dark") -> str:
    return DARK_STYLES if theme == "dark" else LIGHT_STYLES


def get_mac_stylesheet() -> str:
    return LIGHT_STYLES


def get_dark_stylesheet() -> str:
    return DARK_STYLES
