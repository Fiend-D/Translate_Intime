"""Dual-channel control panel: sidebar navigation shell with switchable themes."""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import shutil
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, override

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
    QStandardItemModel,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.audio.device_guard import (
    detect_vb_cable_link,
    find_preferred_system_loopback,
    find_preferred_vb_output,
    is_vb_cable_capture,
    is_vb_cable_input,
    is_virtual_or_loopback_input,
    shares_physical_output_path,
    shares_virtual_cable_path,
    validate_channel_devices,
    vb_cable_setup_hint,
)
from src.core.audio_capture import AudioCapture
from src.core.audio_player import AudioPlayer
from src.core.dota_coach import DEFAULT_COACH_URL, DotaCoachBridge
from src.core.music_share import (
    MusicSharePlayer,
    PreparedMusicTrack,
    list_audio_files,
    prepare_music_track,
)
from src.core.pipeline import TranslationPipeline
from src.core.usage_tracker import UsageState, UsageTracker
from src.core.volc_engine import VOLC_VOICE_OPTIONS
from src.engines.pipeline.llm_mt import HYMT_MODELS, hymt_model
from src.engines.pipeline.model_sources import (
    ModelSource,
    asr_source,
    format_source,
    hymt_source,
    kokoro_source,
    melo_source,
    nllb_source,
    silero_source,
    source_tooltip,
)
from src.engines.pipeline.qwen_asr import QWEN_ASR_MODELS
from src.gui.music_sidebar import MusicSidebarOverlay
from src.gui.phrase_overlay import PhraseOverlay
from src.gui.settings_dirty import wire_dirty_tracking
from src.gui.styles import (
    get_stylesheet,
    install_combo_popup_styling,
    menu_stylesheet,
    polish_combo_popup,
    resolve_theme,
    theme_choices,
)
from src.gui.subtitle_overlay import MERGED_KEY, SubtitleOverlay
from src.gui.toast import show_toast
from src.models.config import (
    AppConfigModel,
    HotkeyConfig,
    QuickPhraseSettings,
    ThemeKey,
    normalize_theme_key,
)
from src.models.enums import Direction
from src.models.subtitle import SubtitleEntry
from src.utils import download_progress
from src.utils.config_manager import (
    SECRET_FIELDS,
    load_config,
    merge_config_updates,
    save_config,
)
from src.utils.download_progress import format_task
from src.utils.hold_key import HoldKeyListener
from src.utils.hotkeys import (
    DEFAULT_HOTKEYS,
    HOTKEY_LABELS,
    AppHotkeys,
    GlobalHotkeys,
    HotkeyBridge,
    combo_to_display,
)
from src.utils.logger import logger
from src.utils.resource_paths import resolve_log_dir


class _UsageBridge(QObject):
    updated = pyqtSignal(object)  # UsageState


class _PreviewBridge(QObject):
    message = pyqtSignal(str)


class _ChannelOpBridge(QObject):
    """Delivers a finished channel start/stop back to the Qt thread."""

    finished = pyqtSignal(object)  # Callable[[], None]


class _MusicBridge(QObject):
    progress = pyqtSignal(float, float)
    finished = pyqtSignal()
    loaded = pyqtSignal(int, int, bool, object)
    load_failed = pyqtSignal(int, object, str)


class _WheelBlockFilter(QObject):
    """Keep the wheel from editing combos/spins/sliders; scroll the page instead."""

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if obj is None or event is None:
            return False
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, QWidget):
            return False
        cur: QWidget | None = obj
        # Stop at window boundaries: an open combo popup is its own window and must scroll.
        while cur is not None and not cur.isWindow():
            if isinstance(cur, (QComboBox, QAbstractSpinBox, QSlider)):
                area = self._scroll_area_of(cur)
                if area is not None and (viewport := area.viewport()) is not None:
                    QApplication.sendEvent(viewport, event.clone())
                return True
            cur = cur.parentWidget()
        return False

    @staticmethod
    def _scroll_area_of(widget: QWidget) -> QAbstractScrollArea | None:
        cur = widget.parentWidget()
        while cur is not None:
            if isinstance(cur, QAbstractScrollArea):
                return cur
            if cur.isWindow():
                return None
            cur = cur.parentWidget()
        return None


_LANG_LABELS = [
    ("中文 zh", "zh"),
    ("English en", "en"),
    ("日本語 ja", "ja"),
    ("한국어 ko", "ko"),
]

_PAGE_TITLES = (
    ("渡入·听", "游戏字幕：听游戏里的外语，实时显示中文字幕浮层"),
    ("渡出·说", "麦克风同传：对着麦克风说话，实时译成外语，可播放到虚拟声卡进游戏"),
    ("点歌台", "音乐分享：选一个文件夹作曲库，把音乐播到虚拟声卡给队友听"),
    ("设置", "字幕外观、翻译引擎、推理设备与助手"),
    ("书信", "打字翻译：输入文字 → 翻译 → 朗读（独立通道，不影响同传）"),
    ("航海日志", "翻译记录：按会话查看、搜索并导出字幕"),
    ("锦囊", "常用语：点一下就输入到游戏聊天框，可自动翻译成外语"),
)
_PAGE_TYPED = 4
_PAGE_TRANSCRIPTS = 5
_PAGE_PHRASES = 6


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _missing_runtime(name: str) -> str:
    return f"安装版未包含 {name}" if getattr(sys, "frozen", False) else f"未安装 {name}"


