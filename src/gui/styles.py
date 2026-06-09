"""
Qt 样式表 — Catppuccin Mocha 暗色主题
"""

DARK_THEME = """
QMainWindow {
    background-color: #11111b;
    color: #cdd6f4;
}

QWidget {
    background-color: transparent;
    color: #cdd6f4;
    font-family: "Segoe UI", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    font-size: 13px;
}

/* ---- 分组框 ---- */
QGroupBox {
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 14px;
    padding: 20px 12px 12px 12px;
    font-weight: bold;
    font-size: 13px;
    color: #cdd6f4;
    background-color: #181825;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: #89b4fa;
}
QGroupBox QFormLayout {
    spacing: 8px;
}
QGroupBox QLineEdit, QGroupBox QComboBox, QGroupBox QSpinBox {
    min-height: 28px;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 18px;
    color: #cdd6f4;
    font-weight: bold;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton#btnStart {
    background-color: #a6e3a1;
    color: #11111b;
    border: none;
    font-size: 14px;
    padding: 10px 24px;
}
QPushButton#btnStart:hover {
    background-color: #94e2d5;
}
QPushButton#btnStop {
    background-color: #f38ba8;
    color: #11111b;
    border: none;
    font-size: 14px;
    padding: 10px 24px;
}
QPushButton#btnStop:hover {
    background-color: #eba0ac;
}

/* ---- 文本框 ---- */
QTextEdit {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 10px;
    color: #cdd6f4;
    selection-background-color: #89b4fa;
    font-size: 13px;
    line-height: 1.5;
}

/* ---- 下拉框 ---- */
QComboBox {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 6px 10px;
    color: #cdd6f4;
    min-width: 120px;
}
QComboBox::drop-down { border: none; }
QComboBox:hover { border-color: #89b4fa; }
QComboBox QAbstractItemView {
    background-color: #313244;
    border: 1px solid #45475a;
    selection-background-color: #45475a;
    color: #cdd6f4;
}

/* ---- 输入框 ---- */
QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 6px 10px;
    color: #cdd6f4;
    font-size: 13px;
}
QLineEdit:focus {
    border-color: #89b4fa;
}

/* ---- 数值输入 ---- */
QSpinBox, QDoubleSpinBox {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 5px 8px;
    color: #cdd6f4;
}

/* ---- 复选框 ---- */
QCheckBox {
    color: #cdd6f4;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px; height: 18px;
    border: 2px solid #45475a;
    border-radius: 4px;
    background-color: #313244;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}

/* ---- 标签页 ---- */
QTabWidget::pane {
    border: 1px solid #313244;
    border-radius: 8px;
    background-color: #181825;
}
QTabBar::tab {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 10px 20px;
    margin-right: 3px;
    color: #a6adc8;
    font-size: 13px;
}
QTabBar::tab:selected {
    background-color: #181825;
    color: #89b4fa;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    color: #cdd6f4;
}

/* ---- 状态栏 ---- */
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
    font-size: 12px;
    padding: 4px 10px;
}

/* ---- 分割器 ---- */
QSplitter::handle {
    background-color: #313244;
    height: 3px;
}

/* ---- 滚动条 ---- */
QScrollBar:vertical {
    background: #11111b;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #45475a;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* ---- 标签 ---- */
QLabel#statusLabel {
    font-size: 14px;
    font-weight: bold;
    padding: 4px 12px;
    border-radius: 12px;
}
QLabel#statusIdle { color: #a6adc8; background-color: #313244; }
QLabel#statusRunning { color: #a6e3a1; background-color: #1e3a2f; }
QLabel#statusError { color: #f38ba8; background-color: #3a1e2a; }

/* ---- 滑块 ---- */
QSlider::groove:horizontal {
    height: 6px;
    background: #313244;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px; height: 16px;
    margin: -5px 0;
    background: #89b4fa;
    border-radius: 8px;
}

/* ---- 提示 ---- */
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ---- 对话框 ---- */
QDialog {
    background-color: #1e1e2e;
}
QLabel#tipLabel {
    color: #6c7086;
    font-size: 11px;
    padding: 4px 0;
}
"""

# 按钮颜色工具
def start_button_style() -> str:
    return "QPushButton { background-color: #a6e3a1; color: #11111b; border: none; font-size: 14px; padding: 10px 24px; border-radius: 6px; } QPushButton:hover { background-color: #94e2d5; }"

def stop_button_style() -> str:
    return "QPushButton { background-color: #f38ba8; color: #11111b; border: none; font-size: 14px; padding: 10px 24px; border-radius: 6px; } QPushButton:hover { background-color: #eba0ac; }"
