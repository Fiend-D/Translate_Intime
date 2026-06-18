"""
主窗口 - 友译 GUI
整合主界面和设置界面，支持缩放
"""
import asyncio
import os
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox,
    QGroupBox, QCheckBox, QStatusBar, QSystemTrayIcon,
    QMenu, QApplication, QGridLayout, QSpacerItem, QSizePolicy,
    QTabWidget, QLineEdit, QSpinBox, QDoubleSpinBox,
    QFormLayout, QMessageBox, QScrollArea, QSizeGrip,
    QProgressBar, QFrame, QToolButton, QFileDialog,
    QSlider
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread, QPoint, QSize
from PyQt6.QtGui import QAction, QIcon, QFont

from src.utils.config import AppConfig
from src.core.pipeline import TranslationPipeline, TranslationResult
from src.utils.logger import logger
from .styles import MAC_THEME, start_button_style, stop_button_style
from .subtitle_buffer import SubtitleBuffer
from .subtitle_overlay import SubtitleOverlay


class CardFrame(QFrame):
    """卡片式容器"""
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        self.setStyleSheet("""
            QFrame#cardFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)
        if title:
            title_lbl = QLabel(title)
            title_lbl.setFont(QFont("SF Pro Display", 14, QFont.Weight.Bold))
            title_lbl.setStyleSheet("color: #ffffff; background-color: transparent;")
            self._layout.addWidget(title_lbl)
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
            self._layout.addWidget(sep)

    def add_widget(self, widget):
        self._layout.addWidget(widget)

    def add_layout(self, layout):
        self._layout.addLayout(layout)

    def add_stretch(self):
        self._layout.addStretch()


class Expander(QFrame):
    """折叠面板"""
    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("expander")
        self._expanded = False
        self._setup_ui(title)

    def _setup_ui(self, title: str):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # 标题栏
        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet("background-color: transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)

        self._arrow = QLabel("▶")
        self._arrow.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 10px; background: transparent;")
        self._arrow.setFixedWidth(16)
        header_layout.addWidget(self._arrow)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("SF Pro Display", 12, QFont.Weight.Medium))
        title_lbl.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        header.mousePressEvent = lambda e: self._toggle()
        self._main_layout.addWidget(header)

        # 内容区
        self._content = QWidget()
        self._content.setVisible(False)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(28, 8, 12, 12)
        self._content_layout.setSpacing(8)
        self._main_layout.addWidget(self._content)

        self.setStyleSheet("""
            QFrame#expander {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 8px;
            }
        """)

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._arrow.setText("▼" if self._expanded else "▶")
        self.toggled.emit(self._expanded)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def set_expanded(self, expanded: bool):
        if self._expanded != expanded:
            self._toggle()


class FormRow(QWidget):
    """统一表单行：标签(右对齐) + 控件(填充剩余)"""
    def __init__(self, label: str, widget: QWidget, label_width: int = 160, widget_min_width: int = 120, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)

        lbl = QLabel(label)
        lbl.setFixedWidth(label_width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet("color: rgba(255,255,255,0.8); font-size: 13px; background: transparent;")
        layout.addWidget(lbl)

        widget.setMinimumWidth(widget_min_width)
        layout.addWidget(widget, 1)


class MaskedInput(QFrame):
    """带显示/隐藏切换的密码输入框"""
    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("maskedInput")
        self.setStyleSheet("""
            QFrame#maskedInput {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: #ffffff;
                padding: 6px;
            }
        """)
        layout.addWidget(self._input, 1)

        self._btn_toggle = QToolButton()
        self._btn_toggle.setText("👁")
        self._btn_toggle.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: rgba(255,255,255,0.5);
                font-size: 12px;
            }
            QToolButton:hover {
                color: #ffffff;
            }
        """)
        self._btn_toggle.clicked.connect(self._toggle_visibility)
        layout.addWidget(self._btn_toggle)

    def _toggle_visibility(self):
        if self._input.echoMode() == QLineEdit.EchoMode.Password:
            self._input.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self._input.setEchoMode(QLineEdit.EchoMode.Password)

    def text(self) -> str:
        return self._input.text()

    def setText(self, text: str):
        self._input.setText(text)


class PipelineThread(QThread):
    """在独立线程中运行 asyncio 管道"""
    subtitle_received = pyqtSignal(object)
    outbound_received = pyqtSignal(object)
    asr_text_received = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.pipeline: Optional[TranslationPipeline] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.pipeline = TranslationPipeline(self.config)
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
        if self._loop and self._loop.is_running():
            if self.pipeline:
                asyncio.run_coroutine_threadsafe(self.pipeline.stop(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)
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


class DownloadThread(QThread):
    """后台下载混元模型"""
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, target_dir: str):
        super().__init__()
        self.target_dir = target_dir

    def run(self) -> None:
        try:
            from huggingface_hub import snapshot_download
            os.makedirs(self.target_dir, exist_ok=True)
            snapshot_download(
                repo_id="tencent/HY-MT1.5-1.8B",
                local_dir=self.target_dir,
                resume_download=True,
            )
            self.finished.emit(True, self.target_dir)
        except Exception as e:
            self.finished.emit(False, str(e))


# UI显示值与配置值的映射表
_UI_TO_CONFIG = {
    # ASR
    "自动选择": "auto", "本地识别(FunASR)": "funasr", "OpenAI Whisper": "whisper", "Qwen3-ASR": "qwen3",
    "处理器(CPU)": "cpu", "显卡(CUDA)": "cuda",
    "中文": "zh", "英语": "en", "日语": "ja", "韩语": "ko", "自动检测": "auto",
    # Translation
    "自动选择": "auto", "火山引擎(推荐)": "volc", "阿里云(通义千问)": "aliyun", "腾讯混元": "hunyuan",
    "OpenAI": "openai", "DeepL": "deepl", "百度": "baidu", "微软": "microsoft", "Google": "google", "本地模型": "local",
    "法语": "fr", "德语": "de", "西班牙语": "es", "俄语": "ru",
    # TTS
    "微软Edge(免费)": "edge-tts", "OpenAI(更自然)": "openai",
}
_CONFIG_TO_UI = {v: k for k, v in _UI_TO_CONFIG.items()}

