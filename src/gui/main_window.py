"""
主窗口 - Translator InTime GUI
"""
import asyncio
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QComboBox,
    QGroupBox, QCheckBox, QStatusBar, QSystemTrayIcon,
    QMenu, QApplication, QSplitter
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QAction, QIcon, QFont

from src.utils.config import AppConfig
from src.core.pipeline import TranslationPipeline, TranslationResult
from src.utils.logger import logger
from .settings_dialog import SettingsDialog
from .styles import DARK_THEME, start_button_style, stop_button_style
from .subtitle_buffer import SubtitleBuffer


class PipelineThread(QThread):
    """在独立线程中运行 asyncio 管道"""
    subtitle_received = pyqtSignal(object)  # TranslationResult
    outbound_received = pyqtSignal(object)
    asr_text_received = pyqtSignal(str, str)  # text, direction
    status_changed = pyqtSignal(str)  # 状态文字
    error_occurred = pyqtSignal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.pipeline: Optional[TranslationPipeline] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self) -> None:
        """启动 asyncio 事件循环"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self.pipeline = TranslationPipeline(self.config)

        # 连接回调 — 线程安全地发射 Qt 信号
        self.pipeline.on_subtitle(self._on_subtitle)
        self.pipeline.on_outbound(self._on_outbound)
        self.pipeline.on_asr_text(self._on_asr_text)
        self.pipeline.on_status(self._on_status)

        try:
            self._loop.run_until_complete(self.pipeline.initialize())
            self._loop.run_until_complete(self.pipeline.start())
            self._loop.run_forever()
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            # 清理残留任务
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    def stop(self) -> None:
        """停止管道（线程安全）"""
        if self._loop and self._loop.is_running():
            # 通知管道停止
            if self.pipeline:
                asyncio.run_coroutine_threadsafe(self.pipeline.stop(), self._loop)
            # 停止事件循环
            self._loop.call_soon_threadsafe(self._loop.stop)
        # 超时强制终止，不阻塞 GUI
        if not self.wait(1000):
            self.terminate()

    def _on_subtitle(self, result: TranslationResult) -> None:
        self.subtitle_received.emit(result)

    def _on_outbound(self, result: TranslationResult) -> None:
        self.outbound_received.emit(result)

    def _on_asr_text(self, text: str, direction: str) -> None:
        self.asr_text_received.emit(text, direction)

    def _on_status(self, status: str) -> None:
        self.status_changed.emit(status)


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self._pipeline_thread: Optional[PipelineThread] = None
        self._subtitle_buf = SubtitleBuffer()  # 入站字幕缓冲
        self._outbound_buf = SubtitleBuffer()  # 出站字幕缓冲
        self._setup_ui()
        self._setup_tray()
        self._apply_theme()

    def _setup_ui(self) -> None:
        """初始化界面"""
        self.setWindowTitle("Translator InTime — 实时游戏语音翻译")
        self.setMinimumSize(820, 560)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # ---- 控制栏 ----
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._btn_start = QPushButton("开始翻译")
        self._btn_start.setMinimumHeight(38)
        self._btn_start.setStyleSheet(start_button_style())
        self._btn_start.clicked.connect(self._on_start_stop)
        ctrl.addWidget(self._btn_start)
        self._label_status = QLabel("未启动")
        self._label_status.setStyleSheet(
            "color: #6c7086; font-size: 12px; font-weight: 700; "
            "padding: 5px 12px; background: #181825; border-radius: 999px;")
        ctrl.addWidget(self._label_status)
        ctrl.addStretch()
        self._btn_settings = QPushButton("设置")
        self._btn_settings.setMinimumHeight(38)
        self._btn_settings.clicked.connect(self._on_settings)
        ctrl.addWidget(self._btn_settings)
        main_layout.addLayout(ctrl)

        # ---- 内容区 ----
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(0)

        inbound_group = QGroupBox("游戏语音 -> 中文翻译")
        il = QVBoxLayout(inbound_group)
        il.setContentsMargins(10, 18, 10, 10)
        self._subtitle_text = QTextEdit()
        self._subtitle_text.setReadOnly(True)
        self._subtitle_text.setMinimumHeight(180)
        self._subtitle_text.setFont(QFont("Microsoft YaHei", 13))
        self._subtitle_text.setPlaceholderText(
            "识别到的外语原文会立即显示...\n翻译结果随后跟上...")
        il.addWidget(self._subtitle_text)
        splitter.addWidget(inbound_group)

        outbound_group = QGroupBox("我的语音 -> 翻译输出")
        ol = QVBoxLayout(outbound_group)
        ol.setContentsMargins(10, 18, 10, 10)
        self._outbound_text = QTextEdit()
        self._outbound_text.setReadOnly(True)
        self._outbound_text.setMaximumHeight(80)
        self._outbound_text.setFont(QFont("Microsoft YaHei", 11))
        self._outbound_text.setPlaceholderText("你说的中文 -> 翻译结果...")
        ol.addWidget(self._outbound_text)
        splitter.addWidget(outbound_group)

        main_layout.addWidget(splitter)

        self._status_bar = QStatusBar()
        self._status_bar.setFixedHeight(24)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("就绪  |  选择设备后点击「开始翻译」")

    def _setup_tray(self) -> None:
        """设置系统托盘"""
        self._tray = QSystemTrayIcon(self)
        # 使用内置图标作为兜底
        self._tray.setIcon(self.style().standardIcon(
            self.style().StandardPixmap.SP_MediaVolume
        ))
        self._tray.setToolTip("Translator InTime")

        tray_menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._on_show)
        tray_menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._on_force_quit)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标显示窗口"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_show()

    def _on_show(self) -> None:
        """显示窗口"""
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_force_quit(self) -> None:
        """强制退出（停止管道 + 退出应用）"""
        self._stop_pipeline()
        self._tray.hide()
        QApplication.instance().quit()

    def _apply_theme(self) -> None:
        """应用暗色主题"""
        self.setStyleSheet(DARK_THEME)

    # ---- 事件处理 ----

    def _on_start_stop(self) -> None:
        """开始/停止翻译"""
        if self._pipeline_thread is None or not self._pipeline_thread.isRunning():
            self._start_pipeline()
        else:
            self._stop_pipeline()

    def _start_pipeline(self) -> None:
        """启动翻译管道"""
        self._btn_start.setText("停止翻译")
        self._btn_start.setStyleSheet(stop_button_style())
        self._label_status.setText("初始化中...")
        self._label_status.setStyleSheet(
            "color: #f9e2af; font-size: 12px; font-weight: 700; "
            "padding: 5px 12px; background: #2a2416; border-radius: 999px;")

        self._pipeline_thread = PipelineThread(self.config)
        self._pipeline_thread.subtitle_received.connect(self._append_subtitle)
        self._pipeline_thread.outbound_received.connect(self._append_outbound)
        self._pipeline_thread.asr_text_received.connect(self._show_asr_text)
        self._pipeline_thread.status_changed.connect(self._update_status)
        self._pipeline_thread.error_occurred.connect(self._on_error)
        self._pipeline_thread.started.connect(self._on_pipeline_started)
        self._pipeline_thread.start()

    def _on_pipeline_started(self) -> None:
        """管道线程启动后的回调"""
        mode = "双向" if self.config.audio.game_output_device is not None else "出站"
        self._label_status.setText(f"运行中 - {mode}")
        self._label_status.setStyleSheet(
            "color: #a6e3a1; font-size: 12px; font-weight: 700; "
            "padding: 5px 12px; background: #1e3a2f; border-radius: 999px;")
        self._status_bar.showMessage(f"就绪 — {mode}翻译运行中")

    def _stop_pipeline(self) -> None:
        """停止翻译管道"""
        self._btn_start.setText("开始翻译")
        self._btn_start.setStyleSheet(start_button_style())
        self._label_status.setText("已停止")
        self._label_status.setStyleSheet(
            "color: #6c7086; font-size: 12px; font-weight: 700; "
            "padding: 5px 12px; background: #181825; border-radius: 999px;")

        if self._pipeline_thread:
            self._pipeline_thread.stop()
            self._pipeline_thread = None

    def _show_asr_text(self, text: str, direction: str) -> None:
        """ASR 识别到语音，通过缓冲器聚句后显示"""
        if direction == "inbound":
            result = self._subtitle_buf.feed(text)
            if result:
                self._subtitle_text.append(
                    f"<span style='color: #888'>[EN] {result}</span>"
                )
        else:
            result = self._outbound_buf.feed(text)
            if result:
                self._outbound_text.append(
                    f"<span style='color: #a5d6a7'>[ZH] {result}</span>"
                )

    def _update_status(self, status: str) -> None:
        """更新状态指示"""
        self._label_status.setText(status)
        self._status_bar.showMessage(status)

    def _append_subtitle(self, result: TranslationResult) -> None:
        """追加翻译结果（ASR 原文已显示，这里追加翻译）"""
        line = (
            f"<b style='color: #4fc3f7'>-> {result.translated_text}</b><br>"
            f"<hr style='border-color: #333'>"
        )
        self._subtitle_text.append(line)
        self._trim_text_edit(self._subtitle_text, self.config.ui.max_subtitle_lines)

    def _append_outbound(self, result: TranslationResult) -> None:
        """追加出站翻译结果"""
        line = (
            f"<span style='color: #4fc3f7'>→ {result.translated_text}</span>"
        )
        self._outbound_text.append(line)
        self._trim_text_edit(self._outbound_text, 50)

    def _on_error(self, error_msg: str) -> None:
        """处理错误"""
        logger.error(f"管道错误: {error_msg}")
        self._status_bar.showMessage(f"错误: {error_msg}")
        self._stop_pipeline()

    def _on_settings(self) -> None:
        """打开设置对话框"""
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            # 设置已保存，如果管道在运行则重启
            if self._pipeline_thread and self._pipeline_thread.isRunning():
                self._stop_pipeline()
                self._start_pipeline()

    def _on_always_top(self, checked: bool) -> None:
        """窗口置顶切换"""
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()
        self.config.ui.always_on_top = checked

    @staticmethod
    def _trim_text_edit(edit: QTextEdit, max_lines: int) -> None:
        """限制 TextEdit 最大行数"""
        doc = edit.document()
        if doc.blockCount() > max_lines:
            # 删除最前面的多余行
            cursor = edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            for _ in range(doc.blockCount() - max_lines):
                cursor.select(cursor.SelectionType.BlockUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()  # 删除换行符

    def closeEvent(self, event) -> None:
        """关闭窗口 → 最小化到托盘，右键退出才真正关闭"""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Translator InTime",
            "已最小化到托盘，右键图标选择「退出」关闭",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