class MainWindow(QMainWindow):
    """Control panel: config only; lyrics overlays carry live subtitles."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("声渡 SoundFerry")
        self.setMinimumSize(940, 600)
        self.resize(1240, 780)

        self._registry = None
        try:
            self._config = load_config()
        except Exception as exc:
            logger.error(f"Configuration load failed; preserving stored credentials: {exc}")
            self._config = AppConfigModel()
            self._config_load_error = str(exc)
        self._apply_model_storage()

        self._theme: ThemeKey = self._config.theme_mode
        self._active_theme = resolve_theme(self._theme)
        self._pipeline: TranslationPipeline | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_process_tick)
        self._timer.setInterval(30)
        # 资源监控定时器 (独立于 _timer, 间隔 1.5s)
        self._resource_monitor: Any = None
        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self._on_resource_tick)
        self._resource_timer.setInterval(1500)
        # Model downloads can run without a channel (e.g. voice preview), so poll separately.
        self._download_timer = QTimer(self)
        self._download_timer.timeout.connect(self._refresh_downloads)
        self._download_timer.setInterval(500)
        self._overlays: dict[Direction, SubtitleOverlay] = {}
        self._corpus_hotwords: list[str] = list(getattr(self._config, "hotwords", None) or [])
        self._corpus_glossary: dict[str, str] = dict(getattr(self._config, "glossary", None) or {})
        self._music_bridge = _MusicBridge(self)
        self._music_bridge.progress.connect(self._on_music_progress)
        self._music_bridge.finished.connect(self._on_music_finished)
        self._music_bridge.loaded.connect(self._on_music_loaded)
        self._music_bridge.load_failed.connect(self._on_music_load_failed)
        self._music = MusicSharePlayer(
            on_progress=lambda pos, dur: self._music_bridge.progress.emit(pos, dur),
            on_finished=lambda: self._music_bridge.finished.emit(),
        )
        self._music_tracks: list[Path] = []
        self._music_folder: Path | None = None
        # Hotkeys edited in the dialog but not saved yet
        self._hotkeys_draft: HotkeyConfig | None = None
        self._music_sidebar: MusicSidebarOverlay | None = None
        self._music_switching = False
        self._music_load_generation = 0
        self._music_pending_autoplay = False
        self._music_loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="music-loader")
        self._music_load_future: Future[None] | None = None
        self._music_load_cancel = threading.Event()
        self._hotkeys = GlobalHotkeys(HotkeyBridge(self))
        self._hotkeys.bridge.activated.connect(self._on_hotkey)
        self._app_hotkeys: AppHotkeys | None = None
        self._hotkey_guard: dict[str, float] = {}
        self._hotkeys_capturing = False
        self._dota_coach_armed = False
        self._dota_coach_timer = QTimer(self)
        self._dota_coach_timer.setSingleShot(True)
        self._dota_coach_timer.timeout.connect(self._on_dota_coach_timeout)
        # Dragging an overlay emits many geometry updates; write the config once it settles.
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(800)
        self._geometry_save_timer.timeout.connect(self._persist_config)
        self._dota_bridge = DotaCoachBridge(self)
        self._dota_bridge.finished.connect(self._on_dota_coach_finished)

        usage_path = resolve_log_dir(self._config.log_dir) / "usage_data.json"
        # Engine connects (Volc WebSocket) and device teardown can take seconds;
        # one worker keeps channel operations ordered and off the Qt thread.
        self._channel_ops = ThreadPoolExecutor(max_workers=1, thread_name_prefix="channel-op")
        # Pending operations per channel ("mic"/"game"); zero counts are removed.
        self._channel_busy: Counter[str] = Counter()
        self._channel_bridge = _ChannelOpBridge(self)
        self._channel_bridge.finished.connect(lambda done: done())
        self._closing = False
        self._error_box: QMessageBox | None = None
        self._error_repeats = 0
        self._preview_bridge = _PreviewBridge(self)
        self._preview_bridge.message.connect(self._append_log)
        self._preview_stop = threading.Event()
        self._preview_thread: threading.Thread | None = None
        self._usage_bridge = _UsageBridge(self)
        self._usage_bridge.updated.connect(self._on_usage_state)
        self._usage_tracker = UsageTracker(
            usage_path,
            on_update=lambda st: self._usage_bridge.updated.emit(st),
        )
        self._pack_remain_text = "剩余：未查询"
        self._lang_signals_wired = False
        self._current_tab_index = 0
        self._typed_languages_set = False
        self._model_loading_names: tuple[str, ...] = ()
        self._download_rows: dict[str, tuple[QWidget, QLabel, QProgressBar]] = {}
        self._download_states: dict[str, str] = {}
        self._download_status_active = False
        self._source_updaters: list[Callable[[], None]] = []
        self._wheel_filter = _WheelBlockFilter(self)

        self._setup_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._wheel_filter)
            self._combo_popup_styler = install_combo_popup_styling(app)
        for combo in self.findChildren(QComboBox):
            polish_combo_popup(combo)
        self._app_hotkeys = AppHotkeys(self, self._on_hotkey)
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
        self._apply_theme()
        self._load_config_into_ui()
        self._start_resource_monitor()
        self._apply_hotkeys()
        self._refresh_usage_chip(self._usage_tracker.state)
        self._update_save_button()
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setInterval(700)
        self._dirty_timer.timeout.connect(self._update_save_button)
        self._dirty_timer.start()
        self._append_log("界面已就绪")
        if getattr(self, "_config_load_error", ""):
            message = f"配置文件无法读取，已使用默认设置：{self._config_load_error}"
            self._append_log(message)
            show_toast("配置文件损坏，已使用默认设置（原文件已备份）", ms=6000)
        self._append_log("提示：设置修改后点右上角「保存」生效")

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = self._build_header()
        root.addWidget(self._build_sidebar())

        main = QVBoxLayout()
        main.setContentsMargins(24, 18, 20, 18)
        main.setSpacing(14)
        main.addWidget(self._header)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        main.addWidget(self._progress)

        body = QHBoxLayout()
        body.setSpacing(16)

        self._pages = QStackedWidget()
        self._pages.setObjectName("pages")
        self._pages.addWidget(self._wrap_scroll(self._build_game_column()))
        self._pages.addWidget(self._wrap_scroll(self._build_mic_column()))
        self._pages.addWidget(self._wrap_scroll(self._build_music_column()))
        self._pages.addWidget(self._wrap_scroll(self._build_settings_panel()))
        self._pages.addWidget(self._wrap_scroll(self._build_typed_page()))
        self._pages.addWidget(self._build_transcripts_page())
        self._pages.addWidget(self._build_phrase_page())
        body.addWidget(self._pages, 1)
        self._log_panel = self._build_log_panel()
        body.addWidget(self._log_panel, 0)
        main.addLayout(body, 1)
        root.addLayout(main, 1)
        self._show_page(0)

        self._populate_devices()
        self._spn_font.valueChanged.connect(self._on_look_changed)
        self._spn_opacity.valueChanged.connect(self._on_look_changed)
        self._spn_history.valueChanged.connect(self._on_look_changed)
        self._chk_show_original.toggled.connect(self._on_look_changed)
        self._chk_subtitle_auto_hide.toggled.connect(self._on_look_changed)
        self._chk_subtitle_merged.toggled.connect(self._on_look_changed)
        self._chk_show_mic.toggled.connect(self._on_show_mic_toggled)
        self._chk_show_game.toggled.connect(self._on_show_game_toggled)
        self._chk_enable_mic.toggled.connect(self._on_enable_toggled)
        self._chk_enable_game.toggled.connect(self._on_enable_toggled)
        self._wire_dirty_tracking()
        self._on_enable_toggled()

    def _wire_dirty_tracking(self) -> None:
        """Refresh unsaved-state UI for every current and future settings control."""
        wire_dirty_tracking(self, self._update_save_button)

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        """Allow panel content to scroll instead of crushing text when short."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(widget)
        return scroll

    def _nav_item(self, text: str, tip: str = "", *, checkable: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(checkable)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            button.setToolTip(tip)
        return button

    def _nav_group(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("navGroup")
        return label

    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(212)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(3)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel("渡")
        mark.setObjectName("logoMark")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(mark)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("声渡")
        title.setObjectName("appTitle")
        names.addWidget(title)
        subtitle = QLabel("SoundFerry · 实时同传")
        subtitle.setObjectName("appSubtitle")
        names.addWidget(subtitle)
        brand.addLayout(names)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(10)

        self._nav_group_buttons = QButtonGroup(self)
        self._nav_group_buttons.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []

        def add_page(text: str, index: int) -> None:
            button = self._nav_item(text, checkable=True)
            button.clicked.connect(lambda _=False, i=index: self._on_nav_clicked(i))
            self._nav_group_buttons.addButton(button, index)
            self._nav_buttons.append(button)
            layout.addWidget(button)

        layout.addWidget(self._nav_group("同传"))
        add_page("◉   渡入·听", 0)
        add_page("◎   渡出·说", 1)
        add_page("♪   点歌台", 2)

        layout.addWidget(self._nav_group("工具"))
        add_page("✎   书信", _PAGE_TYPED)
        add_page("☰   航海日志", _PAGE_TRANSCRIPTS)
        add_page("❝   锦囊", _PAGE_PHRASES)
        self._btn_adjust = self._nav_item(
            "▣   调整字幕",
            "不开通道也能调出字幕浮层，摆好位置和大小。\n"
            "锁定后的浮层点击穿透、鼠标点不到，要在这里解锁；调好后在浮层上双击重新锁定。",
        )
        self._btn_adjust.clicked.connect(self._on_preview_overlays)
        layout.addWidget(self._btn_adjust)
        self._btn_detect_cable = self._nav_item(
            "⇆   检测虚拟声卡", "一键检测 VB-Cable / 虚拟声卡链路是否完整"
        )
        self._btn_detect_cable.clicked.connect(self._on_detect_vb_cable)
        layout.addWidget(self._btn_detect_cable)

        layout.addWidget(self._nav_group("系统"))
        self._btn_hotkeys = self._nav_item("⌨   快捷键", "自定义全局快捷键")
        self._btn_hotkeys.clicked.connect(self._on_edit_hotkeys)
        layout.addWidget(self._btn_hotkeys)
        add_page("⚙   设置", 3)

        layout.addStretch(1)
        layout.addWidget(self._field_label("主题"))
        self._cmb_theme = QComboBox()
        for label, key in theme_choices():
            self._cmb_theme.addItem(label, key)
        self._cmb_theme.setToolTip("界面主题，保存后生效；「跟随系统」按系统明暗切换")
        self._cmb_theme.currentIndexChanged.connect(self._on_theme_selected)
        layout.addWidget(self._cmb_theme)
        version = QLabel("v0.4")
        version.setObjectName("statusLabel")
        layout.addWidget(version)
        return bar

    def _build_header(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._page_title = QLabel()
        self._page_title.setObjectName("pageTitle")
        titles.addWidget(self._page_title)
        self._page_subtitle = QLabel()
        self._page_subtitle.setObjectName("appSubtitle")
        titles.addWidget(self._page_subtitle)
        layout.addLayout(titles)
        layout.addStretch()
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._badge = QLabel("Idle")
        self._badge.setObjectName("badgeLabel")
        layout.addWidget(self._badge)

        self._status_label = QLabel("状态：空闲")
        self._status_label.setObjectName("pill")
        layout.addWidget(self._status_label)

        self._latency_label = QLabel("延迟：—")
        self._latency_label.setObjectName("pill")
        layout.addWidget(self._latency_label)

        self._btn_usage = QPushButton("用量 —")
        self._btn_usage.setObjectName("ghostButton")
        self._btn_usage.setToolTip(
            "本地累计 Token/费用（来自 AST UsageResponse）。\n"
            "点击查看明细；设置里填 IAM AK/SK 可查询资源包剩余量。"
        )
        self._btn_usage.clicked.connect(self._on_open_usage)
        layout.addWidget(self._btn_usage)

        self._chk_advanced = QCheckBox("全部设备")
        self._chk_advanced.setToolTip(
            "「游戏声音」列表中也显示虚拟线缆捕获源（CABLE Output 等）。\n"
            "一般不需要：选错会把自己的译文再翻译一遍。"
        )
        self._chk_advanced.toggled.connect(self._on_advanced_toggled)
        layout.addWidget(self._chk_advanced)

        self._btn_status_panel = QPushButton("◨ 面板")
        self._btn_status_panel.setObjectName("iconButton")
        self._btn_status_panel.setCheckable(True)
        self._btn_status_panel.setChecked(True)
        self._btn_status_panel.setToolTip("显示 / 收起右侧运行状态面板")
        self._btn_status_panel.toggled.connect(self._on_status_panel_toggled)
        layout.addWidget(self._btn_status_panel)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("accentButton")
        self._btn_save.setToolTip(
            "保存设置到本地，所有设置保存后生效。\n"
            "浮层上的拖动、锁定、滚轮调整和字幕显隐开关是即时的，不需要保存。"
        )
        self._btn_save.clicked.connect(self._on_save)
        layout.addWidget(self._btn_save)
        for pill in (self._badge, self._status_label, self._latency_label):
            pill.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return bar

    def _build_game_column(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("翻译字幕")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self._chk_enable_game = QCheckBox("启用")
        header.addWidget(self._chk_enable_game)
        layout.addLayout(header)

        tip = QLabel("听游戏里的外语 → 显示中文字幕（选「游戏声音」捕获设备）")
        tip.setObjectName("appSubtitle")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QVBoxLayout()
        form.setSpacing(8)

        lang_row = QHBoxLayout()
        src_box = QVBoxLayout()
        src_box.addWidget(self._field_label("源语言"))
        self._cmb_game_source = QComboBox()
        self._prep_combo(self._cmb_game_source)
        self._fill_lang_combo(self._cmb_game_source)
        src_box.addWidget(self._cmb_game_source)
        lang_row.addLayout(src_box, 1)

        tgt_box = QVBoxLayout()
        tgt_box.addWidget(self._field_label("字幕语言"))
        self._cmb_game_target = QComboBox()
        self._prep_combo(self._cmb_game_target)
        self._fill_lang_combo(self._cmb_game_target)
        tgt_box.addWidget(self._cmb_game_target)
        lang_row.addLayout(tgt_box, 1)
        form.addLayout(lang_row)

        self._loopback_row = QWidget()
        lb_lay = QVBoxLayout(self._loopback_row)
        lb_lay.setContentsMargins(0, 0, 0, 0)
        lb_lay.setSpacing(4)
        lb_lay.addWidget(self._field_label("游戏声音"))
        self._cmb_loopback = QComboBox()
        self._prep_combo(self._cmb_loopback)
        self._cmb_loopback.setToolTip(
            "选择要翻译的游戏/系统声音来源。\n"
            "Linux：选带「系统声音回采 / monitor」的项\n"
            "Windows：选「系统正在播放的声音 / Loopback」\n"
            "不要选 CABLE Output（会与同传虚拟线冲突）"
        )
        lb_lay.addWidget(self._cmb_loopback)
        form.addWidget(self._loopback_row)
        layout.addLayout(form)

        opts = QHBoxLayout()
        self._chk_show_game = QCheckBox("显示浮层")
        self._chk_play_game = QCheckBox("语音输出")
        opts.addWidget(self._chk_show_game)
        opts.addWidget(self._chk_play_game)
        opts.addStretch()
        opts.addWidget(self._refresh_devices_button())
        layout.addLayout(opts)

        self._game_preview = self._preview_box("游戏语音翻译字幕将显示在桌面浮层…")
        layout.addWidget(self._game_preview, 1)

        self._btn_game = QPushButton("▶  开始字幕")
        self._btn_game.setObjectName("primaryButton")
        self._btn_game.clicked.connect(lambda: self._on_channel_toggle("game"))
        layout.addWidget(self._btn_game)
        return card

    def _build_mic_column(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("麦克风区域")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self._chk_enable_mic = QCheckBox("启用")
        header.addWidget(self._chk_enable_mic)
        layout.addLayout(header)

        tip = QLabel("我对着麦克风说话 → 译成外语（可选播放到虚拟声卡进游戏）")
        tip.setObjectName("appSubtitle")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QVBoxLayout()
        form.setSpacing(8)

        form.addWidget(self._field_label("我的麦克风"))
        self._cmb_input = QComboBox()
        self._prep_combo(self._cmb_input)
        self._cmb_input.setToolTip("选择你说话用的麦克风")
        form.addWidget(self._cmb_input)

        self._output_row = QWidget()
        out_lay = QVBoxLayout(self._output_row)
        out_lay.setContentsMargins(0, 0, 0, 0)
        out_lay.setSpacing(4)
        out_lay.addWidget(self._field_label("译文播放到"))
        self._cmb_output = QComboBox()
        self._prep_combo(self._cmb_output)
        self._cmb_output.setToolTip(
            "翻译后的语音从哪里播放。\n"
            "只自己听：选耳机/扬声器\n"
            "让游戏里队友听到：选虚拟声卡（如 CABLE Input / translator_virtual_sink）"
        )
        out_lay.addWidget(self._cmb_output)
        form.addWidget(self._output_row)

        lang_row = QHBoxLayout()
        src_box = QVBoxLayout()
        src_box.addWidget(self._field_label("我说"))
        self._cmb_mic_source = QComboBox()
        self._prep_combo(self._cmb_mic_source)
        self._fill_lang_combo(self._cmb_mic_source)
        src_box.addWidget(self._cmb_mic_source)
        lang_row.addLayout(src_box, 1)

        tgt_box = QVBoxLayout()
        tgt_box.addWidget(self._field_label("译成"))
        self._cmb_mic_target = QComboBox()
        self._prep_combo(self._cmb_mic_target)
        self._fill_lang_combo(self._cmb_mic_target)
        tgt_box.addWidget(self._cmb_mic_target)
        lang_row.addLayout(tgt_box, 1)
        form.addLayout(lang_row)
        layout.addLayout(form)

        # Keep internal aliases used by collect/load (synced with game combos)
        self._cmb_source = self._cmb_mic_source
        self._cmb_target = self._cmb_mic_target

        opts = QHBoxLayout()
        self._chk_show_mic = QCheckBox("显示浮层")
        self._chk_play_mic = QCheckBox("语音输出")
        opts.addWidget(self._chk_show_mic)
        opts.addWidget(self._chk_play_mic)
        opts.addStretch()
        opts.addWidget(self._refresh_devices_button())
        layout.addLayout(opts)

        self._mic_preview = self._preview_box("麦克风翻译结果将显示在桌面浮层…")
        layout.addWidget(self._mic_preview, 1)

        self._btn_mic = QPushButton("🎙  开启麦克风")
        self._btn_mic.setObjectName("primaryButton")
        self._btn_mic.clicked.connect(lambda: self._on_channel_toggle("mic"))
        layout.addWidget(self._btn_mic)
        return card

    def _build_music_column(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("音乐分享")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        tip = QLabel(
            "选一个文件夹作曲库 → 下拉选歌 → 播到虚拟声卡给队友听。\n"
            "播放后右侧会出现透明侧栏，可一键切歌。"
        )
        tip.setObjectName("appSubtitle")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        folder_row = QHBoxLayout()
        self._lbl_music_folder = QLabel("未选择文件夹")
        self._lbl_music_folder.setObjectName("fieldLabel")
        self._lbl_music_folder.setWordWrap(True)
        folder_row.addWidget(self._lbl_music_folder, 1)
        self._btn_music_folder = QPushButton("选文件夹…")
        self._btn_music_folder.setObjectName("ghostButton")
        self._btn_music_folder.clicked.connect(self._on_music_pick_folder)
        folder_row.addWidget(self._btn_music_folder)
        layout.addLayout(folder_row)

        layout.addWidget(self._field_label("曲目"))
        self._cmb_music_track = QComboBox()
        self._prep_combo(self._cmb_music_track)
        self._cmb_music_track.setEnabled(False)
        self._cmb_music_track.currentIndexChanged.connect(self._on_music_track_combo)
        layout.addWidget(self._cmb_music_track)

        self._lbl_music_time = QLabel("00:00 / 00:00")
        self._lbl_music_time.setObjectName("fieldLabel")
        layout.addWidget(self._lbl_music_time)

        self._sld_music_vol = QSlider(Qt.Orientation.Horizontal)
        self._sld_music_vol.setRange(0, 100)
        self._sld_music_vol.setValue(100)
        self._sld_music_vol.setToolTip(
            "音量（100=原始音量；为避免失真，音乐通路不做超过 100% 的数字增益）"
        )
        self._sld_music_vol.valueChanged.connect(self._on_music_volume)
        vol_row = QHBoxLayout()
        vol_row.addWidget(self._field_label("音量"))
        vol_row.addWidget(self._sld_music_vol, 1)
        layout.addLayout(vol_row)

        opts = QHBoxLayout()
        self._chk_music_loop = QCheckBox("单曲循环")
        self._chk_music_loop.toggled.connect(lambda v: self._music.set_loop(v))
        self._chk_music_auto_next = QCheckBox("播完下一首")
        self._chk_music_auto_next.setChecked(True)
        self._chk_music_auto_next.setToolTip("非单曲循环时，播完自动切下一首")
        opts.addWidget(self._chk_music_loop)
        opts.addWidget(self._chk_music_auto_next)
        opts.addStretch()
        layout.addLayout(opts)

        from src.gui.hotkey_edit import HotkeyEdit

        mode_row = QHBoxLayout()
        mode_row.addWidget(self._field_label("播放方式"))
        self._cmb_music_mode = QComboBox()
        self._prep_combo(self._cmb_music_mode)
        self._cmb_music_mode.addItem("自由播放（播放后直到暂停/停止）", "free")
        self._cmb_music_mode.addItem("按住按键播放（松开即停）", "hold")
        self._cmb_music_mode.setToolTip(
            "按住按键播放：按住设定的按键开始播放当前曲目（没选则第一首），松开立即停止；"
            "游戏在前台时也有效，按键照常传给游戏"
        )
        mode_row.addWidget(self._cmb_music_mode, 1)
        layout.addLayout(mode_row)

        hold_row = QHBoxLayout()
        self._lbl_music_hold = self._field_label("按键")
        hold_row.addWidget(self._lbl_music_hold)
        self._edt_music_hold = HotkeyEdit(allow_single_key=True, allow_mouse=True)
        self._edt_music_hold.setToolTip(
            "点击后按下要按住的键：单键（如 F8、`）、组合键，或鼠标侧键/中键"
        )
        hold_row.addWidget(self._edt_music_hold, 1)
        self._lbl_music_delay = self._field_label("延迟")
        hold_row.addWidget(self._lbl_music_delay)
        self._spn_music_delay = QSpinBox()
        self._spn_music_delay.setRange(0, 2000)
        self._spn_music_delay.setSingleStep(50)
        self._spn_music_delay.setSuffix(" ms")
        self._spn_music_delay.setValue(300)
        self._spn_music_delay.setToolTip("按住超过这个时间才开始播放，轻点按键不会出声")
        hold_row.addWidget(self._spn_music_delay)
        layout.addLayout(hold_row)

        self._music_hold = HoldKeyListener(self)
        self._music_hold.pressed.connect(self._on_music_hold_pressed)
        self._music_hold.released.connect(self._on_music_hold_released)
        self._music_hold_timer = QTimer(self)
        self._music_hold_timer.setSingleShot(True)
        self._music_hold_timer.timeout.connect(self._on_music_hold_elapsed)
        self._music_hold_playing = False
        self._music_hold_load_gen = -1
        self._edt_music_hold.capturing_changed.connect(
            lambda capturing: self._music_hold.set_enabled(not capturing)
        )
        self._cmb_music_mode.currentIndexChanged.connect(self._on_music_hold_settings_changed)
        self._edt_music_hold.combo_changed.connect(self._on_music_hold_settings_changed)
        self._spn_music_delay.valueChanged.connect(self._on_music_hold_settings_changed)

        note = QLabel(
            "输出设备与麦克风同传共用「译文播放到」。播音乐时建议关掉麦克风语音输出，避免抢声道。"
        )
        note.setObjectName("fieldLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        self._btn_music_play = QPushButton("▶  播放到输出")
        self._btn_music_play.setObjectName("primaryButton")
        self._btn_music_play.clicked.connect(self._on_music_play)
        self._btn_music_pause = QPushButton("暂停")
        self._btn_music_pause.setObjectName("ghostButton")
        self._btn_music_pause.clicked.connect(self._on_music_pause)
        self._btn_music_stop = QPushButton("停止")
        self._btn_music_stop.setObjectName("ghostButton")
        self._btn_music_stop.clicked.connect(self._on_music_stop)
        btn_row.addWidget(self._btn_music_play, 1)
        btn_row.addWidget(self._btn_music_pause)
        btn_row.addWidget(self._btn_music_stop)
        layout.addLayout(btn_row)
        return card

    _SETTINGS_TABS = ("常规", "云端服务", "本地模型", "助手")

    def _build_settings_panel(self) -> QWidget:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        seg = QFrame()
        seg.setObjectName("segBar")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(4, 4, 4, 4)
        seg_lay.setSpacing(4)
        self._settings_tab_group = QButtonGroup(self)
        self._settings_tab_group.setExclusive(True)
        for index, name in enumerate(self._SETTINGS_TABS):
            button = QPushButton(name)
            button.setObjectName("segButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._settings_tab_group.addButton(button, index)
            seg_lay.addWidget(button)
        seg_lay.addStretch(1)
        self._settings_tab_group.idClicked.connect(self._show_settings_tab)
        outer.addWidget(seg)

        self._settings_stack = QStackedWidget()
        self._usage_pills: dict[str, QLabel] = {}
        for build in (
            self._build_settings_general,
            self._build_settings_cloud,
            self._build_settings_local,
            self._build_settings_assistant,
        ):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            build(lay)
            lay.addStretch(1)
            self._settings_stack.addWidget(page)
        outer.addWidget(self._settings_stack, 1)
        self._show_settings_tab(0)
        self._refresh_settings_usage()
        return root

    def _show_settings_tab(self, index: int) -> None:
        # QStackedWidget sizes to its tallest page; ignore hidden pages so short
        # tabs do not leave a long empty scroll area.
        for i in range(self._settings_stack.count()):
            page = self._settings_stack.widget(i)
            if page is None:
                continue
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            page.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self._settings_stack.setCurrentIndex(index)
        button = self._settings_tab_group.button(index)
        if button is not None:
            button.setChecked(True)
        self._settings_stack.updateGeometry()

    def _settings_card(
        self,
        parent: QVBoxLayout,
        title: str,
        *,
        kind: str = "",
        usage_key: str = "",
        desc: str = "",
    ) -> QVBoxLayout:
        card = self._card()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        header.addWidget(label)
        if kind:
            tag = QLabel({"local": "本地", "cloud": "云端"}[kind])
            tag.setObjectName("kindLocal" if kind == "local" else "kindCloud")
            header.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        if usage_key:
            pill = QLabel("未使用")
            pill.setObjectName("usageOff")
            header.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            self._usage_pills[usage_key] = pill
        layout.addLayout(header)
        if desc:
            hint = QLabel(desc)
            hint.setObjectName("fieldLabel")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        parent.addWidget(card)
        return layout

    def _field_hint(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)

    def _model_source_row(
        self, layout: QVBoxLayout, source: Callable[[], ModelSource | None]
    ) -> None:
        """Download origin + storage folder under a model picker, refreshed on selection."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        text = QLabel()
        text.setObjectName("sourceLabel")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(text, 1)
        button = QPushButton("打开目录")
        button.setObjectName("ghostButton")
        lay.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(row)
        current: list[ModelSource] = []

        def refresh() -> None:
            src = source()
            row.setVisible(src is not None)
            current[:] = [src] if src is not None else []
            if src is not None:
                text.setText(format_source(src))
                text.setToolTip(source_tooltip(src))
                button.setToolTip(str(src.path))

        def open_folder() -> None:
            if current:
                self._open_model_folder(current[0].path)

        button.clicked.connect(open_folder)
        refresh()
        self._source_updaters.append(refresh)

    def _refresh_model_sources(self, *_args: object) -> None:
        for refresh in self._source_updaters:
            refresh()

    @staticmethod
    def _asr_combo_source(combo: QComboBox) -> ModelSource | None:
        return asr_source(str(combo.currentData() or ""))

    def _on_browse_model_dir(self) -> None:
        from src.utils.resource_paths import model_resource_root

        start = self._txt_model_dir.text().strip() or str(model_resource_root())
        folder = QFileDialog.getExistingDirectory(self, "选择模型目录", start)
        if folder:
            self._txt_model_dir.setText(str(Path(folder)))

    def _model_dir_draft(self) -> str:
        if not hasattr(self, "_txt_model_dir"):
            return self._config.model_dir
        return self._txt_model_dir.text().strip()

    @staticmethod
    def _check_model_dir(value: str) -> None:
        """Reject a model folder that cannot be created or written before it is saved."""
        if not value:
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"模型目录请填写完整路径：{value}")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".soundferry-write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"模型目录无法写入：{path}（{exc}）") from exc

    def _apply_model_storage(self) -> None:
        from src.utils.model_fetch import set_mirror_preference
        from src.utils.resource_paths import set_model_dir

        set_model_dir(self._config.model_dir)
        set_mirror_preference(self._config.model_mirror)

    def _open_model_folder(self, path: Path) -> None:
        # Not downloaded yet: open the nearest existing parent so the user sees where it goes.
        target = path if path.is_dir() else path.parent
        while not target.exists() and target != target.parent:
            target = target.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._append_log(f"无法打开目录：{target}")

    def _refresh_downloads(self) -> None:
        tasks = download_progress.snapshot()
        for task in tasks:
            previous = self._download_states.get(task.key)
            if previous != task.state:
                if task.state == "running":
                    self._append_log(f"开始下载 {task.label}：{task.url or '准备中'} → {task.dest}")
                elif task.state == "done":
                    self._append_log(f"下载完成：{task.label} → {task.dest}")
                else:
                    self._append_log(format_task(task))
                    show_toast(format_task(task), ms=4000)
                self._download_states[task.key] = task.state
        live = {task.key for task in tasks}
        for key in [k for k in self._download_states if k not in live]:
            del self._download_states[key]
        self._render_download_rows(tasks)
        self._render_download_status(tasks)

    def _render_download_rows(self, tasks: list[download_progress.DownloadTask]) -> None:
        live = {task.key for task in tasks}
        for key in [k for k in self._download_rows if k not in live]:
            widget, _label, _bar = self._download_rows.pop(key)
            widget.deleteLater()
        for task in tasks:
            if task.key not in self._download_rows:
                widget = QWidget()
                lay = QVBoxLayout(widget)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(4)
                label = QLabel()
                label.setObjectName("fieldLabel")
                label.setWordWrap(True)
                bar = QProgressBar()
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                lay.addWidget(label)
                lay.addWidget(bar)
                self._download_list.addWidget(widget)
                self._download_rows[task.key] = (widget, label, bar)
            _widget, label, bar = self._download_rows[task.key]
            source = f"\n来源：{task.url}" if task.url and task.state == "running" else ""
            label.setText(format_task(task) + source)
            label.setToolTip(f"存放：{task.dest}")
            frac = task.fraction
            if task.state == "done":
                bar.setRange(0, 1000)
                bar.setValue(1000)
            elif frac is None:
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 1000)
                bar.setValue(int(frac * 1000))
        self._download_card.setVisible(bool(tasks))

    def _render_download_status(self, tasks: list[download_progress.DownloadTask]) -> None:
        running = [task for task in tasks if task.state == "running"]
        if not running:
            if self._download_status_active:
                self._download_status_active = False
                self._progress.setVisible(False)
                self._model_loading_names = ()
                self._restore_status_label()
            return
        self._download_status_active = True
        task = running[0]
        more = f" 等 {len(running)} 项" if len(running) > 1 else ""
        self._status_label.setText(f"状态：下载 {format_task(task)}{more}")
        self._progress.setVisible(True)
        frac = task.fraction
        if frac is None:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 1000)
            self._progress.setValue(int(frac * 1000))

    def _sub_title(self, layout: QVBoxLayout, text: str, usage_key: str = "") -> None:
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("subTitle")
        row.addWidget(label)
        row.addStretch(1)
        if usage_key:
            pill = QLabel("未使用")
            pill.setObjectName("usageOff")
            row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            self._usage_pills[usage_key] = pill
        layout.addLayout(row)

    def _form_row(self, layout: QVBoxLayout, label: str, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        caption = self._field_label(label)
        caption.setMinimumWidth(76)
        row.addWidget(caption)
        for widget in widgets:
            row.addWidget(widget, 1 if isinstance(widget, (QComboBox, QLineEdit)) else 0)
        layout.addLayout(row)
        return row

    def _secret_edit(self, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText(placeholder)
        return edit

    def _voice_combo(self, engine_id: str, lang: str) -> QComboBox:
        from src.engines.pipeline.tts_catalog import tts_engine

        info = tts_engine(engine_id)
        combo = QComboBox()
        self._prep_combo(combo)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for voice in info.voices_zh if lang == "zh" else info.voices_en:
            combo.addItem(voice.label, voice.id)
        combo.setToolTip("可从列表选择，也可直接粘贴音色 ID")
        combo.editTextChanged.connect(self._update_save_button)
        return combo

    def _tts_voice_rows(self, layout: QVBoxLayout, engine_id: str) -> None:
        from src.engines.pipeline.tts_catalog import voice_fields

        fields = voice_fields(engine_id)
        if fields is None:
            return
        zh = self._voice_combo(engine_id, "zh")
        en = self._voice_combo(engine_id, "en")
        self._tts_voice_combos[fields[0]] = zh
        self._tts_voice_combos[fields[1]] = en
        preview = QPushButton("试听")
        preview.setObjectName("ghostButton")
        preview.setToolTip("用当前音色合成一句示例并从默认扬声器播放")
        preview.clicked.connect(lambda: self._on_preview_tts(engine_id))
        self._form_row(layout, "中文音色", zh, preview)
        self._form_row(layout, "外语音色", en)

    @staticmethod
    def _voice_value(combo: QComboBox) -> str:
        text = combo.currentText().strip()
        index = combo.findText(text)
        if index >= 0 and combo.itemData(index) is not None:
            return str(combo.itemData(index))
        return text

    @staticmethod
    def _set_voice_value(combo: QComboBox, value: str) -> None:
        combo.blockSignals(True)
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(value)
        combo.blockSignals(False)

    # ── 常规 ────────────────────────────────────────────────────────────
    def _build_settings_general(self, page: QVBoxLayout) -> None:
        save_tip = QLabel(
            "所有设置点右上角「保存」后生效，未保存时切换页面或关闭窗口会提醒。"
            "浮层上的拖动、锁定、滚轮调整和字幕显隐开关是即时的。"
        )
        save_tip.setObjectName("fieldLabel")
        save_tip.setWordWrap(True)
        page.addWidget(save_tip)

        layout = self._settings_card(
            page,
            "翻译引擎",
            desc="选「本地」的组件在「本地模型」页设置模型；选「云端」的在「云端服务」页填写账号与音色。",
        )
        self._cmb_translation_mode = QComboBox()
        self._prep_combo(self._cmb_translation_mode)
        self._cmb_translation_mode.addItem("云端 · 火山同传（低延迟，按量计费）", "volc")
        self._cmb_translation_mode.addItem("经济模式（识别 + 翻译 + 语音 自由组合）", "economy")
        self._cmb_translation_mode.setToolTip(
            "切换翻译引擎。若通道正在运行会先停止，无需重启应用。\n"
            "火山同传：一个云端服务完成识别、翻译与配音；\n"
            "经济模式：下面三项可分别选本地或云端。"
        )
        self._cmb_translation_mode.currentIndexChanged.connect(self._refresh_settings_usage)
        self._form_row(layout, "引擎", self._cmb_translation_mode)

        self._economy_rows = QWidget()
        eco = QVBoxLayout(self._economy_rows)
        eco.setContentsMargins(0, 0, 0, 0)
        eco.setSpacing(10)
        self._cmb_asr_backend = QComboBox()
        self._prep_combo(self._cmb_asr_backend)
        if sys.platform.startswith("win"):
            self._cmb_asr_backend.addItem(
                "本地 · Windows Live Captions（系统级，推荐）", "live_captions"
            )
        self._cmb_asr_backend.addItem(
            "本地 · 离线模型（SenseVoice / Zipformer / Whisper）", "local"
        )
        self._cmb_asr_backend.addItem("云端 · 阿里云 Fun-ASR（DashScope）", "dashscope")
        self._cmb_asr_backend.addItem("云端 · Qwen3-ASR-Flash（DashScope）", "qwen_flash")
        self._cmb_asr_backend.addItem("云端 · 豆包极速识别（火山）", "doubao_flash")
        self._cmb_asr_backend.setToolTip(
            "Live Captions：Windows 11 22H2+ 系统级识别，零占用；\n"
            "离线模型：在「本地模型」页为麦克风/游戏分别选模型；\n"
            "Fun-ASR / Qwen3-ASR-Flash：需 DashScope API Key；\n"
            "豆包极速识别：复用火山 API Key，中文与热词效果最好。"
        )
        self._cmb_asr_backend.currentIndexChanged.connect(self._refresh_settings_usage)
        self._form_row(eco, "语音识别", self._cmb_asr_backend)

        self._cmb_mt_backend = QComboBox()
        self._prep_combo(self._cmb_mt_backend)
        self._cmb_mt_backend.addItem("本地 · NLLB（离线）", "nllb")
        self._cmb_mt_backend.addItem("本地 · Hy-MT2 混元翻译（llama.cpp）", "hymt")
        self._cmb_mt_backend.addItem("云端 · 大模型 LLM（OpenAI 兼容，推荐）", "llm")
        self._cmb_mt_backend.addItem("云端 · MyMemory（免费）", "mymemory")
        self._cmb_mt_backend.addItem("自动（NLLB → Argos → MyMemory）", "auto")
        self._cmb_mt_backend.setToolTip(
            "LLM：DeepSeek / 通义 / Ollama / LM Studio 等，带上下文与术语表，失败回退 NLLB；\n"
            "Hy-MT2：腾讯混元翻译 GGUF，型号（1.8B / 7B）在「本地模型」页选，需 pip install llama-cpp-python。\n"
            "保存后重新开启通道生效。"
        )
        if not _module_available("llama_cpp"):
            self._disable_combo_item(self._cmb_mt_backend, "hymt", _missing_runtime("llama.cpp"))
        self._cmb_mt_backend.currentIndexChanged.connect(self._refresh_settings_usage)
        self._form_row(eco, "翻译", self._cmb_mt_backend)

        from src.engines.pipeline.tts_catalog import TTS_ENGINE_ORDER, tts_engine

        self._cmb_tts_backend = QComboBox()
        self._prep_combo(self._cmb_tts_backend)
        for engine_id in TTS_ENGINE_ORDER:
            info = tts_engine(engine_id)
            self._cmb_tts_backend.addItem(info.combo_label, engine_id)
            self._cmb_tts_backend.setItemData(
                self._cmb_tts_backend.count() - 1,
                f"{info.needs}\n{info.note}".strip(),
                Qt.ItemDataRole.ToolTipRole,
            )
        self._cmb_tts_backend.setToolTip(
            "开启「语音输出」后播报译文用的语音引擎；云端引擎失败时自动回退 Edge TTS。\n"
            "保存后重新开启通道生效。"
        )
        self._cmb_tts_backend.currentIndexChanged.connect(self._refresh_settings_usage)
        self._spn_tts_speed = QDoubleSpinBox()
        self._spn_tts_speed.setRange(0.7, 1.3)
        self._spn_tts_speed.setSingleStep(0.05)
        self._spn_tts_speed.setDecimals(2)
        self._spn_tts_speed.setPrefix("语速 ")
        self._spn_tts_speed.setToolTip("经济模式语音合成语速（1.00 = 正常），对所有语音引擎生效")
        self._form_row(eco, "语音合成", self._cmb_tts_backend, self._spn_tts_speed)

        self._lbl_engine_hint = QLabel()
        self._lbl_engine_hint.setObjectName("fieldLabel")
        self._lbl_engine_hint.setWordWrap(True)
        eco.addWidget(self._lbl_engine_hint)
        layout.addWidget(self._economy_rows)

        layout = self._settings_card(page, "字幕外观", desc="保存后生效")
        self._spn_font = QSpinBox()
        self._spn_font.setRange(12, 72)
        self._spn_font.setToolTip("默认窗口大小下的字号；拖大字幕窗口时文字按比例放大")
        self._spn_opacity = QSpinBox()
        self._spn_opacity.setRange(0, 100)
        self._spn_opacity.setSingleStep(5)
        self._spn_opacity.setSuffix(" %")
        self._spn_opacity.setToolTip("只影响字幕底板；0% 为全透明，文字始终清晰")
        self._spn_history = QSpinBox()
        self._spn_history.setRange(0, 40)
        self._spn_history.setToolTip(
            "字幕浮层最多保留的已定稿句数；实际显示行数随窗口高度自动增减（拉高可回看更多）"
        )
        row = self._form_row(layout, "字号", self._spn_font)
        row.addWidget(self._field_label("底板不透明度"))
        row.addWidget(self._spn_opacity)
        row.addWidget(self._field_label("历史句数"))
        row.addWidget(self._spn_history)
        row.addStretch(1)

        self._subtitle_text_color = "#FFFFFF"
        self._subtitle_bg_color = "#141418"
        self._btn_text_color = QPushButton("文字颜色")
        self._btn_text_color.setObjectName("ghostButton")
        self._btn_text_color.clicked.connect(lambda: self._pick_subtitle_color("text"))
        self._btn_bg_color = QPushButton("底板颜色")
        self._btn_bg_color.setObjectName("ghostButton")
        self._btn_bg_color.clicked.connect(lambda: self._pick_subtitle_color("bg"))
        self._cmb_subtitle_font = QComboBox()
        self._prep_combo(self._cmb_subtitle_font)
        self._cmb_subtitle_font.addItem("默认字体", "")
        for family in QFontDatabase.families():
            self._cmb_subtitle_font.addItem(family, family)
        self._cmb_subtitle_font.setToolTip("字幕浮层字体，保存后生效")
        self._cmb_subtitle_font.currentIndexChanged.connect(self._on_look_changed)
        self._chk_subtitle_follow_theme = QCheckBox("跟随主题")
        self._chk_subtitle_follow_theme.setChecked(True)
        self._chk_subtitle_follow_theme.setToolTip(
            "字幕浮层使用当前主题的配色；关闭后使用右侧自定义的文字/底板颜色"
        )
        self._chk_subtitle_follow_theme.toggled.connect(self._on_subtitle_follow_toggled)
        style_row = QHBoxLayout()
        style_row.addWidget(self._chk_subtitle_follow_theme)
        style_row.addWidget(self._btn_text_color)
        style_row.addWidget(self._btn_bg_color)
        style_row.addWidget(self._cmb_subtitle_font, 1)
        layout.addLayout(style_row)
        self._refresh_color_buttons()
        self._chk_show_original = QCheckBox("浮层显示原文")
        self._chk_show_original.setChecked(True)
        self._chk_subtitle_merged = QCheckBox("麦克风和游戏合并为一个浮层")
        self._chk_subtitle_merged.setChecked(True)
        self._chk_subtitle_merged.setToolTip(
            "合并后每句字幕前标出「我」/「对方」；取消则分成两个浮层"
        )
        self._chk_subtitle_auto_hide = QCheckBox("锁定后无人说话时自动隐藏")
        self._chk_subtitle_auto_hide.setChecked(True)
        self._chk_subtitle_auto_hide.setToolTip(
            "浮层锁定（点击穿透）时，约 10 秒没有新字幕就淡出，有人说话立即出现"
        )
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self._chk_show_original)
        overlay_row.addWidget(self._chk_subtitle_merged)
        overlay_row.addWidget(self._chk_subtitle_auto_hide)
        overlay_row.addStretch(1)
        layout.addLayout(overlay_row)

        layout = self._settings_card(page, "声音处理", desc="更改后需重新开启通道")
        self._cmb_quality = QComboBox()
        self._prep_combo(self._cmb_quality)
        self._cmb_quality.addItem("质量优先", "quality")
        self._cmb_quality.addItem("均衡", "balanced")
        self._cmb_quality.addItem("省额度", "saver")
        self._cmb_quality.addItem("低延迟", "turbo")
        self._cmb_quality.setToolTip(
            "一键设置 VAD 送流策略：\n"
            "质量优先：尽量多送音频\n"
            "均衡：推荐\n"
            "省额度：更严门控，游戏通道也开 VAD\n"
            "低延迟：更短挂起"
        )
        self._cmb_quality.currentIndexChanged.connect(self._on_quality_preset_changed)
        self._form_row(layout, "质量档位", self._cmb_quality)

        self._chk_vad = QCheckBox("麦克风 VAD")
        self._chk_vad.setChecked(True)
        self._chk_vad.setToolTip("仅作用于麦克风通道。静音/空噪不送识别，减少幻觉。")
        self._chk_vad_game = QCheckBox("游戏/视频 VAD")
        self._chk_vad_game.setChecked(True)
        self._chk_vad_game.setToolTip("作用于游戏字幕通道。默认优先 Silero，空噪较多时建议开启。")
        self._chk_aec = QCheckBox("软件回声消除")
        self._chk_aec.setChecked(True)
        self._chk_aec.setToolTip(
            "外放音箱时消除麦克风里的译文回声，抢话只认真人说话。\n"
            "译文只进虚拟声卡或戴耳机时自动不生效。"
        )
        checks = QHBoxLayout()
        for chk in (self._chk_vad, self._chk_vad_game, self._chk_aec):
            checks.addWidget(chk)
        checks.addStretch(1)
        layout.addLayout(checks)

        self._cmb_vad_sens = QComboBox()
        self._prep_combo(self._cmb_vad_sens)
        self._cmb_vad_sens.addItem("宽松", "low")
        self._cmb_vad_sens.addItem("标准", "medium")
        self._cmb_vad_sens.addItem("严格", "high")
        self._cmb_vad_sens.setToolTip(
            "宽松：小声也送（易漏噪）\n标准：推荐（麦克风）\n严格：只送明显人声"
        )
        self._cmb_vad_backend = QComboBox()
        self._prep_combo(self._cmb_vad_backend)
        self._cmb_vad_backend.addItem("自动 Silero", "auto")
        self._cmb_vad_backend.addItem("仅 Silero", "silero")
        self._cmb_vad_backend.addItem("仅电平", "rms")
        self._cmb_vad_backend.setToolTip("自动：优先 Silero ONNX，不可用时回退电平 VAD。")
        row = self._form_row(layout, "VAD 灵敏度", self._cmb_vad_sens)
        row.addWidget(self._field_label("后端"))
        row.addWidget(self._cmb_vad_backend, 1)

        is_windows = sys.platform.startswith("win")
        self._cmb_original_audio = QComboBox()
        self._prep_combo(self._cmb_original_audio)
        self._cmb_original_audio.addItem("压低其他应用", "duck")
        self._cmb_original_audio.addItem("静音其他应用", "mute")
        self._cmb_original_audio.addItem("不处理（混听）", "mix")
        self._spn_duck_gain = QDoubleSpinBox()
        self._spn_duck_gain.setRange(0.0, 1.0)
        self._spn_duck_gain.setSingleStep(0.05)
        self._spn_duck_gain.setDecimals(2)
        self._spn_duck_gain.setPrefix("音量 ")
        self._spn_duck_gain.setToolTip("压低模式下其他应用的音量（0 = 近乎静音，1 = 不压）")
        mixer = "Windows 音量合成器" if is_windows else "PulseAudio / PipeWire（pactl）"
        self._cmb_original_audio.setToolTip(
            f"播放译文语音时，按应用压低或静音其他声音（{mixer}），播完自动恢复。\n"
            "需开启麦克风/游戏「语音输出」才生效。"
        )
        if not is_windows and shutil.which("pactl") is None:
            self._cmb_original_audio.setToolTip("未找到 pactl，Linux 上的原文闪避不可用")
            self._cmb_original_audio.setEnabled(False)
            self._spn_duck_gain.setEnabled(False)
        self._form_row(layout, "原文闪避", self._cmb_original_audio, self._spn_duck_gain)

        self._cmb_capture_backend = QComboBox()
        self._prep_combo(self._cmb_capture_backend)
        if is_windows:
            self._cmb_capture_backend.addItem("自动（WASAPI Loopback）", "auto")
            self._cmb_capture_backend.addItem("WASAPI Loopback", "loopback")
            self._cmb_capture_backend.setToolTip(
                "捕获扬声器/耳机的全部声音。为避免译文回灌，\n"
                "请把 TTS/音乐输出到 CABLE Input 或另一设备。"
            )
        else:
            self._cmb_capture_backend.addItem("自动（PulseAudio / PipeWire monitor）", "auto")
            self._cmb_capture_backend.addItem("系统 monitor", "loopback")
            self._cmb_capture_backend.setToolTip(
                "捕获输出设备的 monitor 源。为避免译文回灌，请把 TTS/音乐输出到虚拟声卡。"
            )
        self._form_row(layout, "游戏捕获", self._cmb_capture_backend)

        layout = self._settings_card(page, "记录与词表")
        self._chk_transcript_logging = QCheckBox("保存翻译记录")
        self._chk_transcript_logging.setToolTip("关闭后不再将原文和译文写入本地文件")
        self._spn_transcript_retention = QSpinBox()
        self._spn_transcript_retention.setRange(1, 3650)
        self._spn_transcript_retention.setPrefix("保留 ")
        self._spn_transcript_retention.setSuffix(" 天")
        self._chk_transcript_logging.toggled.connect(self._spn_transcript_retention.setEnabled)
        archive_row = QHBoxLayout()
        archive_row.addWidget(self._chk_transcript_logging)
        archive_row.addWidget(self._spn_transcript_retention)
        archive_row.addStretch(1)
        layout.addLayout(archive_row)
        self._btn_corpus = QPushButton("热词/术语")
        self._btn_corpus.setObjectName("ghostButton")
        self._btn_corpus.clicked.connect(self._on_edit_corpus)
        self._btn_vb_hint = QPushButton("检测虚拟声卡")
        self._btn_vb_hint.setObjectName("ghostButton")
        self._btn_vb_hint.setToolTip("一键检测 VB-Cable / 虚拟声卡链路")
        self._btn_vb_hint.clicked.connect(self._on_device_guide)
        self._lbl_corpus = QLabel("热词 0 / 术语 0")
        self._lbl_corpus.setObjectName("fieldLabel")
        corpus_row = QHBoxLayout()
        corpus_row.addWidget(self._btn_corpus)
        corpus_row.addWidget(self._btn_vb_hint)
        corpus_row.addWidget(self._lbl_corpus)
        corpus_row.addStretch(1)
        layout.addLayout(corpus_row)

        self._btn_stop = QPushButton("停止全部")
        self._btn_stop.setObjectName("dangerButton")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)
        page.addWidget(self._btn_stop)

    # ── 云端服务 ─────────────────────────────────────────────────────────
    def _build_settings_cloud(self, page: QVBoxLayout) -> None:
        tip = QLabel(
            "以下服务需要联网，密钥只保存在系统钥匙串。右侧标签显示当前组合是否用到该服务。"
        )
        tip.setObjectName("fieldLabel")
        tip.setWordWrap(True)
        page.addWidget(tip)
        self._tts_voice_combos: dict[str, QComboBox] = {}

        layout = self._settings_card(
            page,
            "火山引擎 · 豆包语音",
            kind="cloud",
            usage_key="volc",
            desc="用于：火山同传、豆包极速识别、豆包语音合成。新版控制台填 API Key 即可；"
            "旧版填数字 AppID + Access Token。",
        )
        self._txt_volc_key = self._secret_edit("API Key（或旧版 AppID）")
        layout.addWidget(self._txt_volc_key)
        self._txt_volc_token = self._secret_edit("Access Token（旧版 AppID 鉴权时填写）")
        layout.addWidget(self._txt_volc_token)

        self._sub_title(layout, "火山同传", "volc_ast")
        self._cmb_volc_voice = QComboBox()
        self._prep_combo(self._cmb_volc_voice)
        self._cmb_volc_voice.setEditable(True)
        self._cmb_volc_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for sid, label in VOLC_VOICE_OPTIONS:
            self._cmb_volc_voice.addItem(label, sid)
        self._cmb_volc_voice.editTextChanged.connect(self._update_save_button)
        self._cmb_volc_voice.setToolTip(
            "开启「语音输出」后生效。\n"
            "原音色：复刻说话人声音（默认）\n"
            "公版音色：仅目标语为中文/英文时可用，也可直接粘贴音色 ID"
        )
        self._form_row(layout, "同传音色", self._cmb_volc_voice)
        self._spn_speech_rate = QSpinBox()
        self._spn_speech_rate.setRange(-50, 100)
        self._spn_speech_rate.setSingleStep(10)
        self._spn_speech_rate.setToolTip(
            "火山同传语速：-50≈0.5x，0=正常，100≈2.0x。只影响合成语音快慢，不改变识别准确度。"
        )
        self._spn_rotate = QSpinBox()
        self._spn_rotate.setRange(0, 120)
        self._spn_rotate.setSuffix(" 分钟")
        self._spn_rotate.setToolTip("定期重建火山会话；0=关闭。默认 12 分钟。")
        row = self._form_row(layout, "同传语速", self._spn_speech_rate)
        row.addWidget(self._field_label("会话轮转"))
        row.addWidget(self._spn_rotate)
        row.addStretch(1)

        self._sub_title(layout, "豆包语音合成（经济模式）", "doubao_tts")
        doubao_tip = QLabel(
            "小智同款音色「湾湾小何」等；需在火山控制台开通「语音合成大模型」并购买对应音色。"
        )
        doubao_tip.setObjectName("fieldLabel")
        doubao_tip.setWordWrap(True)
        layout.addWidget(doubao_tip)
        self._tts_voice_rows(layout, "doubao")

        self._sub_title(layout, "资源包额度（可选）")
        self._txt_volc_iam_ak = self._secret_edit("IAM AccessKey（与语音 API Key 不同）")
        self._txt_volc_iam_sk = self._secret_edit("IAM SecretKey")
        iam_row = QHBoxLayout()
        iam_row.addWidget(self._txt_volc_iam_ak, 1)
        iam_row.addWidget(self._txt_volc_iam_sk, 1)
        layout.addLayout(iam_row)
        self._txt_volc_console_app = QLineEdit()
        self._txt_volc_console_app.setPlaceholderText("控制台 AppID（可选，数字）")
        self._lbl_pack_remain = QLabel(self._pack_remain_text)
        self._lbl_pack_remain.setObjectName("fieldLabel")
        self._lbl_pack_remain.setWordWrap(True)
        btn_refresh_pack = QPushButton("刷新额度")
        btn_refresh_pack.setObjectName("ghostButton")
        btn_refresh_pack.clicked.connect(self._on_refresh_pack_quota)
        pack_row = QHBoxLayout()
        pack_row.addWidget(self._txt_volc_console_app, 1)
        pack_row.addWidget(btn_refresh_pack)
        layout.addLayout(pack_row)
        layout.addWidget(self._lbl_pack_remain)

        layout = self._settings_card(
            page,
            "阿里云百炼 · DashScope",
            kind="cloud",
            usage_key="dashscope",
            desc="用于：Fun-ASR / Qwen3-ASR-Flash 识别、CosyVoice 语音合成。"
            "也可设置环境变量 DASHSCOPE_API_KEY。",
        )
        self._txt_dashscope_key = self._secret_edit("DashScope API Key")
        layout.addWidget(self._txt_dashscope_key)
        self._sub_title(layout, "CosyVoice 语音合成（经济模式）", "cosyvoice")
        self._txt_cosyvoice_model = QLineEdit()
        self._txt_cosyvoice_model.setPlaceholderText("cosyvoice-v2")
        self._txt_cosyvoice_model.setToolTip("CosyVoice 模型名；v2 音色以 _v2 结尾")
        self._form_row(layout, "模型", self._txt_cosyvoice_model)
        self._tts_voice_rows(layout, "cosyvoice")

        layout = self._settings_card(
            page,
            "大模型翻译 · OpenAI 兼容",
            kind="cloud",
            usage_key="llm",
            desc="用于：翻译 = 大模型 LLM。DeepSeek / 通义 / 本机 Ollama / LM Studio 均可，"
            "失败时自动回退 NLLB。",
        )
        self._txt_llm_url = QLineEdit()
        self._txt_llm_url.setPlaceholderText(
            "接口地址，如 https://api.deepseek.com/v1 或 http://127.0.0.1:11434/v1"
        )
        layout.addWidget(self._txt_llm_url)
        self._txt_llm_model = QLineEdit()
        self._txt_llm_model.setPlaceholderText("模型名，如 deepseek-chat / qwen-plus / qwen3:8b")
        self._txt_llm_key = self._secret_edit("API Key（本地 Ollama 可不填）")
        llm_row = QHBoxLayout()
        llm_row.addWidget(self._txt_llm_model, 1)
        llm_row.addWidget(self._txt_llm_key, 1)
        layout.addLayout(llm_row)

        layout = self._settings_card(
            page,
            "Edge TTS · 微软",
            kind="cloud",
            usage_key="edge",
            desc="免费、无需账号（需联网）。小智服务端默认语音；其他云端语音失败时也回退到这里。",
        )
        self._tts_voice_rows(layout, "edge")

        layout = self._settings_card(
            page,
            "自建 / 第三方语音合成 · OpenAI 兼容",
            kind="cloud",
            usage_key="openai_tts",
            desc="对接 /v1/audio/speech：OpenAI、硅基流动 CosyVoice2、Kokoro-FastAPI，"
            "或本机 GPT-SoVITS / FishSpeech / IndexTTS 的兼容服务（小智 OpenAITTS / CustomTTS 同类）。",
        )
        self._txt_openai_tts_url = QLineEdit()
        self._txt_openai_tts_url.setPlaceholderText(
            "接口地址，如 https://api.openai.com/v1 或 http://127.0.0.1:8880/v1"
        )
        layout.addWidget(self._txt_openai_tts_url)
        self._txt_openai_tts_model = QLineEdit()
        self._txt_openai_tts_model.setPlaceholderText(
            "模型名，如 tts-1 / FunAudioLLM/CosyVoice2-0.5B"
        )
        self._txt_openai_tts_key = self._secret_edit("API Key（本机服务可不填）")
        openai_row = QHBoxLayout()
        openai_row.addWidget(self._txt_openai_tts_model, 1)
        openai_row.addWidget(self._txt_openai_tts_key, 1)
        layout.addLayout(openai_row)
        self._tts_voice_rows(layout, "openai")

    # ── 本地模型 ─────────────────────────────────────────────────────────
    def _build_settings_local(self, page: QVBoxLayout) -> None:
        from src.engines.pipeline.model_catalog import (
            ASR_OPTIONS,
            NLLB_OPTIONS,
            format_kokoro_info,
            format_option_label,
        )
        from src.utils.resource_paths import default_model_resource_root

        layout = self._settings_card(
            page,
            "模型存储与下载",
            kind="local",
            desc="本地模型离线运行，首次使用时自动下载。每个文件都按固定的 SHA-256 校验，"
            "某个下载源连不上或内容不符时自动换下一个。"
            "识别和翻译模型更改后点「保存」生效（运行中会停止通道）。",
        )
        self._txt_model_dir = QLineEdit()
        self._txt_model_dir.setPlaceholderText(f"默认：{default_model_resource_root()}")
        self._txt_model_dir.setToolTip(
            "模型下载和读取的位置，留空使用默认目录。\n"
            "可以选空间大的磁盘；改目录后已下载的模型不会自动搬过去，"
            "可把旧目录里的 asr / nllb / mt / tts 文件夹整体复制过去，免得重新下载。"
        )
        browse = QPushButton("选择…")
        browse.setObjectName("ghostButton")
        browse.clicked.connect(self._on_browse_model_dir)
        reset = QPushButton("默认")
        reset.setObjectName("ghostButton")
        reset.setToolTip("清空，恢复默认目录")
        reset.clicked.connect(self._txt_model_dir.clear)
        self._form_row(layout, "模型目录", self._txt_model_dir, browse, reset)
        self._cmb_model_mirror = QComboBox()
        self._prep_combo(self._cmb_model_mirror)
        self._cmb_model_mirror.addItem("国内优先：ModelScope 魔搭 / 国内加速", "china")
        self._cmb_model_mirror.addItem("海外优先：Hugging Face / GitHub", "global")
        self._cmb_model_mirror.setToolTip(
            "决定先试哪个下载源，其余的仍作为备用自动尝试。\n"
            "国内网络选「国内优先」；能直连 Hugging Face / GitHub 时选「海外优先」更快。\n"
            "NLLB 翻译和英文 Zipformer 没有国内源，只能从 Hugging Face 下载。"
        )
        self._form_row(layout, "下载源", self._cmb_model_mirror)

        self._download_card = self._card()
        self._download_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        dl_layout = QVBoxLayout(self._download_card)
        dl_layout.setContentsMargins(16, 14, 16, 14)
        dl_layout.setSpacing(8)
        dl_title = QLabel("正在下载")
        dl_title.setObjectName("sectionTitle")
        dl_layout.addWidget(dl_title)
        self._download_list = QVBoxLayout()
        self._download_list.setSpacing(8)
        dl_layout.addLayout(self._download_list)
        self._download_card.setVisible(False)
        page.addWidget(self._download_card)

        layout = self._settings_card(
            page,
            "语音识别模型",
            kind="local",
            usage_key="local_asr",
            desc="用于：语音识别 = 离线模型。两个通道选同一模型时共用一份内存。",
        )
        self._asr_model_combos: dict[str, QComboBox] = {}
        for field, label_text, tip_text in (
            (
                "economy_asr_mic_model",
                "麦克风",
                "我说话用的模型。近场单人说中文推荐 SenseVoice（小智服务端同款）。",
            ),
            (
                "economy_asr_game_model",
                "游戏/视频",
                "游戏/视频声音用的模型。英文对白混 BGM 推荐「智能路由」或 Whisper。",
            ),
        ):
            combo = QComboBox()
            self._prep_combo(combo)
            for opt in ASR_OPTIONS:
                if opt.id != "windows-live-captions":
                    combo.addItem(format_option_label(opt), opt.id)
            if not _module_available("torch"):
                for qwen_id in QWEN_ASR_MODELS:
                    self._disable_combo_item(combo, qwen_id, _missing_runtime("PyTorch"))
            combo.setToolTip(tip_text)
            self._form_row(layout, label_text, combo)
            self._model_source_row(layout, functools.partial(self._asr_combo_source, combo))
            combo.currentIndexChanged.connect(self._refresh_model_sources)
            self._asr_model_combos[field] = combo
        self._cmb_mic_asr_model = self._asr_model_combos["economy_asr_mic_model"]
        self._cmb_game_asr_model = self._asr_model_combos["economy_asr_game_model"]
        self._field_hint(layout, "语音活动检测（所有本地识别共用）")
        self._model_source_row(layout, silero_source)

        layout = self._settings_card(
            page,
            "翻译模型",
            kind="local",
            usage_key="local_mt",
            desc="用于：翻译 = NLLB / 自动，以及大模型翻译失败时的回退；"
            "翻译 = Hy-MT2 时使用下方所选型号，首次使用时下载。",
        )
        self._cmb_nllb_model = QComboBox()
        self._prep_combo(self._cmb_nllb_model)
        for opt in NLLB_OPTIONS:
            self._cmb_nllb_model.addItem(format_option_label(opt), opt.id)
        self._cmb_nllb_model.setToolTip("本地 NLLB CTranslate2 模型，缓存至 resource/nllb/")
        self._form_row(layout, "NLLB", self._cmb_nllb_model)
        self._model_source_row(
            layout, lambda: nllb_source(str(self._cmb_nllb_model.currentData() or ""))
        )
        self._cmb_nllb_model.currentIndexChanged.connect(self._refresh_model_sources)
        self._cmb_hymt_model = QComboBox()
        self._prep_combo(self._cmb_hymt_model)
        for hy in HYMT_MODELS:
            self._cmb_hymt_model.addItem(f"{hy.label} · {hy.size_mb / 1024:.1f} GB", hy.id)
            self._cmb_hymt_model.setItemData(
                self._cmb_hymt_model.count() - 1, hy.note, Qt.ItemDataRole.ToolTipRole
            )
        self._cmb_hymt_model.setToolTip(
            "翻译选 Hy-MT2 时使用；需 pip install llama-cpp-python，首次使用时下载"
        )
        self._form_row(layout, "Hy-MT2", self._cmb_hymt_model)
        self._model_source_row(
            layout, lambda: hymt_source(str(self._cmb_hymt_model.currentData() or ""))
        )
        self._cmb_hymt_model.currentIndexChanged.connect(self._refresh_model_sources)

        layout = self._settings_card(
            page,
            "Kokoro 语音合成",
            kind="local",
            usage_key="kokoro",
            desc=format_kokoro_info()
            + "。中文音色需 v1.1-zh 模型（自动下载），未就绪时用 v1.0 的晓晓。",
        )
        self._tts_voice_rows(layout, "kokoro")
        self._model_source_row(layout, kokoro_source)

        layout = self._settings_card(
            page,
            "MeloTTS 语音合成",
            kind="local",
            usage_key="melo",
            desc="sherpa-onnx VITS 单音色模型，首次使用下载约 170MB；"
            "中英夹杂的句子读得更顺，适合游戏术语多的场景。",
        )
        melo_preview = QPushButton("试听")
        melo_preview.setObjectName("ghostButton")
        melo_preview.clicked.connect(lambda: self._on_preview_tts("melo"))
        melo_row = QHBoxLayout()
        melo_row.addStretch(1)
        melo_row.addWidget(melo_preview)
        layout.addLayout(melo_row)
        self._model_source_row(layout, melo_source)

        from src.utils.device_utils import detect_cuda_available, get_device_options

        layout = self._settings_card(
            page,
            "推理设备",
            kind="local",
            usage_key="device",
            desc="本地识别 / 翻译 / 语音模型使用的设备。",
        )
        self._cmb_device_pref = QComboBox()
        self._prep_combo(self._cmb_device_pref)
        for label, value in get_device_options():
            self._cmb_device_pref.addItem(label, value)
        if detect_cuda_available():
            self._cmb_device_pref.setToolTip(
                "自动：有 NVIDIA GPU 时用 GPU，否则 CPU；\n"
                "CPU：强制 CPU（兼容性最好）；\n"
                "GPU (CUDA)：强制 GPU（需 NVIDIA 显卡 + CUDA onnxruntime）。"
            )
        else:
            self._cmb_device_pref.setToolTip(
                "当前未检测到 NVIDIA GPU 或 CUDA onnxruntime。\n"
                "安装 onnxruntime-gpu 和 NVIDIA 驱动后可选择 GPU。"
            )
        self._form_row(layout, "默认设备", self._cmb_device_pref)
        task_row = QHBoxLayout()
        self._task_device_combos: dict[str, QComboBox] = {}
        for task, label in (("asr", "识别"), ("mt", "翻译"), ("tts", "语音")):
            combo = QComboBox()
            self._prep_combo(combo)
            combo.addItem(f"{label}：跟随", "")
            combo.addItem(f"{label}：CPU", "cpu")
            combo.addItem(f"{label}：GPU", "cuda")
            combo.setToolTip(f"{label}模型单独指定推理设备；「跟随」使用默认设备")
            task_row.addWidget(combo, 1)
            self._task_device_combos[task] = combo
        self._cmb_asr_device = self._task_device_combos["asr"]
        self._cmb_mt_device = self._task_device_combos["mt"]
        self._cmb_tts_device = self._task_device_combos["tts"]
        layout.addLayout(task_row)

    # ── 助手 ────────────────────────────────────────────────────────────
    def _build_settings_assistant(self, page: QVBoxLayout) -> None:
        layout = self._settings_card(
            page,
            "Dota 助手（语音教练）",
            desc="用法：先开麦克风通道 → Ctrl+Alt+K 待命 → 说话等定稿 → 自动发送；再按一次取消。",
        )
        self._chk_dota_coach = QCheckBox("启用")
        self._chk_dota_coach.setChecked(True)
        self._chk_dota_coach.setToolTip(
            "快捷键待命后，把麦克风定稿原文发到本机 Dota Tracker /ai/ask"
        )
        self._cmb_dota_mode = QComboBox()
        self._prep_combo(self._cmb_dota_mode)
        self._cmb_dota_mode.addItem("普通", "normal")
        self._cmb_dota_mode.addItem("加速", "turbo")
        self._cmb_dota_mode.setToolTip("影响 AI 时间节奏建议")
        coach_row = QHBoxLayout()
        coach_row.addWidget(self._chk_dota_coach)
        coach_row.addWidget(self._cmb_dota_mode)
        coach_row.addStretch(1)
        layout.addLayout(coach_row)
        self._txt_dota_url = QLineEdit()
        self._txt_dota_url.setPlaceholderText(DEFAULT_COACH_URL)
        self._txt_dota_url.setToolTip("Dota Tracker 的 /ai/ask 地址")
        layout.addWidget(self._txt_dota_url)

    def _refresh_settings_usage(self, *_args: object) -> None:
        """Mark which cloud/local cards the current engine combination uses."""
        if not hasattr(self, "_cmb_tts_backend") or not hasattr(self, "_usage_pills"):
            return
        from src.engines.pipeline.tts_catalog import tts_engine

        volc = (self._cmb_translation_mode.currentData() or "volc") == "volc"
        eco = not volc
        asr = self._cmb_asr_backend.currentData() or ""
        mt = self._cmb_mt_backend.currentData() or ""
        tts = self._cmb_tts_backend.currentData() or ""
        local_asr = eco and asr == "local"
        local_mt = eco and mt in ("nllb", "auto", "llm")
        local_tts = eco and tts in ("kokoro", "melo", "auto")
        usage = {
            "volc": volc or (eco and (asr == "doubao_flash" or tts == "doubao")),
            "volc_ast": volc,
            "doubao_tts": eco and tts == "doubao",
            "dashscope": eco and (asr in ("dashscope", "qwen_flash") or tts == "cosyvoice"),
            "cosyvoice": eco and tts == "cosyvoice",
            "llm": eco and mt == "llm",
            "edge": eco and tts == "edge",
            "openai_tts": eco and tts == "openai",
            "local_asr": local_asr,
            "local_mt": local_mt or (eco and mt == "hymt"),
            "kokoro": eco and tts in ("kokoro", "auto"),
            "melo": eco and tts == "melo",
            "device": local_asr or local_mt or local_tts or (eco and mt == "hymt"),
        }
        for key, pill in self._usage_pills.items():
            on = usage.get(key, False)
            name = "usageOn" if on else "usageOff"
            pill.setText("● 使用中" if on else "未使用")
            if pill.objectName() != name:
                pill.setObjectName(name)
                style = pill.style()
                if style is not None:
                    style.unpolish(pill)
                    style.polish(pill)

        self._economy_rows.setVisible(eco)
        info = tts_engine(tts)
        asr_where = "本地模型" if asr in ("local", "live_captions") else "云端服务"
        mt_where = "本地模型" if mt in ("nllb", "hymt", "auto") else "云端服务"
        tts_where = {"local": "本地模型", "cloud": "云端服务", "auto": "本地模型"}[info.kind]
        self._lbl_engine_hint.setText(
            f"识别 → 「{asr_where}」页　翻译 → 「{mt_where}」页　语音 → 「{tts_where}」页"
            f"（{info.needs}）"
        )

    def _build_log_panel(self) -> QFrame:
        card = self._card()
        card.setObjectName("logSidePanel")
        card.setMinimumWidth(260)
        card.setMaximumWidth(320)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        log_header = QHBoxLayout()
        log_title = QLabel("运行状态")
        log_title.setObjectName("sectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("accentButton")
        btn_clear.setToolTip("清空状态日志")
        btn_clear.clicked.connect(self._on_clear_log)
        log_header.addWidget(btn_clear)
        layout.addLayout(log_header)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        self._metric_latency = self._metric_tile(metrics, "端到端延迟", "—", "metricValue")
        self._lbl_resource = self._metric_tile(metrics, "资源占用", "CPU 0%\nRAM 0.0GB")
        layout.addLayout(metrics)

        self._side_status = QLabel("空闲")
        self._side_status.setObjectName("fieldLabel")
        self._side_status.setWordWrap(True)
        layout.addWidget(self._side_status)

        self._log_view = QTextEdit()
        # Long gaming sessions log continuously; keep memory and append cost bounded.
        log_doc = self._log_view.document()
        if log_doc is not None:
            log_doc.setMaximumBlockCount(2000)
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(160)
        layout.addWidget(self._log_view, 1)
        return card

    def _metric_tile(
        self, row: QHBoxLayout, title: str, value: str, value_style: str = "metricSmall"
    ) -> QLabel:
        tile = QFrame()
        tile.setObjectName("metricTile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        caption = QLabel(title)
        caption.setObjectName("statusLabel")
        lay.addWidget(caption)
        label = QLabel(value)
        label.setObjectName(value_style)
        label.setWordWrap(True)
        lay.addWidget(label)
        row.addWidget(tile, 1)
        return label

    def _set_latency(self, latency_ms: int | None) -> None:
        if latency_ms is None:
            self._latency_label.setText("延迟：—")
            self._metric_latency.setText("—")
            return
        self._latency_label.setText(f"延迟：{latency_ms} ms")
        self._metric_latency.setText(f"{latency_ms} ms")

    def _on_clear_log(self) -> None:
        self._log_view.clear()
        self._append_log("日志已清空")

    def _card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return frame

    @staticmethod
    def _disable_combo_item(combo: QComboBox, value: str, reason: str) -> None:
        """Grey out an option whose runtime is missing, saying why instead of failing later."""
        index = combo.findData(value)
        if index < 0:
            return
        model = combo.model()
        item = model.item(index) if isinstance(model, QStandardItemModel) else None
        if item is not None:
            item.setEnabled(False)
        combo.setItemText(index, f"{combo.itemText(index)}（{reason}）")
        combo.setItemData(index, reason, Qt.ItemDataRole.ToolTipRole)

    @staticmethod
    def _prep_combo(combo: QComboBox) -> None:
        """Make combos shrink gracefully instead of crushing sibling labels."""
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _preview_box(self, placeholder: str) -> QFrame:
        box = QFrame()
        box.setObjectName("previewBox")
        box.setMinimumHeight(140)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        label = QLabel(placeholder)
        label.setObjectName("previewText")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(label)
        box._text_label = label  # type: ignore[attr-defined]
        return box

    def _set_preview(self, box: QFrame, original: str, translated: str) -> None:
        label: QLabel = box._text_label  # type: ignore[attr-defined]
        label.setText(f"{original}\n\n→ {translated}")

    def _fill_lang_combo(self, combo: QComboBox) -> None:
        for label, code in _LANG_LABELS:
            combo.addItem(label, code)

    def _apply_theme(self) -> None:
        self._active_theme = resolve_theme(self._theme)
        self.setStyleSheet(get_stylesheet(self._active_theme))
        self._cmb_theme.blockSignals(True)
        self._select_combo_data(self._cmb_theme, self._theme)
        self._cmb_theme.blockSignals(False)
        if getattr(self, "_btn_text_color", None) is not None:
            self._refresh_color_buttons()
            for overlay in self._overlay_windows():
                overlay.set_appearance(**self._saved_overlay_appearance())
                overlay.set_menu_style(menu_stylesheet(self._active_theme))

    def _on_theme_selected(self, _index: int) -> None:
        self._update_save_button()

    def _on_system_scheme_changed(self, *_args: object) -> None:
        if self._theme == "system":
            self._apply_theme()

    def _show_page(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        self._current_tab_index = index
        title, subtitle = _PAGE_TITLES[index]
        self._page_title.setText(title)
        self._page_subtitle.setText(subtitle)
        if index == _PAGE_TRANSCRIPTS:
            self._transcript_panel.set_log_dir(resolve_log_dir(self._config.log_dir))
        elif index == _PAGE_TYPED and not self._typed_languages_set:
            # Same direction as the mic channel (you type what you would say).
            self._typed_languages_set = True
            self._typed_panel.set_languages(
                self._cmb_mic_source.currentData() or "zh",
                self._cmb_mic_target.currentData() or "en",
            )
        button = self._nav_group_buttons.button(index)
        if button is not None:
            button.setChecked(True)

    def _on_status_panel_toggled(self, visible: bool) -> None:
        self._log_panel.setVisible(visible)
        if self._config.status_panel_visible != visible:
            self._config = self._config.model_copy(update={"status_panel_visible": visible})
            self._persist_config()

    def _append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_view.append(f"[{ts}] {message}")
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
        side = getattr(self, "_side_status", None)
        if side is not None:
            # Keep latest line visible above the scroll log
            short = message if len(message) <= 80 else message[:77] + "…"
            side.setText(short)

    @staticmethod
    def _is_preferred_virtual_output(name: str) -> bool:
        """True for VB-Cable / Voicemeeter virtual inputs that the app's TTS
        should feed into so teammates hear the translation.

        Covers:
          - CABLE Input (classic VB-Cable)
          - Voicemeeter Input (VAIO main input)
          - Voicemeeter AUX Input / VAIO3 Input / Voicemeeter In N
        """
        t = (name or "").lower()
        # Classic Cable + any "VB-Audio ... Input"
        if "cable" in t and "input" in t:
            return True
        if "voicemeeter" in t and ("vaio" in t or "aux" in t) and "input" in t:
            return True
        # Voicemeeter In 1/2/3/4/5
        return "voicemeeter in " in t

    def _populate_devices(self) -> None:
        from src.gui.device_labels import format_device_label, role_default_label, role_hint

        try:
            inputs = AudioCapture.list_input_devices()
        except Exception as exc:
            logger.warning(f"列举输入设备失败: {exc}")
            inputs = []
        try:
            outputs = AudioPlayer.list_devices()
        except Exception as exc:
            logger.warning(f"列举输出设备失败: {exc}")
            outputs = []

        # Soundcard / WASAPI-process-exclude 等附加设备（Loopback 捕获用）
        extra_capture: list[dict[str, Any]] = []
        try:
            from src.audio.stream import list_audio_devices

            devices = list_audio_devices()
            extra_capture = list(devices.get("input", []))
        except Exception as exc:
            logger.debug(f"补充音频设备失败: {exc}")

        # ---- 我的麦克风：真实输入，排除纯回采 / 虚拟回环 ----
        self._cmb_input.clear()
        self._cmb_input.addItem(role_default_label("mic"), None)
        seen_mic: set[Any] = set()
        for dev in inputs:
            idx = dev["index"]
            name = str(dev["name"])
            if idx in seen_mic:
                continue
            if is_virtual_or_loopback_input(name, idx):
                continue
            seen_mic.add(idx)
            label = format_device_label("mic", name, idx)
            self._add_device_item(self._cmb_input, label, idx, name)
        self._cmb_input.setToolTip(role_hint("mic") + "\n" + vb_cable_setup_hint())

        # ---- 译文播放到 ----
        self._cmb_output.clear()
        self._cmb_output.addItem(role_default_label("output"), None)
        seen_out: set[Any] = set()
        preferred_out: list[tuple[Any, str]] = []
        other_out: list[tuple[Any, str]] = []
        out_names: dict[str, str] = {}
        for dev in outputs:
            idx = dev["index"]
            if idx in seen_out:
                continue
            seen_out.add(idx)
            name = str(dev["name"])
            out_names[str(idx)] = name
            label = format_device_label("output", name, idx)
            if self._is_preferred_virtual_output(name):
                preferred_out.append((idx, label))
            else:
                other_out.append((idx, label))
        for idx, label in preferred_out + other_out:
            self._add_device_item(self._cmb_output, label, idx, out_names.get(str(idx), ""))
        self._cmb_output.setToolTip(
            role_hint("output")
            + "\n队友听翻译：选 CABLE Input 或 Voicemeeter Input（需安装虚拟声卡）"
        )
        # 首次未选输出：优先 CABLE / Voicemeeter Input
        if self._config.output_device is None:
            if preferred_out:
                self._select_combo_data(self._cmb_output, preferred_out[0][0])
            else:
                vb = find_preferred_vb_output(
                    [{"name": label, "index": idx} for idx, label in other_out]
                )
                if vb is not None:
                    self._select_combo_data(self._cmb_output, vb)

        # ---- 游戏声音捕获：优先真实 loopback，虚拟线缆放最后（或高级才见） ----
        self._cmb_loopback.clear()
        self._cmb_loopback.addItem(role_default_label("loopback"), None)
        preferred: list[tuple[Any, str]] = []
        vb_loop: list[tuple[Any, str]] = []
        others: list[tuple[Any, str]] = []
        lb_names: dict[str, str] = {}
        seen_lb: set[Any] = set()
        advanced = bool(getattr(self, "_chk_advanced", None) and self._chk_advanced.isChecked())

        # 先放额外的 WASAPI / process-exclude 捕获源（字符串 id，不走 seen_lb 数字集）
        for dev in extra_capture:
            idx = dev.get("id")
            name = str(dev.get("name", ""))
            # 额外列表里可能有重复的输入设备；额外设备主要贡献是 wasapi_* 的 id
            if idx in seen_lb:
                continue
            # sounddevice 查出来的数字设备重复
            if (
                isinstance(idx, int)
                and not str(idx).startswith("wasapi_")
                and idx in {d.get("index") for d in inputs}
            ):
                continue
            seen_lb.add(idx)
            lb_names[str(idx)] = name
            label = format_device_label("loopback", name, idx)
            if is_vb_cable_capture(name, idx) or is_vb_cable_input(name, idx):
                vb_loop.append((idx, label))
            elif (
                "loopback" in f"{name} {idx}".lower()
                or ".monitor" in f"{name} {idx}".lower()
                or "wasapi_loopback:" in f"{name} {idx}".lower()
                or "wasapi_proc_exclude:" in f"{name} {idx}".lower()
            ):
                preferred.append((idx, label))
            else:
                others.append((idx, label))

        for dev in inputs:
            idx = dev["index"]
            name = str(dev["name"])
            if idx in seen_lb:
                continue
            seen_lb.add(idx)
            lb_names[str(idx)] = name
            label = format_device_label("loopback", name, idx)
            if is_vb_cable_capture(name, idx) or is_vb_cable_input(name, idx):
                vb_loop.append((idx, label))
            else:
                others.append((idx, label))

        saved_lb = (str(self._config.loopback_device), self._config.loopback_device_name)
        # 虚拟线缆捕获仅高级模式列出，避免误选；已保存的那一项始终保留，否则会显示为"未连接"
        shown_vb = [
            (idx, label)
            for idx, label in vb_loop
            if advanced
            or str(idx) == saved_lb[0]
            or (saved_lb[1] and lb_names.get(str(idx)) == saved_lb[1])
        ]
        for idx, label in preferred + others + shown_vb:
            self._add_device_item(self._cmb_loopback, label, idx, lb_names.get(str(idx), ""))
        self._cmb_loopback.setToolTip(role_hint("loopback"))

        # 未配置过捕获源时：自动选系统 Loopback（避开虚拟线）
        if self._config.loopback_device is None:
            auto_lb = find_preferred_system_loopback(
                [{"name": label, "index": idx} for idx, label in preferred]
            )
            if auto_lb is not None:
                self._select_combo_data(self._cmb_loopback, auto_lb)

        self._device_cache_inputs: list[dict[str, Any]] = []
        self._device_cache_outputs: list[dict[str, Any]] = []
        for dev in inputs:
            self._device_cache_inputs.append(
                {"name": str(dev.get("name", "")), "index": dev.get("index")}
            )
        for dev in extra_capture:
            self._device_cache_inputs.append(
                {"name": str(dev.get("name", "")), "index": dev.get("id")}
            )
        for idx, label in preferred_out + other_out:
            self._device_cache_outputs.append({"name": label, "index": idx})
        # also keep raw output names for detection
        for dev in outputs:
            self._device_cache_outputs.append(
                {"name": str(dev.get("name", "")), "index": dev.get("index")}
            )

    # -------------------------------------------------------------- config
    _DEVICE_NAME_ROLE = Qt.ItemDataRole.UserRole + 1
    _DEVICE_MISSING_ROLE = Qt.ItemDataRole.UserRole + 2

    def _add_device_item(self, combo: QComboBox, label: str, idx: Any, name: str) -> None:
        combo.addItem(label, idx)
        combo.setItemData(combo.count() - 1, name, self._DEVICE_NAME_ROLE)

    def _select_device(self, combo: QComboBox, idx: Any, name: str) -> None:
        """Pick the saved device by name first; keep a placeholder if it is unplugged."""
        if idx is None and not name:
            combo.setCurrentIndex(0)
            return
        if name:
            # One endpoint can be listed by several sources under the same name.
            same_name = [
                i for i in range(combo.count()) if combo.itemData(i, self._DEVICE_NAME_ROLE) == name
            ]
            exact = [i for i in same_name if str(combo.itemData(i)) == str(idx)]
            if exact or same_name:
                combo.setCurrentIndex((exact or same_name)[0])
                return
        for i in range(combo.count()):
            if str(combo.itemData(i)) == str(idx):
                item_name = combo.itemData(i, self._DEVICE_NAME_ROLE) or ""
                if not name or item_name == name:
                    combo.setCurrentIndex(i)
                    return
        # Saved device is not connected: keep the setting instead of silently erasing it.
        combo.addItem(f"⚠ 未连接：{name or idx}", idx)
        last = combo.count() - 1
        combo.setItemData(last, name, self._DEVICE_NAME_ROLE)
        combo.setItemData(last, True, self._DEVICE_MISSING_ROLE)
        combo.setCurrentIndex(last)

    def _combo_device_name(self, combo: QComboBox) -> str:
        if combo.currentData() is None:
            return ""
        return str(combo.currentData(self._DEVICE_NAME_ROLE) or "")

    def _missing_devices(self, channel: Literal["mic", "game"]) -> list[str]:
        combos = [self._cmb_input] if channel == "mic" else [self._cmb_loopback]
        if (channel == "mic" and self._chk_play_mic.isChecked()) or (
            channel == "game" and self._chk_play_game.isChecked()
        ):
            combos.append(self._cmb_output)
        return [
            self._combo_device_name(c) or str(c.currentData())
            for c in combos
            if c.currentData(self._DEVICE_MISSING_ROLE)
        ]

    def _select_combo_data(self, combo: QComboBox, value: Any) -> None:
        if value is None:
            combo.setCurrentIndex(0)
            return
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        for i in range(combo.count()):
            if str(combo.itemData(i)) == str(value):
                combo.setCurrentIndex(i)
                return

    def _select_lang(self, combo: QComboBox, code: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == code:
                combo.setCurrentIndex(i)
                return

    def _sync_lang_pairs_from_mic(self) -> None:
        """Mic 我说/译成 ↔ Game 字幕语言/源语言（反向）。"""
        if getattr(self, "_syncing_langs", False):
            return
        self._syncing_langs = True
        try:
            src = self._cmb_mic_source.currentData()
            tgt = self._cmb_mic_target.currentData()
            self._select_lang(self._cmb_game_source, tgt)
            self._select_lang(self._cmb_game_target, src)
        finally:
            self._syncing_langs = False

    def _sync_lang_pairs_from_game(self) -> None:
        """Game 源语言/字幕语言 ↔ Mic 译成/我说。"""
        if getattr(self, "_syncing_langs", False):
            return
        self._syncing_langs = True
        try:
            game_src = self._cmb_game_source.currentData()
            game_tgt = self._cmb_game_target.currentData()
            self._select_lang(self._cmb_mic_source, game_tgt)
            self._select_lang(self._cmb_mic_target, game_src)
        finally:
            self._syncing_langs = False

    def _sync_lang_pairs(self) -> None:
        self._sync_lang_pairs_from_mic()

    def _on_enable_toggled(self, *_args: Any) -> None:
        running_mic = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.OUTBOUND
        )
        running_game = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.INBOUND
        )
        mic_on = self._chk_enable_mic.isChecked()
        game_on = self._chk_enable_game.isChecked()

        # Config widgets editable only when that channel is idle
        for w in (
            self._cmb_input,
            self._cmb_output,
            self._cmb_mic_source,
            self._cmb_mic_target,
            self._chk_play_mic,
        ):
            w.setEnabled((not running_mic) and mic_on)
        for w in (
            self._cmb_loopback,
            self._cmb_game_source,
            self._cmb_game_target,
            self._chk_play_game,
        ):
            w.setEnabled((not running_game) and game_on)

        if self._config.subtitle_merged:
            # One shared overlay: either page's switch controls it.
            any_on = mic_on or running_mic or game_on or running_game
            self._chk_show_mic.setEnabled(any_on)
            self._chk_show_game.setEnabled(any_on)
        else:
            self._chk_show_mic.setEnabled(mic_on or running_mic)
            self._chk_show_game.setEnabled(game_on or running_game)
        # Channel buttons always clickable (start/stop that channel)
        self._btn_mic.setEnabled(mic_on or running_mic)
        self._btn_game.setEnabled(game_on or running_game)

    def _on_show_mic_toggled(self, checked: bool) -> None:
        self._link_show_toggle(self._chk_show_game, checked)
        self._on_show_overlay_toggled()

    def _on_show_game_toggled(self, checked: bool) -> None:
        self._link_show_toggle(self._chk_show_mic, checked)
        self._on_show_overlay_toggled()

    def _link_show_toggle(self, other: QCheckBox, checked: bool) -> None:
        if self._config.subtitle_merged and other.isChecked() != checked:
            other.blockSignals(True)
            other.setChecked(checked)
            other.blockSignals(False)

    def _sync_show_toggles(self) -> None:
        """Merged mode has one overlay, so the two switches become one."""
        merged = self._config.subtitle_merged
        label = "显示字幕浮层" if merged else "显示浮层"
        tip = (
            "合并字幕：两路共用一个浮层，这里和另一页的开关同步"
            if merged
            else "单独显示这一路的字幕浮层"
        )
        for box in (self._chk_show_mic, self._chk_show_game):
            box.setText(label)
            box.setToolTip(tip)
        if merged and self._chk_show_mic.isChecked() != self._chk_show_game.isChecked():
            self._chk_show_game.setChecked(True)  # links the mic switch too
        self._on_enable_toggled()

    def _on_show_overlay_toggled(self, *_args: Any) -> None:
        # Shown/hidden is runtime state (hotkeys flip it mid-game): save it at once.
        shown = {
            "show_mic_subtitle": self._chk_show_mic.isChecked(),
            "show_game_subtitle": self._chk_show_game.isChecked(),
        }
        if any(getattr(self._config, k) != v for k, v in shown.items()):
            self._config = self._config.model_copy(update=shown)
            self._persist_config()
        if self._pipeline is None:
            # Preview overlays only if already visible
            if self._overlays:
                self._sync_overlays_visibility(preview=True)
            return
        self._sync_overlays_visibility(preview=False)

    def _load_config_into_ui(self) -> None:
        cfg = self._config
        self._hotkeys_draft = None
        self._theme = cfg.theme_mode
        self._btn_status_panel.setChecked(cfg.status_panel_visible)
        self._log_panel.setVisible(cfg.status_panel_visible)

        self._select_lang(self._cmb_mic_source, cfg.source_language)
        self._select_lang(self._cmb_mic_target, cfg.target_language)
        self._select_lang(self._cmb_game_source, cfg.target_language)
        self._select_lang(self._cmb_game_target, cfg.source_language)

        self._chk_enable_mic.setChecked(cfg.enable_mic)
        self._chk_enable_game.setChecked(cfg.enable_game)
        for box, value in (
            (self._chk_show_mic, cfg.show_mic_subtitle),
            (self._chk_show_game, cfg.show_game_subtitle),
        ):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._sync_show_toggles()
        self._chk_play_mic.setChecked(cfg.play_mic_voice)
        self._chk_play_game.setChecked(cfg.play_game_voice)
        self._spn_font.setValue(cfg.subtitle_font_size)
        self._spn_opacity.setValue(round(cfg.subtitle_opacity * 100))
        self._subtitle_text_color = getattr(cfg, "subtitle_text_color", "#FFFFFF") or "#FFFFFF"
        self._subtitle_bg_color = getattr(cfg, "subtitle_bg_color", "#141418") or "#141418"
        self._chk_subtitle_follow_theme.blockSignals(True)
        self._chk_subtitle_follow_theme.setChecked(cfg.subtitle_follow_theme)
        self._chk_subtitle_follow_theme.blockSignals(False)
        self._apply_theme()
        self._select_combo_data(
            self._cmb_subtitle_font, getattr(cfg, "subtitle_font_family", "") or ""
        )
        self._cmb_translation_mode.blockSignals(True)
        self._select_combo_data(
            self._cmb_translation_mode,
            getattr(cfg, "translation_mode", "volc") or "volc",
        )
        self._cmb_translation_mode.blockSignals(False)
        self._txt_volc_key.setText(cfg.volc_api_key)
        if hasattr(self, "_txt_dashscope_key"):
            self._txt_dashscope_key.setText(getattr(cfg, "economy_dashscope_api_key", "") or "")
        if hasattr(self, "_cmb_asr_backend"):
            self._cmb_asr_backend.blockSignals(True)
            self._select_combo_data(self._cmb_asr_backend, cfg.economy_asr_backend)
            self._cmb_asr_backend.blockSignals(False)
        for field, combo in self._asr_model_combos.items():
            combo.blockSignals(True)
            self._select_combo_data(combo, getattr(cfg, field))
            combo.blockSignals(False)
        if hasattr(self, "_cmb_device_pref"):
            self._cmb_device_pref.blockSignals(True)
            self._select_combo_data(
                self._cmb_device_pref,
                getattr(cfg, "device_preference", "auto") or "auto",
            )
            self._cmb_device_pref.blockSignals(False)
        if hasattr(self, "_txt_model_dir"):
            self._txt_model_dir.setText(cfg.model_dir)
            self._select_combo_data(self._cmb_model_mirror, cfg.model_mirror)
        if hasattr(self, "_cmb_nllb_model"):
            self._cmb_nllb_model.blockSignals(True)
            self._select_combo_data(
                self._cmb_nllb_model,
                getattr(
                    cfg,
                    "economy_nllb_model",
                    "JustFrederik/nllb-200-distilled-600M-ct2-int8",
                )
                or "JustFrederik/nllb-200-distilled-600M-ct2-int8",
            )
            self._cmb_nllb_model.blockSignals(False)
        if hasattr(self, "_cmb_hymt_model"):
            self._cmb_hymt_model.blockSignals(True)
            self._select_combo_data(
                self._cmb_hymt_model, hymt_model(getattr(cfg, "economy_hymt_model", None)).id
            )
            self._cmb_hymt_model.blockSignals(False)
        if hasattr(self, "_cmb_mt_backend"):
            self._select_combo_data(
                self._cmb_mt_backend, getattr(cfg, "economy_mt_backend", "nllb") or "nllb"
            )
            self._txt_llm_url.setText(getattr(cfg, "economy_llm_base_url", "") or "")
            self._txt_llm_model.setText(getattr(cfg, "economy_llm_model", "") or "")
            self._txt_llm_key.setText(getattr(cfg, "economy_llm_api_key", "") or "")
        self._select_combo_data(self._cmb_tts_backend, cfg.economy_tts_backend)
        self._spn_tts_speed.setValue(cfg.economy_kokoro_speed)
        for field, combo in self._tts_voice_combos.items():
            self._set_voice_value(combo, getattr(cfg, field))
        self._txt_cosyvoice_model.setText(cfg.economy_cosyvoice_model)
        self._txt_openai_tts_url.setText(cfg.economy_openai_tts_url)
        self._txt_openai_tts_model.setText(cfg.economy_openai_tts_model)
        self._txt_openai_tts_key.setText(cfg.economy_openai_tts_api_key)
        for task, combo in getattr(self, "_task_device_combos", {}).items():
            self._select_combo_data(combo, getattr(cfg, f"economy_{task}_device", "") or "")
        self._txt_volc_token.setText(cfg.volc_access_token)
        self._txt_volc_iam_ak.setText(getattr(cfg, "volc_iam_ak", "") or "")
        self._txt_volc_iam_sk.setText(getattr(cfg, "volc_iam_sk", "") or "")
        self._txt_volc_console_app.setText(getattr(cfg, "volc_console_app_id", "") or "")
        self._chk_vad.setChecked(bool(getattr(cfg, "vad_enabled", True)))
        self._chk_vad_game.setChecked(bool(getattr(cfg, "vad_game_enabled", False)))
        self._chk_aec.setChecked(bool(getattr(cfg, "aec_enabled", True)))
        self._select_combo_data(
            self._cmb_vad_sens, getattr(cfg, "vad_sensitivity", "medium") or "medium"
        )
        self._select_combo_data(
            self._cmb_vad_backend, getattr(cfg, "vad_backend", "auto") or "auto"
        )
        self._vad_open_ms = int(cfg.vad_open_ms)
        self._vad_hangover_ms = int(cfg.vad_hangover_ms)
        self._cmb_quality.blockSignals(True)
        self._select_combo_data(
            self._cmb_quality, getattr(cfg, "quality_preset", "balanced") or "balanced"
        )
        self._cmb_quality.blockSignals(False)
        self._select_combo_data(
            self._cmb_original_audio, getattr(cfg, "original_audio", "mix") or "mix"
        )
        self._spn_duck_gain.setValue(float(cfg.duck_gain))
        self._select_combo_data(
            self._cmb_capture_backend, getattr(cfg, "capture_backend", "auto") or "auto"
        )
        self._spn_rotate.setValue(int(getattr(cfg, "volc_session_rotate_minutes", 12) or 0))
        self._chk_dota_coach.setChecked(bool(getattr(cfg, "dota_coach_enabled", True)))
        self._txt_dota_url.setText(cfg.dota_coach_url)
        self._select_combo_data(
            self._cmb_dota_mode, getattr(cfg, "dota_coach_mode", "normal") or "normal"
        )
        self._set_voice_value(self._cmb_volc_voice, getattr(cfg, "volc_speaker_id", "") or "")
        self._spn_speech_rate.setValue(int(getattr(cfg, "volc_speech_rate", 0) or 0))
        self._spn_history.setValue(int(getattr(cfg, "subtitle_history_lines", 2) or 0))
        self._chk_transcript_logging.setChecked(cfg.transcript_logging_enabled)
        self._spn_transcript_retention.setValue(cfg.transcript_retention_days)
        self._chk_show_original.setChecked(bool(getattr(cfg, "show_original_in_overlay", True)))
        self._chk_subtitle_auto_hide.setChecked(bool(cfg.subtitle_auto_hide))
        self._chk_subtitle_merged.blockSignals(True)
        self._chk_subtitle_merged.setChecked(bool(cfg.subtitle_merged))
        self._chk_subtitle_merged.blockSignals(False)
        self._chk_advanced.blockSignals(True)
        self._chk_advanced.setChecked(bool(getattr(cfg, "show_advanced_devices", False)))
        self._chk_advanced.blockSignals(False)
        # Re-enumerate so advanced loopback list matches checkbox
        self._populate_devices()
        self._select_device(self._cmb_input, cfg.input_device, cfg.input_device_name)
        if cfg.output_device is not None or cfg.output_device_name:
            self._select_device(self._cmb_output, cfg.output_device, cfg.output_device_name)
        self._select_device(self._cmb_loopback, cfg.loopback_device, cfg.loopback_device_name)
        self._rebind_devices_by_name()
        self._apply_advanced_visibility()
        self._corpus_hotwords = list(getattr(cfg, "hotwords", None) or [])
        self._corpus_glossary = dict(getattr(cfg, "glossary", None) or {})
        self._refresh_corpus_label()
        self._chk_music_auto_next.setChecked(bool(getattr(cfg, "music_auto_next", True)))
        for widget in (self._cmb_music_mode, self._edt_music_hold, self._spn_music_delay):
            widget.blockSignals(True)
        self._select_combo_data(self._cmb_music_mode, cfg.music_play_mode)
        self._edt_music_hold.set_combo(cfg.music_hold_key)
        self._spn_music_delay.setValue(cfg.music_hold_delay_ms)
        for widget in (self._cmb_music_mode, self._edt_music_hold, self._spn_music_delay):
            widget.blockSignals(False)
        self._update_music_hold_rows()
        self._apply_music_hold()
        folder = (getattr(cfg, "music_folder", "") or "").strip()
        if folder and Path(folder).expanduser() != self._music_folder:
            self._load_music_folder(Path(folder).expanduser(), select_first=True, quiet=True)

        # Keep language pairs linked both ways (wire once)
        self._syncing_langs = False
        if not self._lang_signals_wired:
            self._cmb_mic_source.currentIndexChanged.connect(self._sync_lang_pairs_from_mic)
            self._cmb_mic_target.currentIndexChanged.connect(self._sync_lang_pairs_from_mic)
            self._cmb_game_source.currentIndexChanged.connect(self._sync_lang_pairs_from_game)
            self._cmb_game_target.currentIndexChanged.connect(self._sync_lang_pairs_from_game)
            self._lang_signals_wired = True
        self._phrase_panel.set_settings(cfg.quick_phrases)
        self._refresh_phrase_target()
        self._sync_phrase_overlay()
        self._on_enable_toggled()
        self._refresh_settings_usage()
        self._refresh_model_sources()
        self._update_save_button()

    def _rebind_devices_by_name(self) -> None:
        """Adopt today's index for a renamed-in-place device, and backfill names."""
        update: dict[str, Any] = {}
        for field, combo in (
            ("input_device", self._cmb_input),
            ("output_device", self._cmb_output),
            ("loopback_device", self._cmb_loopback),
        ):
            if combo.currentData(self._DEVICE_MISSING_ROLE):
                continue
            idx, name = combo.currentData(), self._combo_device_name(combo)
            saved_idx = getattr(self._config, field)
            saved_name = getattr(self._config, f"{field}_name")
            name_count = sum(
                1 for i in range(combo.count()) if combo.itemData(i, self._DEVICE_NAME_ROLE) == name
            )
            if saved_name and name == saved_name and name_count == 1 and str(idx) != str(saved_idx):
                update[field] = idx  # same device, new index after a replug
            elif not saved_name and saved_idx is not None and str(idx) == str(saved_idx) and name:
                update[f"{field}_name"] = name  # config from before names were stored
        if not update:
            return
        moved = {k for k in update if not k.endswith("_name")}
        self._config = self._config.model_copy(update=update)
        if moved:
            self._append_log("音频设备编号已变化，已按设备名重新匹配")
            self._persist_config()

    def _config_snapshot(self, cfg: AppConfigModel) -> dict[str, Any]:
        return cfg.model_dump(mode="json")

    def _is_dirty(self) -> bool:
        try:
            draft = self._collect_config_from_ui()
        except Exception:
            return True
        return self._config_snapshot(draft) != self._config_snapshot(self._config)

    def _update_save_button(self) -> None:
        btn = getattr(self, "_btn_save", None)
        if btn is None:
            return
        dirty = self._is_dirty()
        btn.setText("保存*" if dirty else "保存")
        if dirty:
            btn.setToolTip(
                "有未保存的修改，点击保存后生效。\n"
                "浮层上的拖动、锁定、滚轮调整和字幕显隐开关是即时的，不需要保存。"
            )
        else:
            btn.setToolTip(
                "设置已与本地配置同步。\n通道相关项保存后，需重新开启通道才会应用到正在运行的会话。"
            )

    def _commit_from_ui(self, *, persist: bool = True, apply: bool = True) -> bool:
        """UI draft → self._config (+ disk); ``apply`` puts the saved settings into effect."""
        if apply:
            self._confirm_mode_switch()
        previous = self._config
        try:
            updated = self._collect_config_from_ui()
            if updated.model_dir != previous.model_dir:
                self._check_model_dir(updated.model_dir)
            if persist:
                cleared = {
                    field
                    for field in SECRET_FIELDS
                    if str(getattr(previous, field, "") or "").strip()
                    and not str(getattr(updated, field, "") or "").strip()
                }
                save_config(updated, clear_secret_fields=cleared)
            self._config = updated
            self._hotkeys_draft = None
            if apply:
                self._apply_saved_changes(previous)
            self._update_save_button()
            return True
        except Exception as exc:
            self._config = previous
            QMessageBox.critical(self, "保存失败", str(exc))
            return False

    _ENGINE_RELOAD_FIELDS = frozenset(
        {
            "economy_nllb_model",
            "economy_hymt_model",
            "economy_asr_backend",
            "economy_asr_mic_model",
            "economy_asr_game_model",
            "device_preference",
            "model_dir",
        }
    )
    _OVERLAY_LOOK_FIELDS = frozenset(
        {
            "subtitle_font_size",
            "subtitle_opacity",
            "subtitle_text_color",
            "subtitle_bg_color",
            "subtitle_follow_theme",
            "subtitle_font_family",
            "subtitle_history_lines",
            "show_original_in_overlay",
            "subtitle_auto_hide",
        }
    )

    def _apply_saved_changes(self, previous: AppConfigModel) -> None:
        """Put every setting that changed on save into effect."""
        cfg = self._config
        changed = {
            name for name in type(cfg).model_fields if getattr(previous, name) != getattr(cfg, name)
        }
        if "theme_mode" in changed:
            self._theme = cfg.theme_mode
            self._apply_theme()
            self._append_log(f"主题已切换为「{self._cmb_theme.currentText()}」")
        if changed & {"model_dir", "model_mirror"}:
            self._apply_model_storage()
            self._refresh_model_sources()
        if "model_dir" in changed:
            from src.utils.resource_paths import model_resource_root

            self._append_log(
                f"模型目录已改为 {model_resource_root()}；"
                "已下载的模型不会自动搬过去，可把旧目录内容复制过来以免重新下载"
            )
        engine = {name: getattr(cfg, name) for name in changed & self._ENGINE_RELOAD_FIELDS}
        if "translation_mode" in changed:
            self._apply_translation_mode({**engine, "translation_mode": cfg.translation_mode})
        elif engine:
            stopped = self._reset_economy_engine(engine)
            msg = (
                "引擎/模型设置已保存，通道已停止；重新开启后生效"
                if stopped
                else "引擎/模型设置已保存，将在下次启动通道时生效"
            )
            self._append_log(msg)
            show_toast(msg)
        if "subtitle_merged" in changed:
            self._sync_show_toggles()
            self._reopen_overlays()
        elif changed & self._OVERLAY_LOOK_FIELDS:
            self._apply_overlay_look()
        if "hotkeys" in changed:
            self._apply_hotkeys()
            if self._phrase_overlay is not None:
                self._phrase_overlay.set_hotkeys(self._phrase_hotkey_labels())
        if changed & {"music_play_mode", "music_hold_key", "music_hold_delay_ms"}:
            self._apply_music_hold()
        if changed & {"quick_phrases", "source_language", "target_language"}:
            self._sync_phrase_overlay()
            self._refresh_phrase_target()

    def _persist_config(self) -> None:
        """Write current self._config to disk without pulling dirty UI fields."""
        try:
            save_config(self._config)
        except Exception as exc:
            logger.warning(f"保存配置失败: {exc}")
        self._update_save_button()

    def _ask_unsaved(self, action: str) -> Literal["save", "discard", "cancel"]:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("未保存的修改")
        box.setText(f"有未保存的设置修改。{action}前如何处理？")
        box.setInformativeText(
            "「保存」：写入并采用当前界面设置\n"
            "「丢弃」：恢复为上次保存的设置\n"
            "「取消」：继续留在当前页"
        )
        save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("丢弃", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_btn:
            return "save"
        if clicked == discard_btn:
            return "discard"
        del cancel_btn
        return "cancel"

    def _on_nav_clicked(self, index: int) -> None:
        prev = self._current_tab_index
        if index == prev:
            self._show_page(index)
            return
        if not self._is_dirty():
            self._show_page(index)
            return

        choice = self._ask_unsaved("切换页面")
        if choice == "save":
            if not self._commit_from_ui(persist=True):
                self._show_page(prev)
                return
            self._append_log("配置已保存")
            self._show_page(index)
            return
        if choice == "discard":
            self._load_config_into_ui()
            self._append_log("已丢弃未保存的修改")
            self._show_page(index)
            return
        self._show_page(prev)

    def _collect_config_from_ui(self) -> AppConfigModel:
        # Merge into the complete loaded model. Engine-only/new settings that do
        # not have controls on this screen must not reset when the user saves an
        # unrelated UI option.
        return merge_config_updates(
            self._config,
            source_language=self._cmb_mic_source.currentData(),
            target_language=self._cmb_mic_target.currentData(),
            subtitle_font_size=self._spn_font.value(),
            subtitle_opacity=self._overlay_opacity(),
            subtitle_text_color=self._subtitle_text_color,
            subtitle_bg_color=self._subtitle_bg_color,
            subtitle_follow_theme=self._chk_subtitle_follow_theme.isChecked(),
            subtitle_font_family=self._cmb_subtitle_font.currentData() or "",
            log_dir=self._config.log_dir,
            debug_mode=self._config.debug_mode,
            subtitle_window_positions=self._config.subtitle_window_positions,
            enable_mic=self._chk_enable_mic.isChecked(),
            enable_game=self._chk_enable_game.isChecked(),
            show_mic_subtitle=self._chk_show_mic.isChecked(),
            show_game_subtitle=self._chk_show_game.isChecked(),
            play_mic_voice=self._chk_play_mic.isChecked(),
            play_game_voice=self._chk_play_game.isChecked(),
            input_device=self._cmb_input.currentData(),
            output_device=self._cmb_output.currentData(),
            loopback_device=self._cmb_loopback.currentData(),
            input_device_name=self._combo_device_name(self._cmb_input),
            output_device_name=self._combo_device_name(self._cmb_output),
            loopback_device_name=self._combo_device_name(self._cmb_loopback),
            translation_mode=self._cmb_translation_mode.currentData() or "volc",
            volc_api_key=self._txt_volc_key.text().strip(),
            economy_dashscope_api_key=(
                self._txt_dashscope_key.text().strip()
                if hasattr(self, "_txt_dashscope_key")
                else getattr(self._config, "economy_dashscope_api_key", "")
            ),
            economy_asr_backend=(
                self._cmb_asr_backend.currentData()
                if hasattr(self, "_cmb_asr_backend") and self._cmb_asr_backend.currentData()
                else self._config.economy_asr_backend
            ),
            economy_asr_model=getattr(self._config, "economy_asr_model", "fun-asr-realtime")
            or "fun-asr-realtime",
            economy_asr_mic_model=(
                self._cmb_mic_asr_model.currentData() or self._config.economy_asr_mic_model
            ),
            economy_asr_game_model=(
                self._cmb_game_asr_model.currentData() or self._config.economy_asr_game_model
            ),
            **self._collect_economy_mt_fields(),
            **self._collect_tts_fields(),
            economy_nllb_model=(
                self._cmb_nllb_model.currentData()
                if hasattr(self, "_cmb_nllb_model") and self._cmb_nllb_model.currentData()
                else getattr(
                    self._config,
                    "economy_nllb_model",
                    "JustFrederik/nllb-200-distilled-600M-ct2-int8",
                )
                or "JustFrederik/nllb-200-distilled-600M-ct2-int8"
            ),
            # Picking an NLLB model in settings counts as finishing the first-run choice.
            economy_offline_setup_done=bool(
                getattr(self._config, "economy_offline_setup_done", False)
                or (
                    hasattr(self, "_cmb_nllb_model")
                    and self._cmb_nllb_model.currentData()
                    and self._cmb_nllb_model.currentData() != self._config.economy_nllb_model
                )
            ),
            model_dir=self._model_dir_draft(),
            model_mirror=(
                self._cmb_model_mirror.currentData()
                if hasattr(self, "_cmb_model_mirror")
                else self._config.model_mirror
            ),
            economy_sentence_min_chars=int(
                getattr(self._config, "economy_sentence_min_chars", 4) or 4
            ),
            economy_sentence_pause_ms=int(
                getattr(self._config, "economy_sentence_pause_ms", 900) or 900
            ),
            device_preference=(
                self._cmb_device_pref.currentData()
                if hasattr(self, "_cmb_device_pref") and self._cmb_device_pref.currentData()
                else getattr(self._config, "device_preference", "auto") or "auto"
            ),
            economy_utterance_silence_ms=int(
                getattr(self._config, "economy_utterance_silence_ms", 300) or 300
            ),
            economy_utterance_min_ms=int(
                getattr(self._config, "economy_utterance_min_ms", 400) or 400
            ),
            economy_utterance_max_ms=int(
                getattr(self._config, "economy_utterance_max_ms", 8000) or 8000
            ),
            economy_utterance_soft_split_ms=int(
                getattr(self._config, "economy_utterance_soft_split_ms", 6000) or 6000
            ),
            economy_utterance_soft_split_quiet_ms=int(
                getattr(self._config, "economy_utterance_soft_split_quiet_ms", 280) or 280
            ),
            economy_utterance_tail_rms=float(
                getattr(self._config, "economy_utterance_tail_rms", 0.003) or 0.003
            ),
            volc_access_token=self._txt_volc_token.text().strip(),
            volc_speaker_id=self._voice_value(self._cmb_volc_voice),
            volc_speech_rate=self._spn_speech_rate.value(),
            volc_iam_ak=self._txt_volc_iam_ak.text().strip(),
            volc_iam_sk=self._txt_volc_iam_sk.text().strip(),
            volc_console_app_id=self._txt_volc_console_app.text().strip(),
            hotwords=list(getattr(self, "_corpus_hotwords", None) or []),
            glossary=dict(getattr(self, "_corpus_glossary", None) or {}),
            subtitle_history_lines=self._spn_history.value(),
            transcript_logging_enabled=self._chk_transcript_logging.isChecked(),
            transcript_retention_days=self._spn_transcript_retention.value(),
            show_original_in_overlay=self._chk_show_original.isChecked(),
            subtitle_merged=self._chk_subtitle_merged.isChecked(),
            subtitle_auto_hide=self._chk_subtitle_auto_hide.isChecked(),
            overlay_locked=self._config.overlay_locked,
            theme_mode=normalize_theme_key(self._cmb_theme.currentData()),
            hotkeys=self._hotkeys_draft or self._config.hotkeys,
            quick_phrases=(
                self._phrase_panel.settings.model_copy(deep=True)
                if hasattr(self, "_phrase_panel")
                else self._config.quick_phrases
            ),
            music_folder=str(self._music_folder) if self._music_folder else "",
            music_auto_next=self._chk_music_auto_next.isChecked(),
            **self._music_hold_fields(),
            show_advanced_devices=self._chk_advanced.isChecked(),
            vad_enabled=self._chk_vad.isChecked(),
            vad_game_enabled=self._chk_vad_game.isChecked(),
            aec_enabled=self._chk_aec.isChecked(),
            vad_backend=self._cmb_vad_backend.currentData() or "auto",
            vad_sensitivity=self._cmb_vad_sens.currentData() or "medium",
            vad_open_ms=int(getattr(self, "_vad_open_ms", self._config.vad_open_ms)),
            vad_hangover_ms=int(getattr(self, "_vad_hangover_ms", self._config.vad_hangover_ms)),
            vad_preroll_ms=int(getattr(self._config, "vad_preroll_ms", 300) or 300),
            vad_barge_in_ms=int(getattr(self._config, "vad_barge_in_ms", 200) or 200),
            quality_preset=self._cmb_quality.currentData() or "balanced",
            capture_backend=self._cmb_capture_backend.currentData() or "auto",
            # Persist the user's selected policy even when the current channel
            # state means ducking is temporarily inactive.
            original_audio=self._cmb_original_audio.currentData() or "mix",
            duck_gain=float(self._spn_duck_gain.value()),
            volc_session_rotate_minutes=int(self._spn_rotate.value()),
            dota_coach_enabled=self._chk_dota_coach.isChecked(),
            dota_coach_url=self._txt_dota_url.text().strip(),
            dota_coach_mode=self._cmb_dota_mode.currentData() or "normal",
            dota_coach_arm_seconds=int(getattr(self._config, "dota_coach_arm_seconds", 12) or 12),
        )

    def _on_quality_preset_changed(self) -> None:
        from src.core.quality_presets import apply_quality_preset

        params = apply_quality_preset(self._cmb_quality.currentData() or "balanced")
        self._chk_vad.setChecked(params.vad_enabled)
        self._chk_vad_game.setChecked(params.vad_game_enabled)
        self._select_combo_data(self._cmb_vad_backend, params.vad_backend)
        self._select_combo_data(self._cmb_vad_sens, params.vad_sensitivity)
        self._vad_open_ms = params.vad_open_ms
        self._vad_hangover_ms = params.vad_hangover_ms
        self._append_log(
            f"质量档位 → {self._cmb_quality.currentText()} "
            f"(open={params.vad_open_ms}ms hangover={params.vad_hangover_ms}ms)"
        )

    def _confirm_mode_switch(self) -> None:
        """First switch to economy mode needs the offline model choice; cancel keeps the engine."""
        mode = self._cmb_translation_mode.currentData() or "volc"
        if mode != "economy" or self._config.translation_mode == "economy":
            return
        if self._ensure_economy_offline_setup():
            return
        self._cmb_translation_mode.blockSignals(True)
        self._select_combo_data(self._cmb_translation_mode, self._config.translation_mode)
        self._cmb_translation_mode.blockSignals(False)
        self._append_log("未完成本地模型设置，翻译引擎保持不变")

    def _apply_translation_mode(self, update: dict[str, Any]) -> None:
        """Saved a new engine: stop channels so the next start builds it."""
        mode = self._config.translation_mode
        stopped = self._reset_engine_for_setting(update)

        label = "火山同传" if mode == "volc" else "经济模式"
        msg = f"已切换引擎 → {label}"
        if stopped:
            msg = "已切换引擎，通道已停止"
            self._append_log(msg)
            show_toast(msg)
        else:
            self._append_log(msg)
        if mode == "economy":
            from src.engines.pipeline.engine import (
                LOCAL_ASR_BACKENDS,
                resolve_dashscope_api_key,
                resolve_economy_asr_backend,
            )

            # Prefer live combo selection so toast matches what the user sees.
            asr_pref = ""
            if hasattr(self, "_cmb_asr_backend") and self._cmb_asr_backend.currentData():
                asr_pref = str(self._cmb_asr_backend.currentData())
            asr_pref = asr_pref or resolve_economy_asr_backend(self._config)
            if asr_pref in LOCAL_ASR_BACKENDS:
                if asr_pref == "live_captions":
                    self._append_log(
                        "经济模式已使用 Windows Live Captions（系统级ASR），无需 API Key"
                    )
                else:
                    import sys

                    raw = getattr(self._config, "economy_asr_backend", "") or ""
                    if raw == "live_captions" and not sys.platform.startswith("win"):
                        self._append_log("Live Captions 仅支持 Windows；已自动切换为本地 ASR")
                    self._append_log("经济模式已使用本地 ASR，无需 DashScope API Key")
            elif not (
                (
                    self._txt_dashscope_key.text().strip()
                    if hasattr(self, "_txt_dashscope_key")
                    else ""
                )
                or resolve_dashscope_api_key(self._config)
            ):
                warn = (
                    "经济模式云端 ASR 需要 DashScope API Key"
                    "（设置页填写或环境变量 DASHSCOPE_API_KEY），"
                    "或切换为「本地 ASR / Live Captions」"
                )
                self._append_log(warn)
                show_toast(warn)

    def _reset_economy_engine(self, update: dict[str, Any]) -> bool:
        if (getattr(self._config, "translation_mode", "volc") or "volc") != "economy":
            return False
        return self._reset_engine_for_setting(update)

    def _reset_engine_for_setting(self, update: dict[str, Any]) -> bool:
        """Stop channels and drop the engine so the next start uses ``update``.

        Runs on the channel worker behind any pending start, never on the Qt thread.
        Returns True when a channel was running or about to run.
        """
        pipeline = self._pipeline
        if pipeline is None:
            return False
        stopped = bool(pipeline.active_channels() or self._channel_busy)
        if stopped:
            self._close_overlays()
            self._timer.stop()
            self._btn_stop.setEnabled(False)
            self._progress.setVisible(False)
            self._badge.setText("Idle")

        def work() -> Callable[[], None]:
            if pipeline.active_channels():
                pipeline.stop()
            pipeline.close_engine()
            pipeline._config = pipeline._config.model_copy(update=update)
            return self._on_enable_toggled

        self._run_channel_op(("mic", "game"), work)
        return stopped

    def _ensure_economy_offline_setup(self) -> bool:
        """Show first-run offline model dialog if needed. Returns False if cancelled."""
        if bool(getattr(self._config, "economy_offline_setup_done", False)):
            return True

        from src.gui.offline_model_dialog import OfflineModelDialog

        dlg = OfflineModelDialog(
            current_model_id=getattr(self._config, "economy_nllb_model", None),
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return False

        model_id = dlg.selected_nllb_model_id()
        self._config = self._config.model_copy(
            update={
                "economy_nllb_model": model_id,
                "economy_offline_setup_done": True,
            }
        )
        if hasattr(self, "_cmb_nllb_model"):
            self._cmb_nllb_model.blockSignals(True)
            self._select_combo_data(self._cmb_nllb_model, model_id)
            self._cmb_nllb_model.blockSignals(False)
        self._persist_config()
        self._append_log(f"已选择本地翻译模型：{model_id}")
        return True

    _PREVIEW_LINES: tuple[tuple[str, str], ...] = (
        ("zh", "你好，这是语音试听，欢迎使用实时同传。"),
        ("en", "Hello, this is a voice preview."),
    )

    def _on_preview_tts(self, engine_id: str) -> None:
        from src.engines.pipeline.tts_catalog import tts_engine

        if self._preview_thread is not None and self._preview_thread.is_alive():
            self._append_log("上一段试听还在进行，请稍候")
            return
        try:
            draft = self._collect_config_from_ui()
        except Exception as exc:
            self._append_log(f"试听失败：设置无效（{exc}）")
            return
        draft = draft.model_copy(update={"economy_tts_backend": engine_id})
        title = tts_engine(engine_id).title
        self._append_log(f"正在试听 {title}…（本地模型首次使用需下载）")
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(
            target=self._run_tts_preview,
            args=(draft, title),
            name="tts-preview",
            daemon=True,
        )
        self._preview_thread.start()

    def _run_tts_preview(self, config: AppConfigModel, title: str) -> None:
        emit = self._preview_bridge.message.emit
        try:
            self._synthesize_preview(config, title, emit)
        except Exception as exc:
            logger.exception("TTS preview failed")
            emit(f"{title} 试听失败：{exc}")

    def _synthesize_preview(
        self, config: AppConfigModel, title: str, emit: Callable[[str], None]
    ) -> None:
        import numpy as np

        from src.engines.pipeline.engine import build_tts

        tts = build_tts(config)
        # Preview the chosen engine itself, not the Edge fallback.
        tts = getattr(tts, "inner", tts)
        tts.start()
        try:
            deadline = time.monotonic() + 300
            while getattr(tts, "warming_up", False) and time.monotonic() < deadline:
                if self._preview_stop.wait(0.2):
                    return
            if not tts.configured:
                emit(f"{title} 未就绪：请检查账号/接口地址，或等待模型下载完成（详见日志）")
                return
            lines: tuple[tuple[str, str], ...] = self._PREVIEW_LINES
            if config.economy_tts_backend == "melo":
                lines = (("zh", "你好，this is MeloTTS，中英混读试听。"),)
            chunks = [tts.synthesize(text, language=lang) for lang, text in lines]
            pcm = b"".join(c for c in chunks if c)
        finally:
            tts.stop()
        if not pcm:
            emit(f"{title} 合成失败：请检查密钥与音色 ID（详见日志）")
            return
        if self._preview_stop.is_set():
            return
        try:
            import sounddevice as sd

            sd.play(np.frombuffer(pcm, dtype=np.int16), 16000)
            emit(f"{title} 试听播放中（默认扬声器）")
            sd.wait()
        except Exception as exc:
            emit(f"试听播放失败：{exc}")

    def _stop_tts_preview(self) -> None:
        self._preview_stop.set()
        if self._preview_thread is not None and self._preview_thread.is_alive():
            with contextlib.suppress(Exception):
                import sounddevice as sd

                sd.stop()
            self._preview_thread.join(timeout=1.0)
        self._preview_thread = None

    def _collect_tts_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "economy_tts_backend": self._cmb_tts_backend.currentData() or "kokoro",
            "economy_kokoro_speed": round(self._spn_tts_speed.value(), 2),
            "economy_cosyvoice_model": self._txt_cosyvoice_model.text().strip() or "cosyvoice-v2",
            "economy_openai_tts_url": self._txt_openai_tts_url.text().strip(),
            "economy_openai_tts_model": self._txt_openai_tts_model.text().strip(),
            "economy_openai_tts_api_key": self._txt_openai_tts_key.text().strip(),
        }
        for field, combo in self._tts_voice_combos.items():
            fields[field] = self._voice_value(combo) or getattr(self._config, field)
        return fields

    def _collect_economy_mt_fields(self) -> dict[str, Any]:
        cfg = self._config
        fields: dict[str, Any] = {
            key: getattr(cfg, key)
            for key in (
                "economy_mt_backend",
                "economy_llm_base_url",
                "economy_llm_model",
                "economy_llm_api_key",
                "economy_asr_device",
                "economy_mt_device",
                "economy_tts_device",
            )
        }
        if hasattr(self, "_cmb_mt_backend"):
            fields["economy_mt_backend"] = self._cmb_mt_backend.currentData() or "nllb"
            fields["economy_llm_base_url"] = self._txt_llm_url.text().strip()
            fields["economy_llm_model"] = self._txt_llm_model.text().strip()
            fields["economy_llm_api_key"] = self._txt_llm_key.text().strip()
        if hasattr(self, "_cmb_hymt_model"):
            fields["economy_hymt_model"] = (
                self._cmb_hymt_model.currentData() or cfg.economy_hymt_model
            )
        for task, combo in getattr(self, "_task_device_combos", {}).items():
            fields[f"economy_{task}_device"] = combo.currentData() or ""
        return fields

    def _resolve_output_device_name(self) -> str:
        """根据当前 output_device 配置返回可读的设备名 (而非索引数字)."""
        try:
            if hasattr(self, "_cmb_output"):
                idx = self._cmb_output.currentIndex()
                if idx >= 0:
                    text = self._cmb_output.itemText(idx)
                    if text:
                        return text
        except Exception:
            pass
        out_dev = getattr(self._config, "output_device", None)
        if out_dev is None:
            return "系统默认"
        return f"设备 #{out_dev}"

    def _start_resource_monitor(self) -> None:
        """启动后台资源监控 (CPU/RAM/GPU), 定时刷新标签."""
        from src.utils.device_utils import ResourceMonitor

        self._resource_monitor = ResourceMonitor(interval_ms=1500)
        self._resource_monitor.start()
        self._resource_timer.start()
        self._download_timer.start()

    def _on_resource_tick(self) -> None:
        """定时刷新资源占用标签."""
        if not hasattr(self, "_lbl_resource") or self._resource_monitor is None:
            return
        self._lbl_resource.setText(self._resource_monitor.format_text().replace(" | ", "\n"))

    def _refresh_corpus_label(self) -> None:
        hw = getattr(self, "_corpus_hotwords", None) or []
        gl = getattr(self, "_corpus_glossary", None) or {}
        self._lbl_corpus.setText(f"热词 {len(hw)} / 术语 {len(gl)}")

    def _on_edit_corpus(self) -> None:
        from src.gui.corpus_dialog import CorpusDialog

        dlg = CorpusDialog(
            hotwords=getattr(self, "_corpus_hotwords", None) or [],
            glossary=getattr(self, "_corpus_glossary", None) or {},
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._corpus_hotwords, self._corpus_glossary = dlg.result_corpus()
        self._refresh_corpus_label()
        self._update_save_button()
        self._append_log(
            f"热词/术语已修改：热词 {len(self._corpus_hotwords)} / 术语 {len(self._corpus_glossary)}"
            "（点「保存」后生效，运行中的通道需重新开启）"
        )

    def _build_typed_page(self) -> QFrame:
        from src.gui.typed_panel import TypedTranslatePanel

        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        self._typed_panel = TypedTranslatePanel()
        self._typed_panel.translated.connect(self._persist_typed_translation)
        layout.addWidget(self._typed_panel)
        return card

    def _build_transcripts_page(self) -> QFrame:
        from src.gui.transcript_panel import TranscriptPanel

        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        self._transcript_panel = TranscriptPanel(resolve_log_dir(self._config.log_dir))
        layout.addWidget(self._transcript_panel)
        return card

    def _build_phrase_page(self) -> QFrame:
        from src.core.phrase_input import PhraseInserter
        from src.core.phrase_translate import PhraseTranslator
        from src.gui.phrase_panel import PhrasePanel, PhraseResultBridge

        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        self._phrase_overlay: PhraseOverlay | None = None
        self._phrase_panel = PhrasePanel(self._config.quick_phrases)
        self._phrase_panel.settings_changed.connect(self._on_phrase_settings_changed)
        self._phrase_panel.overlay_toggled.connect(
            lambda checked: self._update_phrase_settings(show_overlay=bool(checked))
        )
        self._phrase_panel.retranslate_requested.connect(
            lambda ids: self._ensure_phrase_translations(ids, force=True)
        )
        layout.addWidget(self._phrase_panel)
        self._phrase_bridge = PhraseResultBridge(self)
        self._phrase_bridge.translated.connect(self._on_phrase_translated)
        self._phrase_translator = PhraseTranslator(
            lambda: self._config, self._phrase_bridge.translated.emit
        )
        self._phrase_inserter = PhraseInserter(self)
        return card

    def _phrase_langs(self) -> tuple[str, str]:
        return self._config.source_language, self._config.target_language

    def _refresh_phrase_target(self) -> None:
        _source, target = self._phrase_langs()
        label = next((text.split()[0] for text, code in _LANG_LABELS if code == target), target)
        self._phrase_panel.set_target(target, label)
        self._ensure_phrase_translations()

    def _apply_phrase_settings(self, settings: QuickPhraseSettings) -> None:
        self._config = self._config.model_copy(update={"quick_phrases": settings})
        self._persist_config()
        self._sync_phrase_overlay()

    def _on_phrase_settings_changed(self, _settings: QuickPhraseSettings) -> None:
        self._update_save_button()

    def _update_phrase_settings(self, **changes: Any) -> None:
        """Overlay state (position, lock, size, shown): applies at once, keeps the panel draft."""
        draft = self._phrase_panel.settings.model_copy(update=changes)
        self._phrase_panel.set_settings(draft)
        self._apply_phrase_settings(self._config.quick_phrases.model_copy(update=changes))

    def _toggle_phrase_overlay(self) -> None:
        self._update_phrase_settings(show_overlay=not self._config.quick_phrases.show_overlay)

    def _ensure_phrase_translations(
        self, ids: list[str] | None = None, *, force: bool = False
    ) -> None:
        from src.core.phrase_translate import PhraseRequest

        settings = self._config.quick_phrases
        if not settings.translate:
            return
        source, target = self._phrase_langs()
        wanted = set(ids) if ids is not None else None
        for group in settings.groups:
            for phrase in group.phrases:
                if wanted is not None and phrase.id not in wanted:
                    continue
                if not force and phrase.translation and phrase.translated_to == target:
                    continue
                request = PhraseRequest(phrase.id, phrase.text, source, target)
                if self._phrase_translator.request(request):
                    self._phrase_panel.set_phrase_status(phrase.id, "翻译中…")

    def _on_phrase_translated(self, request: object, translation: object) -> None:
        from src.core.phrase_translate import PhraseRequest

        if not isinstance(request, PhraseRequest):
            return
        settings = self._config.quick_phrases.model_copy(deep=True)
        phrase = settings.find(request.phrase_id)
        if phrase is None or phrase.text != request.text:
            self._phrase_panel.set_phrase_status(request.phrase_id, "")
            return
        if not translation:
            self._phrase_panel.set_phrase_status(request.phrase_id, "翻译失败，可点「重新翻译」")
            return
        phrase.translation = str(translation)
        phrase.translated_to = request.target_lang
        draft = self._phrase_panel.settings.model_copy(deep=True)
        pending = draft.find(request.phrase_id)
        if pending is not None and pending.text == request.text:
            pending.translation, pending.translated_to = phrase.translation, phrase.translated_to
            self._phrase_panel.set_settings(draft)
        self._phrase_panel.set_phrase_status(request.phrase_id, "")
        self._apply_phrase_settings(settings)

    def _sync_phrase_overlay(self) -> None:
        settings = self._config.quick_phrases
        overlay = self._phrase_overlay
        if not settings.show_overlay:
            if overlay is not None:
                overlay.close()
                overlay.deleteLater()
                self._phrase_overlay = None
            return
        if overlay is None:
            overlay = PhraseOverlay(settings)
            overlay.phrase_clicked.connect(self._on_phrase_clicked)
            overlay.group_changed.connect(
                lambda name: self._update_phrase_settings(current_group=name)
            )
            overlay.geometry_changed.connect(
                lambda geom: self._update_phrase_settings(overlay_geometry=tuple(geom))
            )
            overlay.lock_changed.connect(
                lambda locked: self._update_phrase_settings(overlay_locked=bool(locked))
            )
            overlay.font_size_requested.connect(
                lambda size: self._update_phrase_settings(font_size=int(size))
            )
            overlay.opacity_requested.connect(
                lambda value: self._update_phrase_settings(chip_opacity=float(value))
            )
            overlay.hide_requested.connect(lambda: self._update_phrase_settings(show_overlay=False))
            self._phrase_overlay = overlay
            overlay.show()
        else:
            overlay.set_settings(settings)
        overlay.set_hotkeys(self._phrase_hotkey_labels())

    def _phrase_hotkey_labels(self) -> list[str]:
        hk = self._config.hotkeys
        if not hk.enabled:
            return []
        return [combo_to_display(getattr(hk, f"phrase_{n}", "")) for n in range(1, 10)]

    def _on_phrase_clicked(self, phrase_id: str) -> None:
        settings = self._config.quick_phrases
        phrase = settings.find(phrase_id)
        if phrase is None:
            return
        text = phrase.output(translate=settings.translate, bilingual=settings.bilingual)
        if self._phrase_inserter.insert(text, press_enter=phrase.auto_enter):
            return
        show_toast("已复制到剪贴板，按 Ctrl+V 粘贴（当前系统不支持自动输入）", ms=2500)

    def _send_phrase_slot(self, number: int) -> None:
        phrases = self._config.quick_phrases.active_group().phrases
        if 0 < number <= len(phrases):
            self._on_phrase_clicked(phrases[number - 1].id)
        else:
            show_toast(f"当前分组没有第 {number} 条常用语")

    def _persist_typed_translation(self, original: str, translated: str) -> None:
        from src.utils.logger import SubtitleLogger

        try:
            if not self._config.transcript_logging_enabled:
                return
            if self._pipeline is not None:
                self._pipeline.log_typed_translation(original, translated)
            else:
                if getattr(self, "_archive_logger", None) is None:
                    self._archive_logger = SubtitleLogger(
                        resolve_log_dir(self._config.log_dir),
                        enabled=self._config.transcript_logging_enabled,
                        retention_days=self._config.transcript_retention_days,
                    )
                self._archive_logger.log_typed(original, translated)
            self._append_log("打字翻译已写入留档")
        except Exception as exc:
            logger.warning(f"打字翻译留档失败: {exc}")

    def _on_open_usage(self) -> None:
        from src.gui.usage_dialog import UsageDialog

        dlg = UsageDialog(
            self._usage_tracker,
            parent=self,
            pack_remain_text=self._pack_remain_text,
            on_refresh_packs=self._on_refresh_pack_quota,
        )
        dlg.exec()
        self._refresh_usage_chip(self._usage_tracker.state)

    def _on_usage_state(self, state: object) -> None:
        if isinstance(state, UsageState):
            self._refresh_usage_chip(state)

    def _refresh_usage_chip(self, state: UsageState) -> None:
        if not hasattr(self, "_btn_usage"):
            return
        self._btn_usage.setText(
            f"用量 ¥{state.session_cost:.3f} / 累¥{state.total_cost:.2f} · "
            f"{state.session_tokens:,}tok"
        )
        tip = (
            f"本次会话：{state.session_tokens:,} tokens / ¥{state.session_cost:.4f}\n"
            f"累计：{state.total_tokens:,} tokens / ¥{state.total_cost:.4f}\n"
            f"{self._pack_remain_text}\n"
            "点击查看明细；设置里可填 IAM 查询资源包剩余。"
        )
        self._btn_usage.setToolTip(tip)
        if hasattr(self, "_lbl_pack_remain"):
            self._lbl_pack_remain.setText(self._pack_remain_text)

    def _on_pipeline_usage(self, source: str, payload: dict[str, Any]) -> None:
        try:
            self._usage_tracker.feed_usage_dict(source or "mic", payload)
        except Exception as exc:
            logger.debug(f"usage feed failed: {exc}")

    def _on_refresh_pack_quota(self) -> None:
        from src.core.volc_console import (
            fetch_quota_monitoring,
            fetch_resource_packs,
            summarize_quota_payload,
        )

        ak = self._txt_volc_iam_ak.text().strip()
        sk = self._txt_volc_iam_sk.text().strip()
        app_id = self._txt_volc_console_app.text().strip()
        if not ak or not sk:
            self._pack_remain_text = "剩余：未配置 IAM AK/SK（仅本地用量可用）"
            self._refresh_usage_chip(self._usage_tracker.state)
            QMessageBox.information(
                self,
                "额度查询",
                "语音 API Key 无法查询账户剩余资源包。\n"
                "请在设置中填写火山 IAM AccessKey / SecretKey 后重试。",
            )
            return
        try:
            packs = fetch_resource_packs(ak=ak, sk=sk, app_id=app_id)
            if packs:
                self._pack_remain_text = "剩余：\n" + "\n".join(p.summary for p in packs[:6])
            elif app_id:
                payload = fetch_quota_monitoring(ak=ak, sk=sk, app_id=app_id)
                self._pack_remain_text = "配额：" + summarize_quota_payload(payload)
            else:
                self._pack_remain_text = "剩余：控制台无资源包数据（可补填 AppID 再查并发配额）"
            self._refresh_usage_chip(self._usage_tracker.state)
            self._append_log(self._pack_remain_text.replace("\n", " · "))
        except Exception as exc:
            self._pack_remain_text = f"剩余：查询失败（{exc}）"
            self._refresh_usage_chip(self._usage_tracker.state)
            QMessageBox.warning(self, "额度查询失败", str(exc))

    def _on_device_guide(self) -> None:
        self._on_detect_vb_cable()

    def _refresh_devices_button(self) -> QPushButton:
        btn = QPushButton("刷新设备")
        btn.setObjectName("ghostButton")
        btn.setToolTip("重新扫描音频设备（插拔耳机/声卡后使用），已选设备按名称保留")
        btn.clicked.connect(self._on_refresh_devices)
        return btn

    def _repopulate_devices_keeping_selection(self) -> None:
        picks = [
            (c, c.currentData(), self._combo_device_name(c))
            for c in (self._cmb_input, self._cmb_output, self._cmb_loopback)
        ]
        self._populate_devices()
        for combo, idx, name in picks:
            if idx is not None or combo is not self._cmb_output:
                self._select_device(combo, idx, name)

    def _on_refresh_devices(self) -> None:
        running = bool(
            self._channel_busy or (self._pipeline is not None and self._pipeline.active_channels())
        )
        rescanned = False
        if not running:
            from src.audio.device_listing import rescan_portaudio

            rescanned = rescan_portaudio()
        self._repopulate_devices_keeping_selection()
        self._rebind_devices_by_name()
        self._update_save_button()
        if running:
            self._append_log("音频设备列表已刷新；新插入的设备需先停止通道再刷新才会出现")
        else:
            self._append_log("音频设备已重新扫描" if rescanned else "音频设备列表已刷新")

    def _on_advanced_toggled(self, checked: bool) -> None:
        self._apply_advanced_visibility()
        self._repopulate_devices_keeping_selection()
        self._append_log(
            "游戏声音列表已显示虚拟线缆捕获源" if checked else "游戏声音列表已隐藏虚拟线缆捕获源"
        )

    def _apply_advanced_visibility(self) -> None:
        # Output and capture pickers stay visible; the toggle only widens the capture list.
        for row in (getattr(self, "_output_row", None), getattr(self, "_loopback_row", None)):
            if row is not None:
                row.setVisible(True)

    def _on_detect_vb_cable(self) -> None:
        inputs = list(getattr(self, "_device_cache_inputs", None) or [])
        outputs = list(getattr(self, "_device_cache_outputs", None) or [])
        if not inputs and not outputs:
            # Fresh scan
            self._populate_devices()
            inputs = list(getattr(self, "_device_cache_inputs", None) or [])
            outputs = list(getattr(self, "_device_cache_outputs", None) or [])
        report = detect_vb_cable_link(inputs=inputs, outputs=outputs)
        # Offer auto-apply CABLE Input if found and output empty/default
        if report.has_cable_input and self._cmb_output.currentData() is None:
            self._select_combo_data(self._cmb_output, report.cable_input_id)
            self._append_log(f"已自动选择译文输出：{report.cable_input_name}")
        title = "虚拟声卡检测 · 通过" if report.ok else "虚拟声卡检测 · 不完整"
        QMessageBox.information(self, title, report.summary())
        self._append_log(title)

    def _combo_label(self, combo: QComboBox) -> str:
        return combo.currentText() or ""

    def _try_auto_avoid_shared_cable(self, channel: Literal["mic", "game"]) -> None:
        """If mic TTS and game capture share VB-Cable, switch game source to system loopback."""
        out = self._cmb_output.currentData()
        out_name = self._combo_label(self._cmb_output)
        lb = self._cmb_loopback.currentData()
        lb_name = self._combo_label(self._cmb_loopback)
        if not shares_virtual_cable_path(
            output_name=out_name,
            output_device=out,
            loopback_name=lb_name,
            loopback_device=lb,
        ):
            return
        # Only auto-avoid when the risky path is actually in play
        mic_voice = self._chk_play_mic.isChecked()
        if channel == "mic" and not mic_voice:
            return
        cache = getattr(self, "_device_cache_inputs", None) or []
        alt = find_preferred_system_loopback(cache)
        if alt is None:
            return
        if str(alt) == str(lb):
            return
        self._select_combo_data(self._cmb_loopback, alt)
        self._append_log("已自动避让：游戏声音改选系统 Loopback，避免与同传虚拟线冲突")
        show_toast("已自动避开虚拟线冲突")

    def _validate_devices_for_start(self, channel: Literal["mic", "game"]) -> bool:
        missing = self._missing_devices(channel)
        if missing:
            QMessageBox.warning(
                self,
                "设备未连接",
                "以下已保存的音频设备当前未连接：\n"
                + "\n".join(missing)
                + "\n\n请插上设备后点「刷新设备」，或在下拉框里改选其它设备。",
            )
            return False
        self._try_auto_avoid_shared_cable(channel)
        mic_active = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.OUTBOUND
        )
        game_active = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.INBOUND
        )
        issues = validate_channel_devices(
            channel,
            input_device=self._cmb_input.currentData(),
            input_name=self._combo_label(self._cmb_input),
            output_device=self._cmb_output.currentData(),
            output_name=self._combo_label(self._cmb_output),
            loopback_device=self._cmb_loopback.currentData(),
            loopback_name=self._combo_label(self._cmb_loopback),
            play_mic_voice=self._chk_play_mic.isChecked(),
            play_game_voice=self._chk_play_game.isChecked(),
            mic_channel_active=mic_active,
            game_channel_active=game_active,
        )
        errors = [i for i in issues if i.level == "error"]
        warns = [i for i in issues if i.level == "warn"]
        infos = [i for i in issues if i.level == "info"]
        for info in infos:
            self._append_log(info.message)
        if errors:
            QMessageBox.critical(self, "设备配置有误", "\n".join(e.message for e in errors))
            return False
        if warns:
            reply = QMessageBox.warning(
                self,
                "设备提醒",
                "\n".join(w.message for w in warns) + "\n\n仍要继续启动吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
        return True

    def _hotkey_mapping(self) -> dict[str, str]:
        hk = self._config.hotkeys
        return {
            action: getattr(hk, action, DEFAULT_HOTKEYS.get(action, ""))
            for action in DEFAULT_HOTKEYS
        }

    def _apply_hotkeys(self) -> None:
        hk = self._config.hotkeys
        mapping = self._hotkey_mapping()
        global_ok = self._hotkeys.apply(mapping, enabled=hk.enabled)
        if self._app_hotkeys is not None:
            # Always keep in-window shortcuts so they work even if pynput fails
            self._app_hotkeys.apply(mapping, enabled=hk.enabled)
        if not hk.enabled:
            self._append_log("快捷键已关闭")
        elif global_ok:
            self._append_log("全局快捷键已启用（游戏前台也可触发）")
        else:
            detail = self._hotkeys.last_error or "pynput 不可用"
            self._append_log(f"全局快捷键不可用，已启用窗口内快捷键。{detail}")

    def _on_edit_hotkeys(self) -> None:
        from src.gui.hotkey_dialog import HotkeyDialog

        # Pause so capturing combos won't also trigger channel start/stop
        self._hotkeys_capturing = True
        self._hotkeys.pause()
        if self._app_hotkeys is not None:
            self._app_hotkeys.set_active(False)
        try:
            dlg = HotkeyDialog(self._hotkeys_draft or self._config.hotkeys, self)
            accepted = dlg.exec() == dlg.DialogCode.Accepted
            if accepted:
                result = dlg.result_config()
                if result is not None:
                    self._hotkeys_draft = result
                    self._append_log("快捷键已修改，点「保存」后生效")
        finally:
            self._hotkeys_capturing = False
            self._apply_hotkeys()
            self._update_save_button()

    def _on_hotkey(self, action: str) -> None:
        if getattr(self, "_hotkeys_capturing", False):
            return
        # Debounce double-fire from global + in-window shortcuts
        now = time.monotonic()
        last = self._hotkey_guard.get(action, 0.0)
        if now - last < 0.35:
            return
        self._hotkey_guard[action] = now

        handlers: dict[str, Callable[[], None]] = {
            "toggle_mic": lambda: self._on_channel_toggle("mic"),
            "toggle_game": lambda: self._on_channel_toggle("game"),
            "stop_all": self._on_stop,
            "toggle_mic_overlay": lambda: self._chk_show_mic.toggle(),
            "toggle_game_overlay": lambda: self._chk_show_game.toggle(),
            "toggle_all_overlays": self._toggle_all_overlays,
            "music_play_pause": self._on_music_hotkey_play_pause,
            "music_stop": self._on_music_stop,
            "music_prev": lambda: self._music_step(-1),
            "music_next": lambda: self._music_step(1),
            "music_toggle_sidebar": self._toggle_music_sidebar,
            "dota_coach_ask": self._on_dota_coach_hotkey,
            "toggle_phrase_overlay": self._toggle_phrase_overlay,
            **{f"phrase_{n}": functools.partial(self._send_phrase_slot, n) for n in range(1, 10)},
        }
        handler = handlers.get(action)
        if handler is None:
            return
        label = HOTKEY_LABELS.get(action, action)
        self._append_log(f"快捷键触发：{label}")
        # Dota 教练有自己的待命/结果 toast；常用语直接输入，不弹提示
        if action != "dota_coach_ask" and not action.startswith("phrase_"):
            show_toast(label)
        handler()

    def _on_music_hotkey_play_pause(self) -> None:
        if self._music.is_playing:
            self._music.pause()
            self._append_log("音乐已暂停")
            return
        if self._music.is_loaded:
            try:
                self._music.resume()
                self._show_music_sidebar()
                self._append_log("音乐继续播放")
            except Exception as exc:
                QMessageBox.warning(self, "播放失败", str(exc))
            return
        # Not loaded yet — try start from current combo selection
        self._start_music_playback(show_conflict_warn=False)

    def _toggle_music_sidebar(self) -> None:
        if not self._music_tracks:
            show_toast("请先选择音乐文件夹")
            return
        if self._music_sidebar is not None and self._music_sidebar.isVisible():
            self._hide_music_sidebar()
            return
        self._show_music_sidebar()

    def _toggle_all_overlays(self) -> None:
        # If either is visible, hide both; else show both
        any_on = self._chk_show_mic.isChecked() or self._chk_show_game.isChecked()
        self._chk_show_mic.setChecked(not any_on)
        self._chk_show_game.setChecked(not any_on)

    def _on_save(self) -> None:
        if not self._commit_from_ui(persist=True):
            return
        self._status_label.setText("状态：配置已保存")
        msg = "配置已保存"
        if self._pipeline is not None and self._pipeline.active_channels():
            self._pipeline._config = self._config
            self._pipeline._input_device = self._config.input_device
            self._pipeline._output_device = self._config.output_device
            self._pipeline._loopback_device = self._config.loopback_device
            msg += "（通道相关项需重新开启通道后生效）"
        self._append_log(msg)

    # ------------------------------------------------------------ overlays
    def _on_geometry_changed(self, key: str, geom: tuple[int, int, int, int]) -> None:
        self._config.subtitle_window_positions[key] = geom
        self._geometry_save_timer.start()

    def _on_lock_changed(self, key: str, locked: bool) -> None:
        self._config.overlay_locked[key] = locked
        label = {"outbound": "麦克风", "inbound": "游戏"}.get(key, "")
        self._append_log(f"{label}字幕已{'锁定（点击穿透）' if locked else '解锁'}")
        self._persist_config()

    def _overlay_windows(self) -> list[SubtitleOverlay]:
        """Open overlay windows; a merged window serves both directions."""
        return list({id(o): o for o in self._overlays.values()}.values())

    def _ensure_overlay(self, direction: Direction) -> SubtitleOverlay:
        overlay = self._overlays.get(direction)
        if overlay is not None:
            return overlay
        cfg = self._config
        merged = cfg.subtitle_merged
        if merged and self._overlays:
            overlay = next(iter(self._overlays.values()))
            self._overlays[direction] = overlay
            return overlay
        key = MERGED_KEY if merged else direction.value
        locked = bool(self._config.overlay_locked.get(key, False))
        look = self._saved_overlay_appearance()
        overlay = SubtitleOverlay(
            None if merged else direction,
            font_size=cfg.subtitle_font_size,
            opacity=cfg.subtitle_opacity,
            locked=locked,
            history_lines=cfg.subtitle_history_lines,
            show_original=cfg.show_original_in_overlay,
            text_color=look["text_color"],
            background_color=look["background_color"],
            font_family=look["font_family"],
            auto_hide=cfg.subtitle_auto_hide,
            on_geometry_changed=self._on_geometry_changed,
            on_lock_changed=self._on_lock_changed,
            on_font_size_requested=self._on_overlay_font_requested,
            on_opacity_requested=self._on_overlay_opacity_requested,
        )
        overlay.set_menu_style(menu_stylesheet(self._active_theme))
        pos = self._config.subtitle_window_positions.get(key)
        if pos:
            overlay.restore_geometry_tuple(pos)
        overlay.show()
        self._overlays[direction] = overlay
        return overlay

    def _drop_overlay(self, direction: Direction) -> None:
        overlay = self._overlays.pop(direction, None)
        if overlay is None:
            return
        if overlay in self._overlays.values():
            overlay.clear_speaker(direction)
        else:
            overlay.close()

    def _close_overlays(self) -> None:
        for overlay in self._overlay_windows():
            overlay.close()
        self._overlays.clear()

    def _reopen_overlays(self) -> None:
        was_open = bool(self._overlays)
        self._close_overlays()
        if was_open:
            self._sync_overlays_visibility(preview=not self._pipeline_running())

    def _pipeline_running(self) -> bool:
        return self._pipeline is not None and bool(self._pipeline.active_channels())

    def _on_preview_overlays(self) -> None:
        # Ensure at least one overlay checkbox is on for preview
        if not self._chk_show_mic.isChecked() and not self._chk_show_game.isChecked():
            self._chk_show_game.setChecked(True)
            self._chk_show_mic.setChecked(True)
        self._sync_overlays_visibility(preview=True)
        # Unlock so user can drag/resize after a previous lock
        for overlay in self._overlay_windows():
            if overlay.is_locked():
                overlay.set_locked(False)
        self._append_log("已打开字幕浮层，可拖拽 / 缩放；双击锁定，要再调整就再点「调整字幕」")

    def _on_look_changed(self, *_args: Any) -> None:
        self._update_save_button()

    def _apply_overlay_look(self) -> None:
        cfg = self._config
        for overlay in self._overlay_windows():
            overlay.set_auto_hide(cfg.subtitle_auto_hide)
            overlay.set_font_size(cfg.subtitle_font_size)
            overlay.set_opacity(cfg.subtitle_opacity)
            overlay.set_history_lines(cfg.subtitle_history_lines)
            overlay.set_show_original(cfg.show_original_in_overlay)
            overlay.set_appearance(**self._saved_overlay_appearance())

    def _overlay_opacity(self) -> float:
        return self._spn_opacity.value() / 100.0

    def _set_overlay_state(self, **changes: Any) -> None:
        """Wheel/menu tweaks on the overlay itself apply and save at once, like dragging it."""
        self._config = self._config.model_copy(update=changes)
        for spin, value in (
            (self._spn_font, changes.get("subtitle_font_size")),
            (self._spn_opacity, changes.get("subtitle_opacity")),
        ):
            if value is None:
                continue
            spin.blockSignals(True)
            spin.setValue(round(value * 100) if spin is self._spn_opacity else int(value))
            spin.blockSignals(False)
        self._persist_config()
        self._apply_overlay_look()

    def _on_overlay_font_requested(self, size: int) -> None:
        self._set_overlay_state(subtitle_font_size=int(size))

    def _on_overlay_opacity_requested(self, opacity: float) -> None:
        self._set_overlay_state(subtitle_opacity=float(opacity))

    def _saved_overlay_appearance(self) -> dict[str, str]:
        cfg = self._config
        if cfg.subtitle_follow_theme:
            text_color, background_color = (
                self._active_theme.overlay_text,
                self._active_theme.overlay_bg,
            )
        else:
            text_color, background_color = cfg.subtitle_text_color, cfg.subtitle_bg_color
        return {
            "text_color": text_color,
            "background_color": background_color,
            "font_family": cfg.subtitle_font_family,
        }

    def _overlay_colors(self) -> tuple[str, str]:
        if self._chk_subtitle_follow_theme.isChecked():
            return self._active_theme.overlay_text, self._active_theme.overlay_bg
        return self._subtitle_text_color, self._subtitle_bg_color

    def _overlay_appearance(self) -> dict[str, str]:
        text_color, background_color = self._overlay_colors()
        return {
            "text_color": text_color,
            "background_color": background_color,
            "font_family": self._cmb_subtitle_font.currentData() or "",
        }

    def _on_subtitle_follow_toggled(self, _checked: bool) -> None:
        self._refresh_color_buttons()
        self._on_look_changed()

    def _pick_subtitle_color(self, which: Literal["text", "bg"]) -> None:
        current = self._subtitle_text_color if which == "text" else self._subtitle_bg_color
        title = "字幕文字颜色" if which == "text" else "字幕底板颜色"
        color = QColorDialog.getColor(QColor(current), self, title)
        if not color.isValid():
            return
        if which == "text":
            self._subtitle_text_color = color.name().upper()
        else:
            self._subtitle_bg_color = color.name().upper()
        self._refresh_color_buttons()
        self._on_look_changed()

    def _color_swatch(self, color: str) -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(self._active_theme.border))
        painter.setBrush(QColor(color))
        painter.drawEllipse(1, 1, 14, 14)
        painter.end()
        return QIcon(pixmap)

    def _refresh_color_buttons(self) -> None:
        follow = self._chk_subtitle_follow_theme.isChecked()
        text_color, background_color = self._overlay_colors()
        for button, color in (
            (self._btn_text_color, text_color),
            (self._btn_bg_color, background_color),
        ):
            button.setEnabled(not follow)
            button.setIcon(self._color_swatch(color))

    # ------------------------------------------------------------ pipeline
    def _on_channel_toggle(self, channel: Literal["mic", "game"]) -> None:
        """Toggle one independent channel without touching the other."""
        if channel in self._channel_busy:
            self._append_log("通道正在启动/停止，请稍候…")
            return
        direction = Direction.OUTBOUND if channel == "mic" else Direction.INBOUND
        active = self._pipeline is not None and self._pipeline.is_channel_active(direction)
        if active:
            self._stop_one_channel(channel)
        else:
            self._start_one_channel(channel)

    def _ensure_pipeline(self) -> TranslationPipeline:
        if self._pipeline is not None:
            return self._pipeline

        self._pipeline = TranslationPipeline(self._config)
        self._pipeline.status_changed.connect(self._on_status_changed)
        self._pipeline.subtitle_ready.connect(self._on_subtitle_ready)
        self._pipeline.latency_reported.connect(self._on_latency_reported)
        self._pipeline.error_occurred.connect(self._on_error)
        self._pipeline.log_message.connect(self._append_log)
        self._pipeline.channel_lost.connect(self._on_channel_lost)
        self._pipeline.usage_reported.connect(self._on_pipeline_usage)
        self._usage_tracker.reset_session()
        self._pipeline._input_device = self._config.input_device
        self._pipeline._output_device = self._config.output_device
        self._pipeline._loopback_device = self._config.loopback_device
        return self._pipeline

    def _start_one_channel(self, channel: Literal["mic", "game"]) -> None:
        if self._is_dirty():
            reply = QMessageBox.question(
                self,
                "未保存的修改",
                "有未保存的设置（语言/设备/VAD/密钥等以保存后的配置启动）。\n是否保存并启动？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply != QMessageBox.StandardButton.Save:
                return
            if not self._commit_from_ui(persist=True):
                return
            self._append_log("配置已保存，正在启动通道…")

        if self._config.source_language == self._config.target_language:
            QMessageBox.warning(self, "配置错误", "源语言和目标语言不能相同。")
            return

        mode = getattr(self._config, "translation_mode", "volc") or "volc"
        if mode == "economy" and not self._ensure_economy_offline_setup():
            return

        if not self._validate_devices_for_start(channel):
            return

        if channel == "mic":
            self._chk_enable_mic.setChecked(True)
        else:
            self._chk_enable_game.setChecked(True)

        pipeline = self._ensure_pipeline()
        pipeline._config = self._config
        pipeline._input_device = self._config.input_device
        pipeline._output_device = self._config.output_device
        pipeline._loopback_device = self._config.loopback_device

        direction = Direction.OUTBOUND if channel == "mic" else Direction.INBOUND
        play_voice = (
            self._config.play_mic_voice if channel == "mic" else self._config.play_game_voice
        )
        label = "麦克风" if channel == "mic" else "游戏字幕"

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._append_log(f"正在启动{label}通道…")
        self._append_log(
            f"设备: mic={self._config.input_device!r}, "
            f"loopback={self._config.loopback_device!r}, "
            f"out={self._config.output_device!r}"
        )

        def work() -> Callable[[], None]:
            try:
                ok, reason = pipeline.can_start()
                if not ok:
                    raise RuntimeError(reason)
                pipeline.start_channel(direction, play_voice=play_voice)
            except Exception as exc:
                if not pipeline.active_channels():
                    with contextlib.suppress(Exception):
                        pipeline.stop()
                return functools.partial(
                    self._finish_channel_start, channel, pipeline, play_voice, "", exc
                )
            return functools.partial(
                self._finish_channel_start, channel, pipeline, play_voice, reason, None
            )

        self._run_channel_op(channel, work)

    def _run_channel_op(
        self,
        channels: Literal["mic", "game"] | tuple[Literal["mic", "game"], ...],
        work: Callable[[], Callable[[], None]],
    ) -> None:
        """Run ``work`` on the serial channel worker; its result runs on the GUI thread."""
        names = (channels,) if isinstance(channels, str) else channels
        self._channel_busy.update(names)
        self._refresh_channel_buttons()

        def run() -> None:
            try:
                done = work()
            except Exception as exc:
                logger.exception("Channel operation failed")
                done = functools.partial(self._append_log, f"通道操作失败: {exc}")

            def finish() -> None:
                self._channel_busy.subtract(names)
                self._channel_busy = +self._channel_busy
                if self._closing:
                    return
                done()
                self._refresh_channel_buttons()

            if not self._closing:
                self._channel_bridge.finished.emit(finish)

        self._channel_ops.submit(run)

    def _finish_channel_start(
        self,
        channel: Literal["mic", "game"],
        pipeline: TranslationPipeline,
        play_voice: bool,
        reason: str,
        error: Exception | None,
    ) -> None:
        label = "麦克风" if channel == "mic" else "游戏字幕"
        if error is not None:
            self._append_log(f"{label}启动失败: {error}")
            self._show_error_once("启动失败", str(error))
            # A still-queued operation (e.g. the other channel starting) owns this pipeline too.
            if (
                self._pipeline is pipeline
                and not pipeline.active_channels()
                and not self._channel_busy
            ):
                self._pipeline = None
                self._timer.stop()
                self._btn_stop.setEnabled(False)
                self._progress.setVisible(False)
                self._restore_status_label()
            self._on_enable_toggled()
            return
        if not self._timer.isActive():
            self._timer.start()
        self._sync_overlays_visibility(preview=False)
        mode = pipeline.mode
        mode_label = "火山" if mode == "volc" else "经济"
        self._badge.setText(mode_label)
        self._progress.setVisible(False)
        self._update_model_loading_indicator()
        self._restore_status_label()
        self._btn_stop.setEnabled(True)
        self._append_log(f"{label}通道已启动（{mode_label}）")
        if mode == "economy" and reason:
            self._append_log(reason)
        if play_voice:
            out_dev_name = self._resolve_output_device_name()
            self._append_log(f"{label}已开启语音输出 → 输出设备: {out_dev_name}")
        self._on_enable_toggled()

    def _stop_one_channel(self, channel: Literal["mic", "game"]) -> None:
        if self._pipeline is None:
            return
        direction = Direction.OUTBOUND if channel == "mic" else Direction.INBOUND
        label = "麦克风" if channel == "mic" else "游戏字幕"
        # A busy channel may be mid-start; queue the stop behind it.
        if not self._pipeline.is_channel_active(direction) and channel not in self._channel_busy:
            self._append_log(f"{label}通道本来就未运行")
            self._refresh_channel_buttons()
            return

        self._drop_overlay(direction)
        pipeline = self._pipeline

        def work() -> Callable[[], None]:
            pipeline.stop_channel(direction)
            return functools.partial(self._finish_channel_stop, label)

        self._run_channel_op(channel, work)

    def _finish_channel_stop(self, label: str) -> None:
        self._append_log(f"{label}通道已关闭")
        if self._pipeline is None or not self._pipeline.active_channels():
            self._timer.stop()
            self._btn_stop.setEnabled(False)
            self._progress.setVisible(False)
            self._status_label.setText("状态：已停止")
            self._set_latency(None)
            self._badge.setText("Idle")
            self._append_log("全部通道已停止")
        else:
            remaining = []
            if self._pipeline.is_channel_active(Direction.INBOUND):
                remaining.append("游戏")
            if self._pipeline.is_channel_active(Direction.OUTBOUND):
                remaining.append("麦克风")
            self._status_label.setText("状态：运行中")
            self._badge.setText("+".join(remaining))
        self._on_enable_toggled()

    def _on_start(self) -> None:
        # Legacy: start whichever enable checkboxes are on
        if self._chk_enable_mic.isChecked() and (
            self._pipeline is None or not self._pipeline.is_channel_active(Direction.OUTBOUND)
        ):
            self._start_one_channel("mic")
        if self._chk_enable_game.isChecked() and (
            self._pipeline is None or not self._pipeline.is_channel_active(Direction.INBOUND)
        ):
            self._start_one_channel("game")

    def _refresh_channel_buttons(self) -> None:
        mic_on = self._pipeline is not None and self._pipeline.is_channel_active(Direction.OUTBOUND)
        game_on = self._pipeline is not None and self._pipeline.is_channel_active(Direction.INBOUND)

        if mic_on:
            self._btn_mic.setText("■  关闭麦克风")
            self._btn_mic.setObjectName("dangerButton")
        else:
            self._btn_mic.setText("🎙  开启麦克风")
            self._btn_mic.setObjectName("primaryButton")

        if game_on:
            self._btn_game.setText("■  停止字幕")
            self._btn_game.setObjectName("dangerButton")
        else:
            self._btn_game.setText("▶  开始字幕")
            self._btn_game.setObjectName("primaryButton")

        for btn in (self._btn_game, self._btn_mic):
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)
            btn.update()

        if "mic" in self._channel_busy:
            self._btn_mic.setText("…  处理中")
        if "game" in self._channel_busy:
            self._btn_game.setText("…  处理中")
        self._btn_stop.setEnabled(mic_on or game_on)
        self._btn_mic.setEnabled("mic" not in self._channel_busy)
        self._btn_game.setEnabled("game" not in self._channel_busy)

    def _set_running_ui(self, running: bool) -> None:
        # Kept for compatibility; prefer _refresh_channel_buttons
        if not running:
            self._refresh_channel_buttons()
            self._btn_stop.setEnabled(False)
        else:
            self._refresh_channel_buttons()

    def _on_stop(self) -> None:
        """Stop all channels."""
        self._timer.stop()
        pipeline = self._pipeline
        if pipeline is not None:
            pairs: tuple[tuple[Literal["mic", "game"], Direction], ...] = (
                ("mic", Direction.OUTBOUND),
                ("game", Direction.INBOUND),
            )
            for channel, direction in pairs:
                if self._closing:
                    pipeline.stop_channel(direction)
                elif pipeline.is_channel_active(direction) or channel in self._channel_busy:
                    # Queued behind a pending start so that channel ends up stopped too.
                    self._run_channel_op(
                        channel,
                        functools.partial(self._stop_channel_work, pipeline, direction),
                    )
        self._close_overlays()
        self._refresh_channel_buttons()
        self._on_enable_toggled()
        self._progress.setVisible(False)
        self._status_label.setText("状态：已停止")
        self._set_latency(None)
        self._badge.setText("Idle")
        self._append_log("已停止全部通道")

    @staticmethod
    def _stop_channel_work(
        pipeline: TranslationPipeline, direction: Direction
    ) -> Callable[[], None]:
        pipeline.stop_channel(direction)
        return lambda: None

    def _sync_overlays_visibility(self, *, preview: bool = False) -> None:
        wanted: list[Direction] = []
        mic_running = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.OUTBOUND
        )
        game_running = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.INBOUND
        )
        if self._chk_show_mic.isChecked() and (preview or mic_running):
            wanted.append(Direction.OUTBOUND)
        if self._chk_show_game.isChecked() and (preview or game_running):
            wanted.append(Direction.INBOUND)

        for direction in list(self._overlays):
            if direction not in wanted:
                self._drop_overlay(direction)

        for direction in wanted:
            self._ensure_overlay(direction)
        self._apply_overlay_look()
        if not preview:
            return
        for direction in wanted:
            if direction == Direction.OUTBOUND:
                sample = ("你好，能听到吗？", "Hello, can you hear me?")
            else:
                sample = ("Enemy spotted mid.", "中路发现敌人。")
            self._overlays[direction].set_text(*sample, speaker=direction)

    def _start_channels(self, *, mic: bool, game: bool) -> None:
        """Compatibility helper used by older call paths."""
        if mic:
            self._start_one_channel("mic")
        if game:
            self._start_one_channel("game")

    def _on_process_tick(self) -> None:
        if self._pipeline is not None:
            self._pipeline.process_tick()
            self._update_model_loading_indicator()

    def _update_model_loading_indicator(self) -> None:
        """Keep model download/loading visible instead of showing a false 100%."""
        names = self._pipeline.loading_models if self._pipeline is not None else ()
        if names and self._download_status_active:
            self._model_loading_names = names
            return
        if names:
            if names != self._model_loading_names:
                detail = "、".join(names)
                message = f"正在下载或加载模型：{detail}（首次使用需等待，通道可继续运行）"
                self._append_log(message)
                show_toast(message, ms=3500)
            self._model_loading_names = names
            self._progress.setVisible(True)
            self._progress.setRange(0, 0)
            self._status_label.setText(f"状态：正在准备 {'、'.join(names)}")
            return

        if self._model_loading_names:
            self._append_log(f"模型已就绪：{'、'.join(self._model_loading_names)}")
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._progress.setVisible(False)
            self._restore_status_label()
        self._model_loading_names = ()

    def _restore_status_label(self) -> None:
        running = self._pipeline is not None and bool(self._pipeline.active_channels())
        self._status_label.setText("状态：运行中" if running else "状态：空闲")

    def _on_status_changed(self, status: str) -> None:
        mapping = {
            "idle": "空闲",
            "starting": "启动中",
            "running": "运行中",
            "stopped": "已停止",
        }
        label = mapping.get(status, status)
        self._status_label.setText(f"状态：{label}")
        side = getattr(self, "_side_status", None)
        if side is not None:
            side.setText(f"状态：{label}")
        if status == "running":
            self._badge.setText("Running")
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
        elif status == "stopped":
            self._badge.setText("Idle")

    def _on_subtitle_ready(self, entry: SubtitleEntry) -> None:
        if entry.direction == Direction.OUTBOUND:
            self._set_preview(self._mic_preview, entry.original_text, entry.translated_text)
            show = self._chk_show_mic.isChecked()
            if self._dota_coach_armed and entry.is_final and self._chk_dota_coach.isChecked():
                text = (entry.original_text or "").strip()
                if text and text != "…":
                    self._disarm_dota_coach(silent=True)
                    self._send_dota_coach(text)
        else:
            self._set_preview(self._game_preview, entry.original_text, entry.translated_text)
            show = self._chk_show_game.isChecked()

        if not show:
            # Hide overlay for this direction if present
            self._drop_overlay(entry.direction)
            return
        overlay = self._ensure_overlay(entry.direction)
        overlay.set_text(
            entry.original_text,
            entry.translated_text,
            is_final=entry.is_final,
            speaker=entry.direction,
        )

    def _on_dota_coach_hotkey(self) -> None:
        if not self._chk_dota_coach.isChecked():
            show_toast("Dota 教练未启用 — 请在设置里勾选", ms=1800)
            self._append_log("Dota 教练未启用")
            return
        if self._dota_bridge.busy:
            show_toast("教练正在请求中…", ms=1200)
            return
        if self._dota_coach_armed:
            self._disarm_dota_coach()
            show_toast("已取消教练待命", ms=1200)
            self._append_log("Dota 教练待命已取消")
            return
        if self._pipeline is None or not self._pipeline.is_channel_active(Direction.OUTBOUND):
            show_toast("请先开启麦克风通道，再说出问题", ms=2200)
            self._append_log("Dota 教练：麦克风通道未开启")
            return
        seconds = int(getattr(self._config, "dota_coach_arm_seconds", 12) or 12)
        self._dota_coach_armed = True
        self._dota_coach_timer.start(seconds * 1000)
        show_toast(f"教练待命 {seconds}s — 请说话，定稿后自动发送", ms=2200)
        self._append_log(f"Dota 教练待命（{seconds}s）— 等待麦克风定稿原文")

    def _on_dota_coach_timeout(self) -> None:
        if not self._dota_coach_armed:
            return
        self._disarm_dota_coach(silent=True)
        show_toast("教练待命超时，未收到定稿", ms=1800)
        self._append_log("Dota 教练待命超时")

    def _disarm_dota_coach(self, *, silent: bool = False) -> None:
        self._dota_coach_armed = False
        self._dota_coach_timer.stop()
        if not silent:
            return

    def _send_dota_coach(self, text: str) -> None:
        url = self._txt_dota_url.text().strip() or DEFAULT_COACH_URL
        mode = self._cmb_dota_mode.currentData() or "normal"
        show_toast(f"已发送教练：{text[:28]}{'…' if len(text) > 28 else ''}", ms=1600)
        self._append_log(f"Dota 教练提问：{text}")
        ok = self._dota_bridge.ask(text, url=url, mode=str(mode), source="voice")
        if not ok:
            show_toast("发送失败（忙碌或空文本）", ms=1600)

    def _on_dota_coach_finished(self, ok: bool, question: str, payload: str) -> None:
        if ok:
            preview = payload.replace("\n", " / ")
            if len(preview) > 80:
                preview = preview[:80] + "…"
            show_toast(f"教练：{preview}", ms=3200)
            self._append_log(f"Dota 教练回复：{payload}")
        else:
            show_toast(f"教练失败：{payload[:60]}", ms=2800)
            self._append_log(f"Dota 教练失败：{payload}")

    def _on_latency_reported(self, latency_ms: int) -> None:
        self._set_latency(latency_ms)

    def _on_channel_lost(self, direction_value: str, message: str) -> None:
        channel: Literal["mic", "game"] = (
            "mic" if direction_value == Direction.OUTBOUND.value else "game"
        )
        self._on_error(message)
        self._stop_one_channel(channel)

    def _on_error(self, message: str) -> None:
        self._append_log(f"错误: {message}")
        self._show_error_once("运行错误", message)

    def _show_error_once(self, title: str, message: str) -> None:
        """One non-modal box; repeats update it instead of stacking new dialogs."""
        box = self._error_box
        if box is not None and box.isVisible():
            self._error_repeats += 1
            box.setWindowTitle(title)
            box.setText(message)
            box.setInformativeText(f"另有 {self._error_repeats} 条错误，详见运行日志。")
            return
        box = QMessageBox(QMessageBox.Icon.Warning, title, message, parent=self)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.destroyed.connect(self._on_error_box_closed)
        self._error_box = box
        self._error_repeats = 0
        box.show()

    def _on_error_box_closed(self) -> None:
        self._error_box = None

    @staticmethod
    def _fmt_mmss(seconds: float) -> str:
        s = max(0, int(seconds))
        return f"{s // 60:02d}:{s % 60:02d}"

    def _on_music_pick_folder(self) -> None:
        start = str(self._music_folder) if self._music_folder else ""
        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹", start)
        if not folder:
            return
        self._load_music_folder(Path(folder), select_first=True, quiet=False)
        self._update_save_button()
        self._append_log("已打开曲库文件夹，点「保存」后下次启动也会记住")

    def _load_music_folder(
        self,
        folder: Path,
        *,
        select_first: bool = False,
        quiet: bool = False,
    ) -> None:
        tracks = list_audio_files(folder)
        self._music_folder = folder
        self._music_tracks = tracks
        # Shorten display path
        display = str(folder)
        if len(display) > 42:
            display = "…" + display[-40:]
        self._lbl_music_folder.setText(f"{display}（{len(tracks)} 首）")

        self._cmb_music_track.blockSignals(True)
        self._cmb_music_track.clear()
        if not tracks:
            self._cmb_music_track.addItem("（文件夹内无支持的音频）", None)
            self._cmb_music_track.setEnabled(False)
            self._cmb_music_track.blockSignals(False)
            if not quiet:
                QMessageBox.information(
                    self,
                    "音乐分享",
                    "该文件夹没有找到支持的音频。\n支持：mp3 / wav / flac / ogg / m4a 等",
                )
            return

        for path in tracks:
            self._cmb_music_track.addItem(path.name, str(path))
        self._cmb_music_track.setEnabled(True)
        self._cmb_music_track.blockSignals(False)

        if select_first:
            self._cmb_music_track.setCurrentIndex(0)
            self._load_music_index(0, autoplay=False)
        if not quiet:
            self._append_log(f"音乐文件夹：{folder}（{len(tracks)} 首）")

    def _on_music_track_combo(self, index: int) -> None:
        if index < 0:
            return
        was_playing = self._music.is_playing
        self._load_music_index(index, autoplay=was_playing)

    def _load_music_index(self, index: int, *, autoplay: bool = False) -> bool:
        if index < 0 or index >= len(self._music_tracks):
            return False
        path = self._music_tracks[index]
        device = self._cmb_output.currentData()
        self._music_load_cancel.set()
        if self._music_load_future is not None:
            self._music_load_future.cancel()
        cancel_event = threading.Event()
        self._music_load_cancel = cancel_event
        self._music_load_generation += 1
        generation = self._music_load_generation
        self._music_switching = True
        self._music_pending_autoplay = autoplay
        self._btn_music_play.setEnabled(False)
        self._append_log(f"正在加载曲目：{path.name}")

        bridge = self._music_bridge

        def worker() -> None:
            try:
                prepared = prepare_music_track(path, device, cancel_event=cancel_event)
                if cancel_event.is_set():
                    return
                bridge.loaded.emit(generation, index, autoplay, prepared)
            except Exception as exc:
                if cancel_event.is_set():
                    return
                bridge.load_failed.emit(generation, path, str(exc))

        self._music_load_future = self._music_loader.submit(worker)
        return True

    def _on_music_loaded(
        self,
        generation: int,
        index: int,
        autoplay: bool,
        payload: object,
    ) -> None:
        if generation != self._music_load_generation:
            return
        if not isinstance(payload, PreparedMusicTrack):
            self._on_music_load_failed(generation, payload, "后台返回了无效音频数据")
            return
        name, dur = self._music.load_prepared(payload)
        path = payload.path

        self._cmb_music_track.blockSignals(True)
        self._cmb_music_track.setCurrentIndex(index)
        self._cmb_music_track.blockSignals(False)
        self._lbl_music_time.setText(f"00:00 / {self._fmt_mmss(dur)}")
        self._music.set_volume(self._sld_music_vol.value() / 100.0)
        self._music.set_loop(self._chk_music_loop.isChecked())

        if self._music_sidebar is not None:
            self._music_sidebar.set_tracks(self._music_tracks, index)
            self._music_sidebar.set_current_index(index, name=path.stem)

        self._music_switching = False
        self._btn_music_play.setEnabled(True)
        self._append_log(f"已选曲目：{name}")

        should_play = autoplay or self._music_pending_autoplay
        self._music_pending_autoplay = False
        if generation == self._music_hold_load_gen and not self._music_hold_playing:
            should_play = False  # the hold key was released while this track loaded
        if should_play:
            self._start_music_playback(show_conflict_warn=False, quiet=self._music_hold_playing)

    def _on_music_load_failed(self, generation: int, path: object, message: str) -> None:
        if generation != self._music_load_generation:
            return
        self._music_switching = False
        self._music_pending_autoplay = False
        self._btn_music_play.setEnabled(True)
        name = Path(path).name if isinstance(path, (str, Path)) else "音频文件"
        QMessageBox.warning(self, "无法打开音频", f"{name}\n{message}")

    def _ensure_music_sidebar(self) -> MusicSidebarOverlay:
        if self._music_sidebar is None:
            self._music_sidebar = MusicSidebarOverlay(
                on_select=self._on_sidebar_select,
                on_prev=lambda: self._music_step(-1),
                on_next=lambda: self._music_step(1),
                on_pause=self._on_music_pause,
                on_stop=self._on_music_stop,
            )
        self._music_sidebar.set_tracks(
            self._music_tracks,
            self._cmb_music_track.currentIndex(),
        )
        self._music_sidebar.place_right_edge()
        return self._music_sidebar

    def _show_music_sidebar(self) -> None:
        sidebar = self._ensure_music_sidebar()
        idx = self._cmb_music_track.currentIndex()
        if 0 <= idx < len(self._music_tracks):
            sidebar.set_current_index(idx, name=self._music_tracks[idx].stem)
        sidebar.show()
        sidebar.raise_()

    def _hide_music_sidebar(self) -> None:
        if self._music_sidebar is not None:
            self._music_sidebar.hide()

    def _on_sidebar_select(self, index: int) -> None:
        if index == self._cmb_music_track.currentIndex() and self._music.is_playing:
            return
        self._load_music_index(index, autoplay=True)
        show_toast(
            self._music_tracks[index].stem if 0 <= index < len(self._music_tracks) else "切歌"
        )

    def _music_step(self, delta: int) -> None:
        if not self._music_tracks:
            return
        idx = self._cmb_music_track.currentIndex()
        if idx < 0:
            idx = 0
        nxt = (idx + delta) % len(self._music_tracks)
        self._load_music_index(nxt, autoplay=True)
        show_toast(self._music_tracks[nxt].stem)

    def _on_music_volume(self, value: int) -> None:
        self._music.set_volume(value / 100.0)

    def _start_music_playback(
        self, *, show_conflict_warn: bool = True, quiet: bool = False
    ) -> bool:
        if self._music_switching:
            self._music_pending_autoplay = True
            return True
        if not self._music.is_loaded:
            if self._cmb_music_track.currentData():
                return self._load_music_index(self._cmb_music_track.currentIndex(), autoplay=True)
            else:
                QMessageBox.information(self, "音乐分享", "请先选择文件夹和曲目。")
                return False

        out = self._cmb_output.currentData()
        out_name = self._cmb_output.currentText()
        game_capture_on = self._pipeline is not None and self._pipeline.is_channel_active(
            Direction.INBOUND
        )
        if game_capture_on and shares_physical_output_path(
            output_name=out_name,
            output_device=out,
            loopback_name=self._cmb_loopback.currentText(),
            loopback_device=self._cmb_loopback.currentData(),
        ):
            QMessageBox.critical(
                self,
                "音乐回灌风险",
                "音乐输出与游戏字幕捕获共用同一虚拟线或扬声器 Loopback，"
                "会被再次识别。\n\n"
                "请把游戏捕获改成「免驱动（排除本应用）」或把音乐输出到另一只设备。",
            )
            return False
        self._music.set_device(out)
        self._music.set_volume(self._sld_music_vol.value() / 100.0)
        self._music.set_loop(self._chk_music_loop.isChecked())

        mic_voice_on = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.OUTBOUND)
            and self._chk_play_mic.isChecked()
        )
        if show_conflict_warn and mic_voice_on:
            reply = QMessageBox.warning(
                self,
                "声道冲突提醒",
                "麦克风同传的「语音输出」正在运行，会与音乐抢同一输出设备。\n"
                "建议先关掉麦克风语音输出。\n\n仍要播放吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        try:
            self._music.play()
        except Exception as exc:
            QMessageBox.critical(self, "播放失败", str(exc))
            return False

        if quiet:
            # Hold-to-play: no sidebar or toast flashing on every key press.
            return True
        self._show_music_sidebar()
        self._append_log(f"音乐分享播放中 → {out_name}")
        show_toast("音乐分享中")
        return True

    def _on_music_play(self) -> None:
        self._start_music_playback(show_conflict_warn=True)

    # ------------------------------------------------------- hold-to-play
    def _music_hold_fields(self) -> dict[str, Any]:
        return {
            "music_play_mode": self._cmb_music_mode.currentData() or "free",
            "music_hold_key": self._edt_music_hold.combo(),
            "music_hold_delay_ms": self._spn_music_delay.value(),
        }

    def _music_hold_mode(self) -> bool:
        return self._config.music_play_mode == "hold"

    def _update_music_hold_rows(self) -> None:
        hold = self._cmb_music_mode.currentData() == "hold"
        for widget in (
            self._lbl_music_hold,
            self._edt_music_hold,
            self._lbl_music_delay,
            self._spn_music_delay,
        ):
            widget.setVisible(hold)
        if hold and not self._edt_music_hold.combo():
            self._edt_music_hold.setPlaceholderText("点击这里，按下要按住的键")

    def _apply_music_hold(self) -> None:
        """Listen for the saved hold key."""
        hold = self._music_hold_mode()
        combo = self._config.music_hold_key if hold else ""
        if combo != getattr(self, "_music_hold_combo", None):
            self._music_hold_combo = combo
            self._music_hold.set_combo(combo)
        if not hold:
            self._on_music_hold_released()

    def _on_music_hold_settings_changed(self, *_args: Any) -> None:
        self._update_music_hold_rows()
        self._update_save_button()

    def _on_music_hold_pressed(self) -> None:
        if not self._music_hold_mode() or not self._music_tracks:
            return
        self._music_hold_timer.start(self._config.music_hold_delay_ms)

    def _on_music_hold_elapsed(self) -> None:
        index = self._cmb_music_track.currentIndex()
        if not 0 <= index < len(self._music_tracks):
            index = 0
            self._cmb_music_track.blockSignals(True)
            self._cmb_music_track.setCurrentIndex(0)
            self._cmb_music_track.blockSignals(False)
        self._music_hold_playing = True
        # Every hold starts the track over; release stops it.
        self._music.stop()
        if self._music.is_loaded and self._music.path == self._music_tracks[index]:
            self._start_music_playback(show_conflict_warn=False, quiet=True)
        elif self._load_music_index(index, autoplay=True):
            self._music_hold_load_gen = self._music_load_generation

    def _on_music_hold_released(self) -> None:
        self._music_hold_timer.stop()
        if not self._music_hold_playing:
            return
        self._music_hold_playing = False
        self._music_pending_autoplay = False
        self._music.stop()
        dur = self._music.duration_sec
        self._lbl_music_time.setText(f"00:00 / {self._fmt_mmss(dur)}")

    def _on_music_pause(self) -> None:
        if self._music.is_playing:
            self._music.pause()
            self._append_log("音乐已暂停")
            show_toast("音乐已暂停")
        elif self._music.is_loaded:
            try:
                self._music.resume()
                self._show_music_sidebar()
                self._append_log("音乐继续播放")
            except Exception as exc:
                QMessageBox.warning(self, "播放失败", str(exc))

    def _on_music_stop(self) -> None:
        self._music.stop()
        dur = self._music.duration_sec
        self._lbl_music_time.setText(f"00:00 / {self._fmt_mmss(dur)}")
        self._hide_music_sidebar()
        self._append_log("音乐已停止")

    def _on_music_progress(self, pos: float, dur: float) -> None:
        self._lbl_music_time.setText(f"{self._fmt_mmss(pos)} / {self._fmt_mmss(dur)}")

    def _on_music_finished(self) -> None:
        dur = self._music.duration_sec
        self._lbl_music_time.setText(f"{self._fmt_mmss(dur)} / {self._fmt_mmss(dur)}")
        if self._chk_music_loop.isChecked():
            return
        if self._chk_music_auto_next.isChecked() and len(self._music_tracks) > 1:
            self._append_log("自动下一首")
            self._music_step(1)
            return
        self._hide_music_sidebar()
        self._append_log("音乐播放结束")

    @override
    def closeEvent(self, event: QCloseEvent | None) -> None:
        if self._is_dirty():
            choice = self._ask_unsaved("关闭窗口")
            if choice == "cancel":
                if event is not None:
                    event.ignore()
                return
            if choice == "save" and not self._commit_from_ui(persist=True):
                if event is not None:
                    event.ignore()
                return
            # discard: keep self._config as last saved; positions already patched in
        if self._geometry_save_timer.isActive():
            self._geometry_save_timer.stop()
            self._persist_config()
        for timer in (self._dota_coach_timer, self._download_timer, self._dirty_timer):
            timer.stop()
        self._stop_tts_preview()
        self._typed_panel.shutdown()
        self._phrase_translator.close()
        if self._phrase_overlay is not None:
            self._phrase_overlay.close()
        self._music_hold_timer.stop()
        self._music_hold.stop()
        with contextlib.suppress(Exception):
            self._music.stop()
        self._music_load_cancel.set()
        if self._music_load_future is not None:
            self._music_load_future.cancel()
        self._music_loader.shutdown(wait=False, cancel_futures=True)
        self._music_load_generation += 1
        self._hide_music_sidebar()
        if self._music_sidebar is not None:
            self._music_sidebar.close()
            self._music_sidebar = None
        with contextlib.suppress(Exception):
            self._resource_timer.stop()
            if self._resource_monitor is not None:
                self._resource_monitor.stop()
        self._hotkeys.stop()
        self._closing = True
        # A pending Volc connect can take seconds; don't leave a frozen window on screen.
        self.hide()
        self._channel_ops.shutdown(wait=True, cancel_futures=True)
        self._on_stop()
        if self._pipeline is not None:
            with contextlib.suppress(Exception):
                self._pipeline.stop()
            self._pipeline = None
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self._wheel_filter)
        if event is not None:
            event.accept()


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
