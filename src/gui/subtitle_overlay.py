"""
悬浮字幕窗口 - 桌面歌词样式
支持缩放、锁定、透明背景
锁定后完全透明无边框
"""
import sys
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout,
    QSizeGrip, QPushButton, QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QFont, QCursor


class SubtitleOverlay(QWidget):
    """
    桌面歌词样式的悬浮字幕窗口
    
    功能特性：
    - 可拖拽移动
    - 可缩放（右下角拖拽）
    - 可锁定（固定位置）
    - 锁定后完全透明无边框
    - 未锁定时支持拉伸，字体大小跟随变化
    - 支持自定义字体颜色
    """
    
    close_requested = pyqtSignal()
    lock_toggled = pyqtSignal(bool)
    
    def __init__(self, title: str = "字幕", parent=None):
        super().__init__(parent)
        self._title = title
        self._is_locked = False
        self._is_dragging = False
        self._drag_start_pos = QPoint()
        self._subtitle_lines = []
        self._max_lines = 2  # 最多双行显示（原文+译文）
        self._base_font_size = 16
        self._font_color = "#ffffff"
        self._base_width = 320
        self._base_height = 120
        
        self._setup_ui()
        self._apply_styles()
        
    def _setup_ui(self) -> None:
        """初始化界面"""
        # 设置窗口标志 - 无边框、始终置顶
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        
        # 透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(True)
        
        # 主布局
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(12, 12, 12, 12)
        self._main_layout.setSpacing(4)
        
        # 标题栏容器（用于整体显示/隐藏）
        self._title_bar_widget = QWidget()
        self._title_bar_widget.setStyleSheet("background-color: transparent;")
        title_bar_layout = QHBoxLayout(self._title_bar_widget)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        title_bar_layout.setSpacing(8)
        
        # 标题标签
        self._title_label = QLabel(self._title)
        self._title_label.setFont(QFont("SF Pro Display", 11, QFont.Weight.Medium))
        self._title_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); background-color: transparent;")
        
        # 控制按钮
        self._lock_btn = QPushButton("🔒")
        self._lock_btn.setFixedSize(24, 24)
        self._lock_btn.setToolTip("锁定位置")
        self._lock_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.1);
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """)
        self._lock_btn.clicked.connect(self._toggle_lock)
        
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setToolTip("关闭")
        self._close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.1);
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """)
        self._close_btn.clicked.connect(self.close_overlay)
        
        title_bar_layout.addWidget(self._title_label)
        title_bar_layout.addSpacerItem(QSpacerItem(20, 0, QSizePolicy.Policy.Expanding))
        title_bar_layout.addWidget(self._lock_btn)
        title_bar_layout.addWidget(self._close_btn)
        
        # 字幕显示区域
        self._subtitle_label = QLabel()
        self._subtitle_label.setFont(QFont("SF Pro Display", self._base_font_size, QFont.Weight.Medium))
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle_label.setMinimumWidth(200)
        self._subtitle_label.setMinimumHeight(60)
        self._subtitle_label.setStyleSheet("background-color: transparent;")
        
        # 缩放手柄（未锁定时显示）
        self._size_grip = QSizeGrip(self)
        self._size_grip.setStyleSheet("background-color: transparent;")
        
        # 组装布局
        self._main_layout.addWidget(self._title_bar_widget)
        self._main_layout.addWidget(self._subtitle_label, 1)  # 添加stretch factor让字幕区域可扩展
        
        # 添加右下角缩放手柄到布局
        grip_layout = QHBoxLayout()
        grip_layout.addStretch()
        grip_layout.addWidget(self._size_grip)
        self._main_layout.addLayout(grip_layout)
        
        # 初始大小
        self.resize(self._base_width, self._base_height)
        
    def _apply_styles(self) -> None:
        """应用MAC风格样式"""
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 20, 30, 0.85);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QLabel {
                color: #ffffff;
                background-color: transparent;
            }
        """)
        
    def resizeEvent(self, event) -> None:
        """窗口大小变化时调整字体大小"""
        super().resizeEvent(event)
        if not self._is_locked:
            self._update_font_size()
            
    def _update_font_size(self) -> None:
        """根据窗口大小更新字体大小"""
        # 计算缩放比例
        scale = min(self.width() / self._base_width, self.height() / self._base_height)
        new_size = max(10, int(self._base_font_size * scale))
        
        # 更新字体
        font = self._subtitle_label.font()
        font.setPointSize(new_size)
        self._subtitle_label.setFont(font)
        
        # 重新渲染字幕以应用新字体大小
        self._render_subtitles()
        
    def set_subtitle(self, text: str, original_text: str = "") -> None:
        """设置字幕内容 - 双行显示原文+译文"""
        # 新字幕替换旧字幕（双行模式只保留最新的一组原文+译文）
        self._subtitle_lines = [{
            'original': original_text,
            'translated': text
        }]
        
        # 渲染字幕
        self._render_subtitles()
        
    def _render_subtitles(self) -> None:
        """渲染字幕到界面 - 双行显示原文+译文"""
        lines = []
        for item in self._subtitle_lines:
            if item['original']:
                lines.append(f"<span style='color: rgba(255,255,255,0.5); font-size: 12px;'>{item['original']}</span>")
            lines.append(f"<span style='color: {self._font_color};'>{item['translated']}</span>")
            
        self._subtitle_label.setText("<br>".join(lines))
        
    def clear(self) -> None:
        """清空字幕"""
        self._subtitle_lines = []
        self._subtitle_label.clear()
        
    def set_max_lines(self, max_lines: int) -> None:
        """设置最大显示行数"""
        self._max_lines = max_lines
        if len(self._subtitle_lines) > max_lines:
            self._subtitle_lines = self._subtitle_lines[-max_lines:]
            self._render_subtitles()
            
    def set_font_color(self, color: str) -> None:
        """设置字体颜色"""
        self._font_color = color
        self._render_subtitles()
        
    def set_base_font_size(self, size: int) -> None:
        """设置基础字体大小"""
        self._base_font_size = size
        self._update_font_size()
        
    def _toggle_lock(self) -> None:
        """切换锁定状态 - 锁定后完全透明无边框"""
        self._is_locked = not self._is_locked
        self._update_lock_state()
        self.lock_toggled.emit(self._is_locked)
        
    def set_locked(self, locked: bool) -> None:
        """设置锁定状态（从主面板调用）"""
        if self._is_locked != locked:
            self._is_locked = locked
            self._update_lock_state()
            self.lock_toggled.emit(self._is_locked)
        
    def _update_lock_state(self) -> None:
        """更新锁定状态的UI显示"""
        if self._is_locked:
            # 锁定：完全透明无边框
            self._title_bar_widget.hide()
            self._size_grip.hide()
            # 移除所有边距和背景
            self._main_layout.setContentsMargins(0, 0, 0, 0)
            self.setStyleSheet("""
                QWidget {
                    background-color: transparent;
                    border: none;
                }
                QLabel {
                    color: #ffffff;
                    background-color: transparent;
                }
            """)
            # 锁定后不能拖动
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        else:
            # 解锁：显示标题栏和背景
            self._title_bar_widget.show()
            self._size_grip.show()
            # 恢复边距和背景
            self._main_layout.setContentsMargins(12, 12, 12, 12)
            self._apply_styles()

    def close_overlay(self) -> None:
        """关闭悬浮窗"""
        self.hide()
        self.close_requested.emit()
            
    def is_locked(self) -> bool:
        """返回锁定状态"""
        return self._is_locked
        
    # ---- 拖拽和缩放事件 ----
    
    def mousePressEvent(self, event) -> None:
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton and not self._is_locked:
            self._is_dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            
    def mouseMoveEvent(self, event) -> None:
        """鼠标移动事件"""
        if self._is_dragging and not self._is_locked:
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()
            
    def mouseReleaseEvent(self, event) -> None:
        """鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            event.accept()
            
    def enterEvent(self, event) -> None:
        """鼠标进入窗口"""
        if not self._is_locked:
            self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        
    def leaveEvent(self, event) -> None:
        """鼠标离开窗口"""
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        
    def set_title(self, title: str) -> None:
        """设置窗口标题"""
        self._title = title
        self._title_label.setText(title)
        
    def set_opacity(self, opacity: float) -> None:
        """设置窗口透明度"""
        self.setWindowOpacity(opacity)
        
    def closeEvent(self, event) -> None:
        """关闭事件"""
        self.close_requested.emit()
        event.accept()


if __name__ == "__main__":
    # 测试代码
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    overlay = SubtitleOverlay("游戏语音翻译")
    overlay.show()
    overlay.move(100, 100)
    
    # 模拟字幕更新
    def update_subtitle():
        overlay.set_subtitle("你好，世界！", "Hello, World!")
    
    timer = QTimer()
    timer.timeout.connect(update_subtitle)
    timer.start(2000)
    
    sys.exit(app.exec())
