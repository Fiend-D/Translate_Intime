"""
Qt 样式表 — MAC 风格主题
"""

MAC_THEME = """
/* ---- 主窗口 ---- */
QMainWindow {
    background-color: transparent;
    color: #ffffff;
    border-radius: 12px;
}

QWidget#centralWidget {
    background-color: #1c1c1e;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.1);
}

QWidget#contentWidget {
    background-color: #1c1c1e;
    border-radius: 0 0 12px 12px;
}

QWidget {
    background-color: transparent;
    color: #ffffff;
    font-family: "SF Pro Display", "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}

/* ---- 自定义标题栏 ---- */
QWidget#titleBar {
    background: linear-gradient(to bottom, #2a2a2e, #1c1c1e);
    border-radius: 12px 12px 0 0;
}

/* ---- 分组框 ---- */
QGroupBox {
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
    font-size: 12px;
    color: rgba(255, 255, 255, 0.8);
    background-color: rgba(255, 255, 255, 0.03);
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: rgba(255, 255, 255, 0.6);
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 6px 12px;
    color: #ffffff;
    font-weight: 500;
    font-size: 13px;
    min-width: 60px;
    min-height: 28px;
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.2);
}
QPushButton:pressed {
    background-color: rgba(255, 255, 255, 0.18);
}
QPushButton:disabled {
    background-color: rgba(255, 255, 255, 0.04);
    color: rgba(255, 255, 255, 0.3);
}

/* ---- 复选框 ---- */
QCheckBox {
    color: rgba(255, 255, 255, 0.9);
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(255, 255, 255, 0.25);
    border-radius: 5px;
    background-color: rgba(255, 255, 255, 0.08);
}
QCheckBox::indicator:checked {
    background-color: #007aff;
    border-color: #007aff;
}
QCheckBox::indicator:checked::after {
    image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='3'%3E%3Cpath d='M5 13l4 4L19 7'/%3E%3C/svg%3E");
}

/* ---- 标签 ---- */
QLabel {
    color: rgba(255, 255, 255, 0.9);
    font-size: 13px;
}

/* ---- 状态栏 ---- */
QStatusBar {
    background-color: rgba(255, 255, 255, 0.03);
    color: rgba(255, 255, 255, 0.5);
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    font-size: 11px;
    padding: 4px 12px;
}

/* ---- 下拉框 ---- */
QComboBox {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 6px 12px;
    color: #ffffff;
    min-width: 120px;
    font-size: 13px;
    combobox-popup: 0;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox:hover {
    border-color: rgba(255, 255, 255, 0.2);
}
QComboBox QAbstractItemView {
    background-color: #2a2a2e;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    selection-background-color: rgba(0, 122, 255, 0.3);
    color: #ffffff;
    outline: none;
    padding: 4px;
}
QComboBox::item {
    background-color: transparent;
    color: #ffffff;
    padding: 6px 12px;
    border-radius: 4px;
}
QComboBox::item:selected {
    background-color: rgba(0, 122, 255, 0.3);
    color: #ffffff;
}
QComboBox::item:hover {
    background-color: rgba(255, 255, 255, 0.08);
    color: #ffffff;
}

/* ---- 输入框 ---- */
QLineEdit {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 6px 12px;
    color: #ffffff;
    font-size: 13px;
}
QLineEdit:focus {
    border-color: #007aff;
}

/* ---- 数值输入 ---- */
QSpinBox, QDoubleSpinBox {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 5px 8px;
    color: #ffffff;
    font-size: 13px;
}

/* ---- 标签页 ---- */
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    background-color: rgba(255, 255, 255, 0.03);
}
QTabBar::tab {
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 10px 20px;
    margin-right: 3px;
    color: rgba(255, 255, 255, 0.6);
    font-size: 12px;
}
QTabBar::tab:selected {
    background-color: rgba(255, 255, 255, 0.08);
    color: #ffffff;
    font-weight: 500;
}
QTabBar::tab:hover:!selected {
    color: rgba(255, 255, 255, 0.8);
}

/* ---- 滚动条 ---- */
QScrollBar:vertical {
    background: rgba(255, 255, 255, 0.03);
    width: 6px;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.3);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* ---- 提示 ---- */
QToolTip {
    background-color: rgba(0, 0, 0, 0.9);
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
}

/* ---- 对话框 ---- */
QDialog {
    background-color: #1c1c1e;
    border-radius: 12px;
}

/* ---- 消息框 ---- */
QMessageBox {
    background-color: #1c1c1e;
}
QMessageBox QLabel {
    color: #ffffff;
}

/* ---- 按钮框 ---- */
QDialogButtonBox {
    QPushButton {
        padding: 8px 20px;
    }
    QPushButton#qt_msgbox_ok, QPushButton#qt_msgbox_apply {
        background-color: #007aff;
        border: none;
        color: white;
    }
    QPushButton#qt_msgbox_ok:hover, QPushButton#qt_msgbox_apply:hover {
        background-color: #0066cc;
    }
}
"""

# 按钮颜色工具
def start_button_style() -> str:
    return """
        QPushButton {
            background-color: #00d68f;
            border: none;
            border-radius: 10px;
            color: #000000;
            font-size: 14px;
            font-weight: 600;
            padding: 12px 24px;
        }
        QPushButton:hover {
            background-color: #00c884;
        }
        QPushButton:pressed {
            background-color: #00b478;
        }
    """

def stop_button_style() -> str:
    return """
        QPushButton {
            background-color: #ff453a;
            border: none;
            border-radius: 10px;
            color: #ffffff;
            font-size: 14px;
            font-weight: 600;
            padding: 12px 24px;
        }
        QPushButton:hover {
            background-color: #ff3b30;
        }
        QPushButton:pressed {
            background-color: #e63930;
        }
    """

# 暗色主题（保留向后兼容）
DARK_THEME = MAC_THEME