class MainWindow(QMainWindow):
    """主窗口 - 整合设置界面"""

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self._pipeline_thread: Optional[PipelineThread] = None
        self._subtitle_buf = SubtitleBuffer()
        self._outbound_buf = SubtitleBuffer()
        self._game_overlay: Optional[SubtitleOverlay] = None
        self._mic_overlay: Optional[SubtitleOverlay] = None
        self._is_running = False

        self._setup_ui()
        self._setup_tray()
        self._apply_theme()
        self._init_overlays()
        self._load_config_to_ui()

    def _setup_ui(self) -> None:
        """初始化界面"""
        self.setWindowTitle("友译")
        self.setMinimumSize(900, 600)
        self.resize(1280, 720)

        # 创建中央部件
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        # 主布局
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧标签栏
        left_tabs = QWidget()
        left_tabs.setObjectName("leftTabs")
        left_tabs.setFixedWidth(140)
        left_tabs_layout = QVBoxLayout(left_tabs)
        left_tabs_layout.setContentsMargins(0, 16, 0, 16)
        left_tabs_layout.setSpacing(4)
        left_tabs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 标题
        title_label = QLabel("友译")
        title_label.setFont(QFont("SF Pro Display", 18, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffffff; background-color: transparent; padding: 0 12px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_tabs_layout.addWidget(title_label)
        left_tabs_layout.addSpacing(20)

        # 标签按钮
        self._tab_buttons = []
        tab_names = [
            ("控制", "control"),
            ("识别", "asr"),
            ("翻译", "translation"),
            ("声音", "audio"),
        ]

        for name, tab_id in tab_names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(40)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 0;
                    color: rgba(255, 255, 255, 0.6);
                    font-size: 13px;
                    text-align: left;
                    padding-left: 16px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                    color: rgba(255, 255, 255, 0.8);
                }
                QPushButton:checked {
                    background-color: #007aff;
                    color: #ffffff;
                }
            """)
            btn.clicked.connect(lambda checked, tid=tab_id: self._on_tab_changed(tid))
            self._tab_buttons.append((btn, tab_id))
            left_tabs_layout.addWidget(btn)

        left_tabs_layout.addStretch()
        main_layout.addWidget(left_tabs)

        # 右侧内容面板
        self._content_panel = QWidget()
        self._content_panel.setObjectName("contentPanel")
        self._content_layout = QVBoxLayout(self._content_panel)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(12)

        # 创建所有标签页内容
        self._control_tab = self._create_control_tab()
        self._asr_tab = self._create_asr_tab()
        self._translation_tab = self._create_translation_tab()
        self._audio_tab = self._create_audio_tab()

        # 默认显示控制页
        self._current_tab = None
        self._show_tab("control")
        self._tab_buttons[0][0].setChecked(True)

        # 内容面板容器（包含标签页 + 底部操作栏）
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._content_panel, 1)

        # 底部操作栏
        action_bar = QWidget()
        action_bar.setStyleSheet("background-color: rgba(255, 255, 255, 0.03); border-top: 1px solid rgba(255, 255, 255, 0.05);")
        action_bar_layout = QHBoxLayout(action_bar)
        action_bar_layout.setContentsMargins(16, 10, 16, 10)
        action_bar_layout.setSpacing(12)

        # 开始/停止翻译按钮（左下角 FAB 风格）
        self._btn_start = QPushButton("▶  开始翻译")
        self._btn_start.setFixedHeight(40)
        self._btn_start.setFont(QFont("SF Pro Display", 13, QFont.Weight.Bold))
        self._btn_start.setStyleSheet(start_button_style() + """
            QPushButton {
                padding-left: 16px;
                padding-right: 16px;
                border-radius: 20px;
            }
        """)
        self._btn_start.clicked.connect(self._on_start_stop)
        action_bar_layout.addWidget(self._btn_start)

        # 状态标签（动态显示在按钮旁边）
        self._status_label = QLabel("就绪")
        self._status_label.setFont(QFont("SF Pro Display", 11, QFont.Weight.Medium))
        self._status_label.setStyleSheet("color: rgba(255,255,255,0.4); background-color: transparent; padding: 4px 8px;")
        action_bar_layout.addWidget(self._status_label)

        action_bar_layout.addStretch()

        # 保存设置按钮（右下角）
        self._btn_save = QPushButton("保存设置  Ctrl+S")
        self._btn_save.setFixedSize(120, 32)
        self._btn_save.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3395ff;
            }
            QPushButton:pressed {
                background-color: #0056b3;
            }
        """)
        self._btn_save.setToolTip("快捷键: Ctrl+S")
        self._btn_save.clicked.connect(self._on_save)
        action_bar_layout.addWidget(self._btn_save)

        content_layout.addWidget(action_bar)

        main_layout.addWidget(content_container, 1)

        # 状态栏
        self._status_bar = QStatusBar()
        self._status_bar.setFixedHeight(24)
        self._status_bar.setStyleSheet("""
            QStatusBar {
                background-color: rgba(255, 255, 255, 0.03);
                color: rgba(255, 255, 255, 0.4);
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                font-size: 10px;
            }
        """)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("友译 - 实时游戏翻译")

        # 快捷键 Ctrl+S 保存
        from PyQt6.QtGui import QShortcut, QKeySequence
        shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut_save.activated.connect(self._on_save)

    def _create_asr_tab(self) -> QWidget:
        """创建ASR设置页 - 卡片式布局"""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        # 核心配置卡片
        core_card = CardFrame("识别配置")

        self._asr_backend = QComboBox()
        self._asr_backend.addItems(["自动选择", "本地识别(FunASR)", "OpenAI Whisper", "Qwen3-ASR"])
        self._asr_backend.currentTextChanged.connect(self._on_asr_backend_changed)
        core_card.add_widget(FormRow("识别引擎:", self._asr_backend))

        self._asr_model = QComboBox()
        self._asr_model.addItems([
            "FunAudioLLM/Fun-ASR-Nano-2512",
            "Qwen/Qwen3-ASR-0.6B",
            "tiny", "base", "small", "medium", "large-v3"
        ])
        core_card.add_widget(FormRow("识别模型:", self._asr_model))

        self._asr_device = QComboBox()
        self._asr_device.addItems(["自动选择", "处理器(CPU)", "显卡(CUDA)"])
        core_card.add_widget(FormRow("运算设备:", self._asr_device))

        self._asr_lang_src = QComboBox()
        self._asr_lang_src.addItems(["中文", "英语", "日语", "韩语", "自动检测"])
        core_card.add_widget(FormRow("我的语言:", self._asr_lang_src))

        self._asr_lang_tgt = QComboBox()
        self._asr_lang_tgt.addItems(["英语", "中文", "日语", "韩语", "自动检测"])
        core_card.add_widget(FormRow("游戏语言:", self._asr_lang_tgt))

        self._asr_beam = QSpinBox()
        self._asr_beam.setRange(1, 10)
        self._asr_beam.setValue(5)
        core_card.add_widget(FormRow("识别准确度:", self._asr_beam))

        self._asr_vad = QCheckBox("自动过滤静音")
        vad_row = QHBoxLayout()
        vad_row.addStretch()
        vad_row.addWidget(self._asr_vad)
        core_card.add_layout(vad_row)

        outer.addWidget(core_card)

        # 帮助折叠面板
        help_exp = Expander("模型下载帮助")
        help_lbl = QLabel(
            "FunASR:\n"
            "  pip install -U git+https://github.com/modelscope/FunASR.git\n\n"
            "Whisper:\n"
            "  python -c \"from modelscope import snapshot_download; "
            "snapshot_download('systran/faster-whisper-small', cache_dir='./models')\"\n\n"
            "Qwen3-ASR:\n"
            "  python -c \"from modelscope import snapshot_download; "
            "snapshot_download('Qwen/Qwen3-ASR-0.6B', cache_dir='./models')\""
        )
        help_lbl.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px; font-family: monospace;")
        help_lbl.setWordWrap(True)
        help_exp.add_widget(help_lbl)
        outer.addWidget(help_exp)
        outer.addStretch()

        return w

    def _create_translation_tab(self) -> QWidget:
        """创建翻译设置页 - 采用左侧引擎列表 + 右侧配置卡片布局"""
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        # ===== 左侧引擎列表 =====
        engine_list = QWidget()
        engine_list.setObjectName("engineList")
        engine_list.setFixedWidth(240)
        engine_list.setStyleSheet("""
            QWidget#engineList {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }
        """)
        list_layout = QVBoxLayout(engine_list)
        list_layout.setContentsMargins(8, 12, 8, 12)
        list_layout.setSpacing(4)

        list_title = QLabel("翻译引擎")
        list_title.setFont(QFont("SF Pro Display", 13, QFont.Weight.Bold))
        list_title.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        list_layout.addWidget(list_title)
        list_layout.addSpacing(8)

        self._engine_buttons = {}
        engines = [
            ("volc", "火山引擎", "云端"),
            ("aliyun", "阿里云", "云端"),
            ("hunyuan", "腾讯混元", "本地"),
            ("openai", "OpenAI", "云端"),
        ]
        for key, name, tag in engines:
            btn = QPushButton(f"{name}\n{tag}")
            btn.setCheckable(True)
            btn.setFixedHeight(56)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 8px;
                    color: rgba(255, 255, 255, 0.7);
                    font-size: 13px;
                    text-align: left;
                    padding-left: 12px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                }
                QPushButton:checked {
                    background-color: rgba(0, 122, 255, 0.15);
                    color: #007aff;
                }
            """)
            btn.clicked.connect(lambda checked, k=key: self._on_engine_selected(k))
            self._engine_buttons[key] = btn
            list_layout.addWidget(btn)

        list_layout.addStretch()

        # 通用配置
        common_card = CardFrame("通用")
        self._trans_src = QComboBox()
        self._trans_src.addItems(["中文", "英语", "日语", "韩语", "法语", "德语", "西班牙语", "俄语"])
        common_card.add_widget(FormRow("源语言:", self._trans_src, label_width=90, widget_min_width=100))

        self._trans_tgt = QComboBox()
        self._trans_tgt.addItems(["中文", "英语", "日语", "韩语", "法语", "德语", "西班牙语", "俄语"])
        common_card.add_widget(FormRow("目标语言:", self._trans_tgt, label_width=90, widget_min_width=100))

        self._trans_timeout = QSpinBox()
        self._trans_timeout.setRange(1, 30)
        self._trans_timeout.setValue(10)
        self._trans_timeout.setSuffix(" 秒")
        common_card.add_widget(FormRow("等待时间:", self._trans_timeout, label_width=90, widget_min_width=100))
        list_layout.addWidget(common_card)

        outer.addWidget(engine_list)

        # ===== 右侧配置区 =====
        self._engine_stack = QWidget()
        self._engine_stack_layout = QVBoxLayout(self._engine_stack)
        self._engine_stack_layout.setContentsMargins(0, 0, 0, 0)
        self._engine_stack_layout.setSpacing(16)

        # --- 火山引擎配置 ---
        self._volc_panel = CardFrame("火山引擎配置")
        self._volc_app_id = MaskedInput("API Key")
        self._volc_panel.add_widget(FormRow("API Key:", self._volc_app_id))

        self._volc_token = MaskedInput("新版无需填写")
        self._volc_panel.add_widget(FormRow("Access Token:", self._volc_token))

        # 用量统计折叠面板
        volc_usage_exp = Expander("用量统计")
        self._volc_usage_progress = QProgressBar()
        self._volc_usage_progress.setRange(0, 100)
        self._volc_usage_progress.setValue(0)
        self._volc_usage_progress.setTextVisible(True)
        self._volc_usage_progress.setFormat("%p%")
        self._volc_usage_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3a3a3c;
                border-radius: 6px;
                background-color: #2c2c2e;
                color: #ffffff;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background-color: #34c759;
            }
        """)
        volc_usage_exp.add_widget(self._volc_usage_progress)

        self._volc_usage_detail = QLabel("累计使用: 0 tokens | 费用: 0.00 元")
        self._volc_usage_detail.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 12px;")
        volc_usage_exp.add_widget(self._volc_usage_detail)

        quota_row = QHBoxLayout()
        quota_row.addWidget(QLabel("月度配额:"))
        self._volc_quota_spin = QSpinBox()
        self._volc_quota_spin.setRange(10000, 10000000)
        self._volc_quota_spin.setSingleStep(10000)
        self._volc_quota_spin.setValue(500000)
        self._volc_quota_spin.setSuffix(" tokens")
        self._volc_quota_spin.setStyleSheet("color: #ffffff;")
        quota_row.addWidget(self._volc_quota_spin)
        quota_row.addStretch()
        volc_usage_exp.add_layout(quota_row)

        self._btn_reset_volc_usage = QPushButton("重置统计")
        self._btn_reset_volc_usage.setFixedSize(80, 28)
        self._btn_reset_volc_usage.setStyleSheet("""
            QPushButton {
                background-color: #ff453a;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #ff6b6b;
            }
        """)
        self._btn_reset_volc_usage.clicked.connect(self._reset_volc_usage)
        volc_usage_exp.add_widget(self._btn_reset_volc_usage)
        self._volc_panel.add_widget(volc_usage_exp)
        self._volc_panel.add_stretch()

        # --- 阿里云配置 ---
        self._aliyun_panel = CardFrame("阿里云配置")
        self._aliyun_key = MaskedInput("sk-...")
        self._aliyun_panel.add_widget(FormRow("API Key:", self._aliyun_key))

        self._aliyun_voice = QComboBox()
        self._aliyun_voice.addItems(["Tina", "Cherry", "Serena", "Ethan"])
        self._aliyun_panel.add_widget(FormRow("音色:", self._aliyun_voice))
        self._aliyun_panel.add_stretch()

        # --- 腾讯混元配置 ---
        self._hunyuan_panel = CardFrame("腾讯混元配置")
        path_row = QHBoxLayout()
        self._hunyuan_path = QLineEdit()
        self._hunyuan_path.setPlaceholderText("留空则自动搜索 ./models 目录")
        path_row.addWidget(self._hunyuan_path, 1)
        btn_browse = QPushButton("浏览")
        btn_browse.setFixedWidth(60)
        btn_browse.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.08);
                color: rgba(255,255,255,0.8);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.12);
            }
        """)
        btn_browse.clicked.connect(self._browse_hunyuan_model)
        path_row.addWidget(btn_browse)
        self._hunyuan_panel.add_widget(FormRow("模型路径:", self._hunyuan_path))

        self._hunyuan_status = QLabel("未检测到模型")
        self._hunyuan_status.setStyleSheet("color: #ff453a; font-size: 12px;")
        self._hunyuan_panel.add_widget(self._hunyuan_status)

        self._btn_download_hunyuan = QPushButton("⬇ 一键下载模型")
        self._btn_download_hunyuan.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 122, 255, 0.2);
                color: #007aff;
                border: 1px solid rgba(0, 122, 255, 0.3);
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(0, 122, 255, 0.3);
            }
            QPushButton:disabled {
                background-color: rgba(255,255,255,0.05);
                color: rgba(255,255,255,0.3);
                border-color: rgba(255,255,255,0.08);
            }
        """)
        self._btn_download_hunyuan.clicked.connect(self._download_hunyuan_model)
        self._hunyuan_panel.add_widget(self._btn_download_hunyuan)

        # 帮助提示折叠
        hunyuan_help = Expander("模型下载帮助")
        hunyuan_help_lbl = QLabel(
            "Hugging Face:\n"
            "  python -c \"from huggingface_hub import snapshot_download; "
            "snapshot_download('tencent/HY-MT1.5-1.8B', cache_dir='./models')\"\n\n"
            "直接克隆:\n"
            "  git clone https://huggingface.co/tencent/HY-MT1.5-1.8B ./models/HY-MT1.5-1.8B"
        )
        hunyuan_help_lbl.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px; font-family: monospace;")
        hunyuan_help_lbl.setWordWrap(True)
        hunyuan_help.add_widget(hunyuan_help_lbl)
        self._hunyuan_panel.add_widget(hunyuan_help)
        self._hunyuan_panel.add_stretch()

        # --- OpenAI 配置 ---
        self._openai_panel = CardFrame("OpenAI 配置")
        self._openai_key = MaskedInput("sk-...")
        self._openai_panel.add_widget(FormRow("API Key:", self._openai_key))

        self._openai_model = QComboBox()
        self._openai_model.addItems(["GPT-4o Mini(快)", "GPT-4o(准)", "GPT-3.5(省)"])
        self._openai_model.setEditable(True)
        self._openai_panel.add_widget(FormRow("模型:", self._openai_model))

        self._openai_url = QLineEdit()
        self._openai_url.setPlaceholderText("https://api.openai.com/v1")
        self._openai_panel.add_widget(FormRow("服务地址:", self._openai_url))
        self._openai_panel.add_stretch()

        # 默认显示火山引擎
        self._current_engine = "volc"
        self._engine_stack_layout.addWidget(self._volc_panel)
        self._engine_buttons["volc"].setChecked(True)

        outer.addWidget(self._engine_stack, 1)
        return w

    def _on_engine_selected(self, engine: str) -> None:
        """切换翻译引擎配置面板"""
        # 更新按钮状态
        for key, btn in self._engine_buttons.items():
            btn.setChecked(key == engine)
        # 切换面板
        panels = {
            "volc": self._volc_panel,
            "aliyun": self._aliyun_panel,
            "hunyuan": self._hunyuan_panel,
            "openai": self._openai_panel,
        }
        if self._current_engine in panels:
            old = panels[self._current_engine]
            self._engine_stack_layout.removeWidget(old)
            old.hide()
        if engine in panels:
            new = panels[engine]
            self._engine_stack_layout.addWidget(new)
            new.show()
        self._current_engine = engine

    def _browse_hunyuan_model(self) -> None:
        """浏览选择本地模型目录"""
        path = QFileDialog.getExistingDirectory(self, "选择模型目录", "./models")
        if path:
            self._hunyuan_path.setText(path)
            self._check_hunyuan_model(path)

    def _check_hunyuan_model(self, path: str) -> None:
        """检查腾讯混元本地模型是否存在"""
        if not path:
            # 自动搜索标准目录
            for p in ["./models/HY-MT1.5-1.8B", "./models/models--tencent--HY-MT1.5-1.8B"]:
                if os.path.exists(p) and os.path.exists(os.path.join(p, "config.json")):
                    path = p
                    break
        if path and os.path.exists(os.path.join(path, "config.json")):
            self._hunyuan_status.setText("已找到模型 ✓")
            self._hunyuan_status.setStyleSheet("color: #34c759; font-size: 12px;")
        else:
            self._hunyuan_status.setText("未检测到模型")
            self._hunyuan_status.setStyleSheet("color: #ff453a; font-size: 12px;")

    def _download_hunyuan_model(self) -> None:
        """一键下载混元模型到 ./models/HY-MT1.5-1.8B"""
        target = os.path.abspath("./models/HY-MT1.5-1.8B")
        self._btn_download_hunyuan.setEnabled(False)
        self._btn_download_hunyuan.setText("⬇ 下载中...")
        self._hunyuan_status.setText("正在下载模型，请耐心等待...")
        self._hunyuan_status.setStyleSheet("color: #007aff; font-size: 12px;")

        self._download_thread = DownloadThread(target)
        self._download_thread.finished.connect(self._on_download_finished)
        self._download_thread.start()

    def _on_download_finished(self, success: bool, msg: str) -> None:
        self._btn_download_hunyuan.setEnabled(True)
        if success:
            self._btn_download_hunyuan.setText("⬇ 重新下载")
            self._hunyuan_status.setText("下载完成 ✓")
            self._hunyuan_status.setStyleSheet("color: #34c759; font-size: 12px;")
            self._hunyuan_path.setText(msg)
        else:
            self._btn_download_hunyuan.setText("⬇ 一键下载模型")
            self._hunyuan_status.setText(f"下载失败: {msg[:60]}")
            self._hunyuan_status.setStyleSheet("color: #ff453a; font-size: 12px;")

    def _create_tts_tab(self) -> QWidget:
        """创建TTS设置页"""
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 16, 12, 12)

        self._tts_backend = QComboBox()
        self._tts_backend.addItems(["edge-tts", "openai"])
        layout.addRow("TTS后端:", self._tts_backend)

        self._tts_voice_cn = QLineEdit()
        self._tts_voice_cn.setText("zh-CN-XiaoxiaoNeural")
        layout.addRow("中文声音:", self._tts_voice_cn)

        self._tts_voice_en = QLineEdit()
        self._tts_voice_en.setText("en-US-JennyNeural")
        layout.addRow("英文声音:", self._tts_voice_en)

        self._tts_rate = QLineEdit()
        self._tts_rate.setText("+0%")
        layout.addRow("语速:", self._tts_rate)

        self._tts_volume = QLineEdit()
        self._tts_volume.setText("+0%")
        layout.addRow("音量:", self._tts_volume)

        return w

    def _create_audio_tab(self) -> QWidget:
        """创建声音设置页（TTS + 音频设备）- 卡片式布局"""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        # 语音播报设置卡片
        tts_card = CardFrame("语音播报")

        self._tts_backend = QComboBox()
        self._tts_backend.addItems(["微软Edge(免费)", "OpenAI(更自然)"])
        tts_card.add_widget(FormRow("播报引擎:", self._tts_backend))

        self._tts_voice_cn = QComboBox()
        self._tts_voice_cn.addItems(["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"])
        self._tts_voice_cn.setEditable(True)
        cn_row = FormRow("中文角色:", self._tts_voice_cn)
        tts_card.add_widget(cn_row)

        self._tts_voice_en = QComboBox()
        self._tts_voice_en.addItems(["en-US-JennyNeural", "en-US-GuyNeural", "en-GB-SoniaNeural"])
        self._tts_voice_en.setEditable(True)
        tts_card.add_widget(FormRow("英文角色:", self._tts_voice_en))

        # 语速滑块
        rate_widget = QWidget()
        rate_layout = QHBoxLayout(rate_widget)
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(8)
        self._tts_rate = QSlider(Qt.Orientation.Horizontal)
        self._tts_rate.setRange(-50, 50)
        self._tts_rate.setValue(0)
        self._tts_rate_label = QLabel("0%")
        self._tts_rate_label.setFixedWidth(40)
        self._tts_rate.valueChanged.connect(lambda v: self._tts_rate_label.setText(f"{v:+d}%"))
        rate_layout.addWidget(self._tts_rate)
        rate_layout.addWidget(self._tts_rate_label)
        tts_card.add_widget(FormRow("语速 (-50~+50):", rate_widget))

        # 音量滑块
        vol_widget = QWidget()
        vol_layout = QHBoxLayout(vol_widget)
        vol_layout.setContentsMargins(0, 0, 0, 0)
        vol_layout.setSpacing(8)
        self._tts_volume = QSlider(Qt.Orientation.Horizontal)
        self._tts_volume.setRange(-50, 50)
        self._tts_volume.setValue(0)
        self._tts_volume_label = QLabel("0%")
        self._tts_volume_label.setFixedWidth(40)
        self._tts_volume.valueChanged.connect(lambda v: self._tts_volume_label.setText(f"{v:+d}%"))
        vol_layout.addWidget(self._tts_volume)
        vol_layout.addWidget(self._tts_volume_label)
        tts_card.add_widget(FormRow("音量 (-50~+50):", vol_widget))

        outer.addWidget(tts_card)

        # 音频设备设置卡片
        dev_card = CardFrame("音频设备")

        self._audio_mic = QComboBox()
        self._audio_mic.addItem("使用系统默认麦克风", None)
        dev_card.add_widget(FormRow("麦克风:", self._audio_mic))

        self._audio_game = QComboBox()
        self._audio_game.addItem("使用系统默认音频", None)
        dev_card.add_widget(FormRow("游戏音频来源:", self._audio_game))

        self._audio_tts_out = QComboBox()
        self._audio_tts_out.addItem("使用系统默认扬声器", None)
        dev_card.add_widget(FormRow("播报输出设备:", self._audio_tts_out))

        # 刷新按钮行
        refresh_row = QHBoxLayout()
        refresh_row.addStretch()
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedSize(32, 32)
        btn_refresh.setToolTip("刷新设备列表")
        btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.05);
                color: #007aff;
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 16px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.1);
            }
        """)
        btn_refresh.clicked.connect(self._refresh_devices)
        refresh_row.addWidget(btn_refresh)
        dev_card.add_layout(refresh_row)

        outer.addWidget(dev_card)
        outer.addStretch()

        return w

    def _create_ui_tab(self) -> QWidget:
        """界面设置已合并到控制页，此函数保留兼容"""
        return self._control_tab

    def _load_config_to_ui(self) -> None:
        """加载配置到UI"""
        # ASR
        self._asr_backend.setCurrentText(_CONFIG_TO_UI.get(self.config.asr.backend, self.config.asr.backend))
        self._asr_model.setCurrentText(self.config.asr.model_size)
        self._asr_device.setCurrentText(_CONFIG_TO_UI.get(self.config.asr.device, self.config.asr.device))
        self._asr_lang_src.setCurrentText(_CONFIG_TO_UI.get(self.config.asr.source_language, self.config.asr.source_language))
        self._asr_lang_tgt.setCurrentText(_CONFIG_TO_UI.get(self.config.asr.target_language, self.config.asr.target_language))
        self._asr_beam.setValue(self.config.asr.beam_size)
        self._asr_vad.setChecked(self.config.asr.vad_filter)

        # Translation
        engine = self.config.translation.backend
        if engine in self._engine_buttons:
            self._on_engine_selected(engine)
        self._hunyuan_path.setText(self.config.translation.hunyuan_model_path)
        self._check_hunyuan_model(self.config.translation.hunyuan_model_path)
        self._trans_src.setCurrentText(_CONFIG_TO_UI.get(self.config.translation.source_lang, self.config.translation.source_lang))
        self._trans_tgt.setCurrentText(_CONFIG_TO_UI.get(self.config.translation.target_lang, self.config.translation.target_lang))
        self._trans_timeout.setValue(self.config.translation.timeout)
        self._openai_key.setText(self.config.translation.openai_api_key)
        self._openai_model.setCurrentText(self.config.translation.openai_model)
        self._openai_url.setText(self.config.translation.openai_base_url)
        self._volc_app_id.setText(self.config.translation.volc_app_id)
        self._volc_token.setText(self.config.translation.volc_access_token)

        # 阿里云
        self._aliyun_key.setText(self.config.aliyun.api_key)
        self._aliyun_voice.setCurrentText(self.config.aliyun.voice)

        # TTS
        self._tts_backend.setCurrentText(_CONFIG_TO_UI.get(self.config.tts.backend, self.config.tts.backend))
        self._tts_voice_cn.setCurrentText(self.config.tts.voice)
        self._tts_voice_en.setCurrentText(self.config.tts.target_voice)
        try:
            rate_val = int(self.config.tts.rate.replace("%", ""))
        except ValueError:
            rate_val = 0
        self._tts_rate.setValue(max(-50, min(50, rate_val)))
        try:
            vol_val = int(self.config.tts.volume.replace("%", ""))
        except ValueError:
            vol_val = 0
        self._tts_volume.setValue(max(-50, min(50, vol_val)))

        # UI
        self._ui_font_size.setValue(self.config.ui.font_size)
        self._ui_always_top.setChecked(self.config.ui.always_on_top)
        self._ui_subtitle_opacity.setValue(self.config.ui.subtitle_opacity)
        self._ui_subtitle_lines.setValue(self.config.ui.max_subtitle_lines)

        # 火山引擎用量统计
        self._volc_quota_spin.setValue(self.config.volc_usage.monthly_quota)
        self._update_volc_usage_display()

    def _refresh_devices(self) -> None:
        """刷新音频设备列表"""
        from src.audio.stream import list_audio_devices
        devices = list_audio_devices()

        for combo, dev_list, prefix in [
            (self._audio_mic, devices["input"], "[Mic]"),
            (self._audio_game, devices["input"], "[Game]"),
            (self._audio_tts_out, devices["output"], "[Out]"),
        ]:
            combo.clear()
            combo.addItem("系统默认", None)
            for dev in dev_list:
                combo.addItem(f"{prefix} {dev['name']}", dev["id"])

    def _validate_settings(self) -> Tuple[bool, str]:
        """保存前校验配置，返回 (是否通过, 错误信息)"""
        backend = self._current_engine

        if backend == "hunyuan":
            path = self._hunyuan_path.text().strip()
            if not path:
                path = "./models/HY-MT1.5-1.8B"
            if not os.path.exists(os.path.join(path, "config.json")):
                return False, "腾讯混元模型路径无效，未找到 config.json\n请下载模型或选择正确路径"

        if backend == "openai":
            key = self._openai_key.text().strip()
            if key and not key.startswith("sk-"):
                return False, "OpenAI API Key 格式不正确，应以 sk- 开头"
            if not key:
                return False, "使用 OpenAI 引擎时必须填写 API Key"

        if backend == "aliyun":
            key = self._aliyun_key.text().strip()
            if not key:
                return False, "使用阿里云引擎时必须填写 API Key"

        if backend == "volc":
            if not self._volc_app_id.text().strip():
                return False, "使用火山引擎时必须填写 App ID"
            if not self._volc_token.text().strip():
                return False, "使用火山引擎时必须填写 Access Token"

        return True, ""

    def _on_save(self) -> None:
        """保存配置"""
        ok, err = self._validate_settings()
        if not ok:
            QMessageBox.warning(self, "配置校验失败", err)
            return

        try:
            from src.utils.config import ConfigManager
            mgr = ConfigManager()

            mgr.update("asr",
                backend=_UI_TO_CONFIG.get(self._asr_backend.currentText(), self._asr_backend.currentText()),
                model_size=self._asr_model.currentText(),
                funasr_model="FunAudioLLM/Fun-ASR-Nano-2512" if "Fun-ASR" in self._asr_model.currentText() else self.config.asr.funasr_model,
                qwen3_model="Qwen/Qwen3-ASR-0.6B" if "Qwen" in self._asr_model.currentText() else self.config.asr.qwen3_model,
                device=_UI_TO_CONFIG.get(self._asr_device.currentText(), self._asr_device.currentText()),
                source_language=_UI_TO_CONFIG.get(self._asr_lang_src.currentText(), self._asr_lang_src.currentText()),
                target_language=_UI_TO_CONFIG.get(self._asr_lang_tgt.currentText(), self._asr_lang_tgt.currentText()),
                beam_size=self._asr_beam.value(),
                vad_filter=self._asr_vad.isChecked(),
            )

            # 当前选中的翻译引擎
            backend = self._current_engine

            mgr.update("translation",
                backend=backend,
                source_lang=_UI_TO_CONFIG.get(self._trans_src.currentText(), self._trans_src.currentText()),
                target_lang=_UI_TO_CONFIG.get(self._trans_tgt.currentText(), self._trans_tgt.currentText()),
                timeout=self._trans_timeout.value(),
                openai_api_key=self._openai_key.text(),
                openai_model=self._openai_model.currentText(),
                openai_base_url=self._openai_url.text(),
                volc_app_id=self._volc_app_id.text(),
                volc_access_token=self._volc_token.text(),
                hunyuan_model_path=self._hunyuan_path.text(),
            )

            # 保存阿里云配置
            mgr.update("aliyun",
                api_key=self._aliyun_key.text(),
                voice=self._aliyun_voice.currentText(),
            )

            # 保存火山引擎用量配置
            mgr.update("volc_usage",
                monthly_quota=self._volc_quota_spin.value(),
            )

            mgr.update("tts",
                backend=_UI_TO_CONFIG.get(self._tts_backend.currentText(), self._tts_backend.currentText()),
                voice=self._tts_voice_cn.currentText(),
                target_voice=self._tts_voice_en.currentText(),
                rate=f"{self._tts_rate.value():+d}%",
                volume=f"{self._tts_volume.value():+d}%",
            )

            mgr.update("ui",
                font_size=self._ui_font_size.value(),
                always_on_top=self._ui_always_top.isChecked(),
                max_subtitle_lines=self._ui_subtitle_lines.value(),
                subtitle_opacity=self._ui_subtitle_opacity.value(),
            )

            # 重新加载配置到内存，确保下次启动翻译时使用最新配置
            self.config = mgr.load()

            QMessageBox.information(self, "设置已保存", "配置已保存，重启翻译后生效。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存配置时出错: {e}")

    def _on_tab_changed(self, tab_id: str) -> None:
        """切换标签页"""
        # 更新按钮状态
        for btn, tid in self._tab_buttons:
            btn.setChecked(tid == tab_id)
        # 显示对应内容
        self._show_tab(tab_id)

    def _show_tab(self, tab_id: str) -> None:
        """显示指定标签页内容"""
        # 移除当前内容
        if self._current_tab is not None:
            self._content_layout.removeWidget(self._current_tab)
            self._current_tab.hide()

        # 显示新内容
        tab_map = {
            "control": self._control_tab,
            "asr": self._asr_tab,
            "translation": self._translation_tab,
            "audio": self._audio_tab,
        }
        self._current_tab = tab_map.get(tab_id, self._control_tab)
        self._content_layout.addWidget(self._current_tab)
        self._current_tab.show()

    def _on_asr_backend_changed(self, text: str) -> None:
        """根据识别引擎切换可用模型"""
        backend = _UI_TO_CONFIG.get(text, text)
        self._asr_model.clear()
        if backend == "funasr":
            self._asr_model.addItems(["FunAudioLLM/Fun-ASR-Nano-2512"])
            self._asr_device.setEnabled(True)
        elif backend == "qwen3":
            self._asr_model.addItems(["Qwen/Qwen3-ASR-0.6B"])
            self._asr_device.setEnabled(True)
        elif backend == "whisper":
            self._asr_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
            self._asr_device.setEnabled(True)
        else:
            self._asr_model.addItems([
                "FunAudioLLM/Fun-ASR-Nano-2512",
                "Qwen/Qwen3-ASR-0.6B",
                "tiny", "base", "small", "medium", "large-v3"
            ])

    def _create_control_tab(self) -> QWidget:
        """创建控制标签页 - 卡片式布局"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 悬浮字幕卡片
        overlay_card = CardFrame("悬浮字幕")

        # 游戏翻译字幕
        game_row = QHBoxLayout()
        self._show_game_overlay = QCheckBox("显示游戏翻译字幕")
        self._show_game_overlay.setChecked(self.config.ui.show_game_subtitle)
        self._show_game_overlay.stateChanged.connect(self._toggle_game_overlay)
        game_row.addWidget(self._show_game_overlay)
        game_row.addStretch()

        self._btn_toggle_game = QPushButton("预览位置")
        self._btn_toggle_game.setFixedSize(80, 28)
        self._btn_toggle_game.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #007aff;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self._btn_toggle_game.clicked.connect(self._toggle_game_overlay)
        game_row.addWidget(self._btn_toggle_game)

        self._lock_status_game = QLabel("🔓")
        self._lock_status_game.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 14px;")
        self._lock_status_game.setToolTip("未锁定 - 可拖动调整位置")
        game_row.addWidget(self._lock_status_game)

        self._btn_unlock_game = QPushButton("解锁")
        self._btn_unlock_game.setFixedSize(50, 28)
        self._btn_unlock_game.setVisible(False)
        self._btn_unlock_game.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 69, 58, 0.2);
                color: #ff453a;
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }
        """)
        self._btn_unlock_game.clicked.connect(lambda: self._unlock_overlay("game"))
        game_row.addWidget(self._btn_unlock_game)
        overlay_card.add_layout(game_row)

        # 麦克风翻译字幕
        mic_row = QHBoxLayout()
        self._show_mic_overlay = QCheckBox("显示我说的话")
        self._show_mic_overlay.setChecked(self.config.ui.show_mic_subtitle)
        self._show_mic_overlay.stateChanged.connect(self._toggle_mic_overlay)
        mic_row.addWidget(self._show_mic_overlay)
        mic_row.addStretch()

        self._btn_toggle_mic = QPushButton("预览位置")
        self._btn_toggle_mic.setFixedSize(80, 28)
        self._btn_toggle_mic.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #007aff;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self._btn_toggle_mic.clicked.connect(self._toggle_mic_overlay)
        mic_row.addWidget(self._btn_toggle_mic)

        self._lock_status_mic = QLabel("🔓")
        self._lock_status_mic.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 14px;")
        self._lock_status_mic.setToolTip("未锁定 - 可拖动调整位置")
        mic_row.addWidget(self._lock_status_mic)

        self._btn_unlock_mic = QPushButton("解锁")
        self._btn_unlock_mic.setFixedSize(50, 28)
        self._btn_unlock_mic.setVisible(False)
        self._btn_unlock_mic.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 69, 58, 0.2);
                color: #ff453a;
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }
        """)
        self._btn_unlock_mic.clicked.connect(lambda: self._unlock_overlay("mic"))
        mic_row.addWidget(self._btn_unlock_mic)
        overlay_card.add_layout(mic_row)

        # 字体颜色
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("字幕颜色"))
        color_row.addSpacing(12)

        self._btn_color_game = QPushButton("游戏")
        self._btn_color_game.setFixedSize(70, 28)
        self._btn_color_game.setStyleSheet("""
            QPushButton {
                background-color: #34c759;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 12px;
            }
        """)
        self._btn_color_game.clicked.connect(lambda: self._pick_color("game"))
        color_row.addWidget(self._btn_color_game)

        self._btn_color_mic = QPushButton("麦克风")
        self._btn_color_mic.setFixedSize(70, 28)
        self._btn_color_mic.setStyleSheet("""
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 12px;
            }
        """)
        self._btn_color_mic.clicked.connect(lambda: self._pick_color("mic"))
        color_row.addWidget(self._btn_color_mic)
        color_row.addStretch()
        overlay_card.add_layout(color_row)

        layout.addWidget(overlay_card)

        # 语音播报卡片
        tts_card = CardFrame("语音播报")
        self._play_chinese = QCheckBox("朗读游戏对话的中文翻译")
        self._play_chinese.setChecked(self.config.ui.play_chinese_voice)
        self._play_chinese.stateChanged.connect(self._on_play_chinese_changed)
        tts_card.add_widget(self._play_chinese)

        self._play_outbound = QCheckBox("朗读我说话的外语翻译")
        self._play_outbound.setChecked(self.config.ui.play_outbound_voice)
        self._play_outbound.stateChanged.connect(self._on_play_outbound_changed)
        tts_card.add_widget(self._play_outbound)
        layout.addWidget(tts_card)

        # 界面设置卡片
        ui_card = CardFrame("界面")

        self._ui_font_size = QSpinBox()
        self._ui_font_size.setRange(8, 24)
        self._ui_font_size.setValue(12)
        ui_card.add_widget(FormRow("字幕字号:", self._ui_font_size))

        self._ui_subtitle_opacity = QDoubleSpinBox()
        self._ui_subtitle_opacity.setRange(0.1, 1.0)
        self._ui_subtitle_opacity.setSingleStep(0.05)
        self._ui_subtitle_opacity.setValue(0.85)
        ui_card.add_widget(FormRow("透明度:", self._ui_subtitle_opacity))

        self._ui_subtitle_lines = QSpinBox()
        self._ui_subtitle_lines.setRange(2, 10)
        self._ui_subtitle_lines.setValue(3)
        ui_card.add_widget(FormRow("最大行数:", self._ui_subtitle_lines))

        self._ui_always_top = QCheckBox("始终显示在最前面")
        top_row = QHBoxLayout()
        top_row.addStretch()
        top_row.addWidget(self._ui_always_top)
        ui_card.add_layout(top_row)
        layout.addWidget(ui_card)
        layout.addStretch()

        return w

    def _apply_theme(self) -> None:
        """应用主题"""
        self.setStyleSheet(MAC_THEME + """
            QMainWindow {
                background-color: #1c1c1e;
            }
            QWidget#leftTabs {
                background-color: #1c1c1e;
                border-right: 1px solid rgba(255, 255, 255, 0.1);
            }
            QWidget#contentPanel {
                background-color: #2c2c2e;
            }
            QGroupBox {
                color: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QLabel {
                color: #ffffff;
            }
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #ffffff;
                padding: 6px;
            }
            QComboBox {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #ffffff;
                padding: 6px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #ffffff;
                padding: 6px;
            }
        """)

    def _setup_tray(self) -> None:
        """设置系统托盘"""
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("友译")

        menu = QMenu(self)
        show_action = QAction("显示", self)
        show_action.triggered.connect(self.showNormal)
        menu.addAction(show_action)

        start_action = QAction("开始翻译", self)
        start_action.triggered.connect(self._on_start_stop)
        menu.addAction(start_action)

        menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._on_close)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def _init_overlays(self) -> None:
        """初始化悬浮字幕窗口"""
        self._game_overlay = SubtitleOverlay("游戏语音翻译")
        self._game_overlay.close_requested.connect(self._on_overlay_closed)
        self._game_overlay.lock_toggled.connect(self._on_game_lock_changed)

        self._mic_overlay = SubtitleOverlay("麦克风输入")
        self._mic_overlay.close_requested.connect(self._on_overlay_closed)
        self._mic_overlay.lock_toggled.connect(self._on_mic_lock_changed)

        if self.config.ui.show_game_subtitle:
            self._game_overlay.show()
        if self.config.ui.show_mic_subtitle:
            self._mic_overlay.show()

    def _on_overlay_closed(self) -> None:
        sender = self.sender()
        if sender == self._game_overlay:
            self._show_game_overlay.blockSignals(True)
            self._show_game_overlay.setChecked(False)
            self._show_game_overlay.blockSignals(False)
            self._btn_toggle_game.setText("预览位置")
        elif sender == self._mic_overlay:
            self._show_mic_overlay.blockSignals(True)
            self._show_mic_overlay.setChecked(False)
            self._show_mic_overlay.blockSignals(False)
            self._btn_toggle_mic.setText("预览位置")

    def _on_game_lock_changed(self, locked: bool) -> None:
        if locked:
            self._lock_status_game.setText("🔒")
            self._lock_status_game.setToolTip("已锁定 - 在主面板解锁")
            self._lock_status_game.setStyleSheet("color: #ff453a; font-size: 14px; background-color: transparent;")
            self._btn_unlock_game.setVisible(True)
        else:
            self._lock_status_game.setText("🔓")
            self._lock_status_game.setToolTip("未锁定 - 可拖动调整位置")
            self._lock_status_game.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 14px; background-color: transparent;")
            self._btn_unlock_game.setVisible(False)

    def _on_mic_lock_changed(self, locked: bool) -> None:
        if locked:
            self._lock_status_mic.setText("🔒")
            self._lock_status_mic.setToolTip("已锁定 - 在主面板解锁")
            self._lock_status_mic.setStyleSheet("color: #ff453a; font-size: 14px; background-color: transparent;")
            self._btn_unlock_mic.setVisible(True)
        else:
            self._lock_status_mic.setText("🔓")
            self._lock_status_mic.setToolTip("未锁定 - 可拖动调整位置")
            self._lock_status_mic.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 14px; background-color: transparent;")
            self._btn_unlock_mic.setVisible(False)

    def _unlock_overlay(self, overlay_type: str) -> None:
        if overlay_type == "game" and self._game_overlay:
            self._game_overlay.set_locked(False)
        elif overlay_type == "mic" and self._mic_overlay:
            self._mic_overlay.set_locked(False)

    def _pick_color(self, overlay_type: str) -> None:
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor()
        if color.isValid():
            color_hex = color.name()
            if overlay_type == "game" and self._game_overlay:
                self._game_overlay.set_font_color(color_hex)
            elif overlay_type == "mic" and self._mic_overlay:
                self._mic_overlay.set_font_color(color_hex)

    def _toggle_game_overlay(self, state=None) -> None:
        if not self._game_overlay:
            return
        is_visible = self._game_overlay.isVisible()
        if is_visible:
            self._game_overlay.hide()
            self._btn_toggle_game.setText("预览位置")
            self._show_game_overlay.blockSignals(True)
            self._show_game_overlay.setChecked(False)
            self._show_game_overlay.blockSignals(False)
        else:
            self._game_overlay.show()
            self._btn_toggle_game.setText("隐藏")
            self._show_game_overlay.blockSignals(True)
            self._show_game_overlay.setChecked(True)
            self._show_game_overlay.blockSignals(False)
        self.config.ui.show_game_subtitle = not is_visible

    def _toggle_mic_overlay(self, state=None) -> None:
        if not self._mic_overlay:
            return
        is_visible = self._mic_overlay.isVisible()
        if is_visible:
            self._mic_overlay.hide()
            self._btn_toggle_mic.setText("预览位置")
            self._show_mic_overlay.blockSignals(True)
            self._show_mic_overlay.setChecked(False)
            self._show_mic_overlay.blockSignals(False)
        else:
            self._mic_overlay.show()
            self._btn_toggle_mic.setText("隐藏")
            self._show_mic_overlay.blockSignals(True)
            self._show_mic_overlay.setChecked(True)
            self._show_mic_overlay.blockSignals(False)
        self.config.ui.show_mic_subtitle = not is_visible

    def _on_start_stop(self) -> None:
        if self._is_running:
            self._stop_translation()
        else:
            self._start_translation()

    def _start_translation(self) -> None:
        self._status_label.setText("初始化中...")
        self._status_label.setStyleSheet("color: #007aff;")
        self._btn_start.setText("⏹  停止翻译")
        self._btn_start.setStyleSheet(stop_button_style() + """
            QPushButton {
                padding-left: 16px;
                padding-right: 16px;
                border-radius: 20px;
            }
        """)

        self._pipeline_thread = PipelineThread(self.config)
        self._pipeline_thread.subtitle_received.connect(self._append_subtitle)
        self._pipeline_thread.outbound_received.connect(self._append_outbound)
        self._pipeline_thread.asr_text_received.connect(self._on_asr_text)
        self._pipeline_thread.status_changed.connect(self._on_status)
        self._pipeline_thread.error_occurred.connect(self._on_error)
        self._pipeline_thread.finished.connect(self._on_pipeline_finished)
        self._pipeline_thread.start()

        self._is_running = True
        self._status_bar.showMessage("翻译运行中")

        # 启动用量统计定时器
        self._usage_timer = QTimer(self)
        self._usage_timer.timeout.connect(self._on_volc_usage_timer)
        self._usage_timer.start(5000)  # 每5秒更新一次用量显示

    def _stop_translation(self) -> None:
        self._btn_start.setText("▶  开始翻译")
        self._btn_start.setStyleSheet(start_button_style() + """
            QPushButton {
                padding-left: 16px;
                padding-right: 16px;
                border-radius: 20px;
            }
        """)
        self._status_label.setText("已停止")
        self._status_label.setStyleSheet("color: #9ca3af;")

        if self._pipeline_thread:
            self._pipeline_thread.stop()
            self._pipeline_thread = None

        self._is_running = False
        self._status_bar.showMessage("已停止")

        # 停止用量统计定时器
        if hasattr(self, '_usage_timer') and self._usage_timer:
            self._usage_timer.stop()

    def _on_pipeline_finished(self) -> None:
        self._is_running = False
        self._btn_start.setText("▶  开始翻译")
        self._btn_start.setStyleSheet(start_button_style() + """
            QPushButton {
                padding-left: 16px;
                padding-right: 16px;
                border-radius: 20px;
            }
        """)
        self._status_label.setText("已停止")
        self._status_label.setStyleSheet("color: #9ca3af;")
        self._status_bar.showMessage("已停止")

    def _append_subtitle(self, result: TranslationResult) -> None:
        if self._game_overlay:
            self._game_overlay.set_subtitle(result.translated_text, result.source_text)
        if self.config.ui.play_chinese_voice and hasattr(result, 'translated_text'):
            pass

    def _append_outbound(self, result: TranslationResult) -> None:
        if self._mic_overlay:
            self._mic_overlay.set_subtitle(result.translated_text, result.source_text)
        if self.config.ui.play_outbound_voice and hasattr(result, 'translated_text'):
            pass

    def _on_asr_text(self, text: str, direction: str) -> None:
        pass

    def _on_status(self, status: str) -> None:
        self._status_label.setText(status)
        if "运行中" in status or "就绪" in status:
            self._status_label.setStyleSheet("color: #34c759;")
        elif "错误" in status:
            self._status_label.setStyleSheet("color: #ff453a;")
        else:
            self._status_label.setStyleSheet("color: #007aff;")

    def _on_error(self, error: str) -> None:
        self._status_label.setText(f"错误: {error}")
        self._status_label.setStyleSheet("color: #ff453a;")
        self._status_bar.showMessage(f"错误: {error}")

    def _on_play_chinese_changed(self, state: int) -> None:
        """游戏语音翻译播报选项变化"""
        from src.utils.config import ConfigManager
        mgr = ConfigManager()
        mgr.update("ui", play_chinese_voice=bool(state))
        self.config.ui.play_chinese_voice = bool(state)
        if self._is_running:
            self._status_bar.showMessage("设置已保存，下次启动生效", 3000)

    def _on_play_outbound_changed(self, state: int) -> None:
        """麦克风翻译输出播报选项变化"""
        from src.utils.config import ConfigManager
        mgr = ConfigManager()
        mgr.update("ui", play_outbound_voice=bool(state))
        self.config.ui.play_outbound_voice = bool(state)
        if self._is_running:
            self._status_bar.showMessage("设置已保存，下次启动生效", 3000)

    def _update_volc_usage_display(self) -> None:
        """更新火山引擎用量显示"""
        try:
            volc_usage = self.config.volc_usage
            
            # 更新进度条
            percent = volc_usage.usage_percent
            self._volc_usage_progress.setValue(int(percent))
            
            # 根据用量百分比改变颜色
            if percent < 50:
                color = "#34c759"  # 绿色
            elif percent < 80:
                color = "#ff9500"  # 橙色
            else:
                color = "#ff453a"  # 红色
            
            self._volc_usage_progress.setStyleSheet(f"""
                QProgressBar {{
                    border: 1px solid #3a3a3c;
                    border-radius: 6px;
                    background-color: #2c2c2e;
                    color: #ffffff;
                    text-align: center;
                    height: 20px;
                }}
                QProgressBar::chunk {{
                    border-radius: 6px;
                    background-color: {color};
                }}
            """)
            
            # 更新详情标签
            total = volc_usage.total_tokens
            cost = volc_usage.total_cost
            quota = volc_usage.monthly_quota
            self._volc_usage_detail.setText(
                f"累计使用: {total:,} tokens | 费用: {cost:.2f} 元 | 配额: {quota:,}"
            )
        except Exception as e:
            from src.utils.logger import logger
            logger.debug(f"更新用量显示失败: {e}")

    def _reset_volc_usage(self) -> None:
        """重置火山引擎用量统计"""
        try:
            self.config.volc_usage.total_input_tokens = 0
            self.config.volc_usage.total_output_text_tokens = 0
            self.config.volc_usage.total_output_audio_tokens = 0
            self.config.volc_usage.total_cost = 0.0
            self._update_volc_usage_display()
            from src.utils.logger import logger
            logger.info("火山引擎用量统计已重置")
        except Exception as e:
            from src.utils.logger import logger
            logger.error(f"重置用量统计失败: {e}")

    def _on_volc_usage_timer(self) -> None:
        """定时更新用量显示"""
        if self._is_running:
            self._update_volc_usage_display()

    def _on_close(self) -> None:
        self._stop_translation()
        if self._game_overlay:
            self._game_overlay.close()
        if self._mic_overlay:
            self._mic_overlay.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._on_close()
        event.accept()
