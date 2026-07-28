"""SayHey-style dual-channel control panel with dark/light themes."""

from __future__ import annotations

import contextlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, override

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QCloseEvent, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
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
    QTabWidget,
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
    shares_virtual_cable_path,
    validate_channel_devices,
    vb_cable_setup_hint,
)
from src.core.audio_capture import AudioCapture
from src.core.audio_player import AudioPlayer
from src.core.dota_coach import DEFAULT_COACH_URL, DotaCoachBridge
from src.core.music_share import MusicSharePlayer, list_audio_files
from src.core.pipeline import TranslationPipeline
from src.core.usage_tracker import UsageState, UsageTracker
from src.core.volc_engine import VOLC_VOICE_OPTIONS
from src.gui.music_sidebar import MusicSidebarOverlay
from src.gui.styles import ThemeMode, get_stylesheet
from src.gui.subtitle_overlay import SubtitleOverlay
from src.gui.toast import show_toast
from src.models.config import AppConfigModel
from src.models.enums import Direction
from src.models.subtitle import SubtitleEntry
from src.utils.config_manager import load_config, save_config
from src.utils.hotkeys import AppHotkeys, DEFAULT_HOTKEYS, GlobalHotkeys, HOTKEY_LABELS, HotkeyBridge
from src.utils.logger import logger


class _UsageBridge(QObject):
    updated = pyqtSignal(object)  # UsageState


class _MusicBridge(QObject):
    progress = pyqtSignal(float, float)
    finished = pyqtSignal()


class _WheelBlockFilter(QObject):
    """Ignore mouse-wheel on combos/spins/sliders to prevent accidental edits."""

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if obj is None or event is None:
            return False
        if event.type() != QEvent.Type.Wheel:
            return False
        widget: QWidget | None
        if isinstance(obj, QWidget):
            widget = obj
        else:
            return False
        cur: QWidget | None = widget
        while cur is not None:
            if isinstance(cur, (QComboBox, QAbstractSpinBox, QSlider)):
                event.ignore()
                return True
            cur = cur.parentWidget()
        return False


_LANG_LABELS = [
    ("中文 zh", "zh"),
    ("English en", "en"),
    ("日本語 ja", "ja"),
    ("한국어 ko", "ko"),
]


class MainWindow(QMainWindow):
    """Control panel: config only; lyrics overlays carry live subtitles."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Translator InTime")
        self.setMinimumSize(860, 560)
        self.resize(1100, 720)

        self._registry = None
        try:
            self._config = load_config()
        except Exception:
            self._config = AppConfigModel()

        self._theme: ThemeMode = self._config.theme_mode  # type: ignore[assignment]
        self._pipeline: TranslationPipeline | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_process_tick)
        self._timer.setInterval(30)
        self._overlays: dict[Direction, SubtitleOverlay] = {}
        self._corpus_hotwords: list[str] = list(getattr(self._config, "hotwords", None) or [])
        self._corpus_glossary: dict[str, str] = dict(getattr(self._config, "glossary", None) or {})
        self._music_bridge = _MusicBridge(self)
        self._music_bridge.progress.connect(self._on_music_progress)
        self._music_bridge.finished.connect(self._on_music_finished)
        self._music = MusicSharePlayer(
            on_progress=lambda pos, dur: self._music_bridge.progress.emit(pos, dur),
            on_finished=lambda: self._music_bridge.finished.emit(),
        )
        self._music_tracks: list[Path] = []
        self._music_folder: Path | None = None
        self._music_sidebar: MusicSidebarOverlay | None = None
        self._music_switching = False
        self._hotkeys = GlobalHotkeys(HotkeyBridge(self))
        self._hotkeys.bridge.activated.connect(self._on_hotkey)
        self._app_hotkeys: AppHotkeys | None = None
        self._hotkey_guard: dict[str, float] = {}
        self._hotkeys_capturing = False
        self._dota_coach_armed = False
        self._dota_coach_timer = QTimer(self)
        self._dota_coach_timer.setSingleShot(True)
        self._dota_coach_timer.timeout.connect(self._on_dota_coach_timeout)
        self._dota_bridge = DotaCoachBridge(self)
        self._dota_bridge.finished.connect(self._on_dota_coach_finished)

        usage_path = Path(self._config.log_dir).expanduser() / "usage_data.json"
        self._usage_bridge = _UsageBridge(self)
        self._usage_bridge.updated.connect(self._on_usage_state)
        self._usage_tracker = UsageTracker(
            usage_path,
            on_update=lambda st: self._usage_bridge.updated.emit(st),
        )
        self._voice_dialog = None
        self._pack_remain_text = "剩余：未查询"
        self._lang_signals_wired = False
        self._current_tab_index = 0
        self._suppress_tab_guard = False
        self._wheel_filter = _WheelBlockFilter(self)

        self._setup_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._wheel_filter)
        self._app_hotkeys = AppHotkeys(self, self._on_hotkey)
        self._apply_theme()
        self._load_config_into_ui()
        self._apply_hotkeys()
        self._refresh_usage_chip(self._usage_tracker.state)
        self._update_save_button()
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setInterval(700)
        self._dirty_timer.timeout.connect(self._update_save_button)
        self._dirty_timer.start()
        self._append_log("界面已就绪")
        self._append_log(
            "提示：语言/设备/VAD/密钥等需点「保存」后生效；字幕外观立即预览"
        )

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        body = QHBoxLayout()
        body.setSpacing(12)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("mainTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._wrap_scroll(self._build_game_column()), "游戏字幕")
        self._tabs.addTab(self._wrap_scroll(self._build_mic_column()), "麦克风")
        self._tabs.addTab(self._wrap_scroll(self._build_music_column()), "音乐分享")
        self._tabs.addTab(self._wrap_scroll(self._build_settings_panel()), "设置")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        body.addWidget(self._tabs, 1)
        body.addWidget(self._build_log_panel(), 0)
        root.addLayout(body, 1)

        self._populate_devices()
        self._spn_font.valueChanged.connect(self._on_look_changed)
        self._spn_opacity.valueChanged.connect(self._on_look_changed)
        self._spn_history.valueChanged.connect(self._on_look_changed)
        self._chk_show_original.toggled.connect(self._on_look_changed)
        self._chk_show_mic.toggled.connect(self._on_show_overlay_toggled)
        self._chk_show_game.toggled.connect(self._on_show_overlay_toggled)
        self._chk_enable_mic.toggled.connect(self._on_enable_toggled)
        self._chk_enable_game.toggled.connect(self._on_enable_toggled)
        self._on_enable_toggled()
        self._refresh_unlock_button()

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        """Allow panel content to scroll instead of crushing text when short."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(widget)
        return scroll

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("headerBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        row1 = QHBoxLayout()
        title = QLabel("Translator InTime")
        title.setObjectName("appTitle")
        row1.addWidget(title)

        self._badge = QLabel("Idle")
        self._badge.setObjectName("badgeLabel")
        row1.addWidget(self._badge)
        row1.addStretch()

        self._status_label = QLabel("状态：空闲")
        self._status_label.setObjectName("statusLabel")
        row1.addWidget(self._status_label)

        self._latency_label = QLabel("延迟：—")
        self._latency_label.setObjectName("statusLabel")
        row1.addWidget(self._latency_label)

        self._btn_usage = QPushButton("用量 —")
        self._btn_usage.setObjectName("ghostButton")
        self._btn_usage.setToolTip(
            "本地累计 Token/费用（来自 AST UsageResponse）。\n"
            "点击查看明细；设置里填 IAM AK/SK 可查询资源包剩余量。"
        )
        self._btn_usage.clicked.connect(self._on_open_usage)
        row1.addWidget(self._btn_usage)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self._btn_adjust = QPushButton("调整字幕")
        self._btn_adjust.setObjectName("ghostButton")
        self._btn_adjust.setToolTip("打开字幕浮层以便拖拽/缩放；若已锁定会先解锁")
        self._btn_adjust.clicked.connect(self._on_preview_overlays)
        row2.addWidget(self._btn_adjust)

        self._btn_unlock = QPushButton("解锁字幕")
        self._btn_unlock.setObjectName("ghostButton")
        self._btn_unlock.setToolTip(
            "字幕锁定后会点击穿透，无法在浮层上操作。\n"
            "点这里解除锁定，即可再次拖拽/右键菜单。"
        )
        self._btn_unlock.clicked.connect(self._on_unlock_overlays)
        row2.addWidget(self._btn_unlock)

        self._btn_hotkeys = QPushButton("快捷键")
        self._btn_hotkeys.setObjectName("ghostButton")
        self._btn_hotkeys.setToolTip("自定义全局快捷键")
        self._btn_hotkeys.clicked.connect(self._on_edit_hotkeys)
        row2.addWidget(self._btn_hotkeys)

        self._btn_typed = QPushButton("打字翻译")
        self._btn_typed.setObjectName("ghostButton")
        self._btn_typed.setToolTip("文本翻译并朗读（独立通道）")
        self._btn_typed.clicked.connect(self._on_typed_translate)
        row2.addWidget(self._btn_typed)

        self._btn_detect_cable = QPushButton("检测虚拟声卡")
        self._btn_detect_cable.setObjectName("ghostButton")
        self._btn_detect_cable.setToolTip("一键检测 VB-Cable / 虚拟声卡链路是否完整")
        self._btn_detect_cable.clicked.connect(self._on_detect_vb_cable)
        row2.addWidget(self._btn_detect_cable)

        self._chk_advanced = QCheckBox("高级")
        self._chk_advanced.setToolTip("显示译文输出设备、游戏声音捕获等高级选项")
        self._chk_advanced.toggled.connect(self._on_advanced_toggled)
        row2.addWidget(self._chk_advanced)

        self._btn_theme = QPushButton("浅色" if self._theme == "dark" else "深色")
        self._btn_theme.setObjectName("iconButton")
        self._btn_theme.setToolTip("切换明暗主题")
        self._btn_theme.clicked.connect(self._toggle_theme)
        row2.addWidget(self._btn_theme)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("ghostButton")
        self._btn_save.setToolTip(
            "保存设置到本地。\n"
            "需保存后生效：语言、设备、语音输出、VAD/质量档位、原文闪避、"
            "捕获方式、会话轮转、火山密钥/音色/语速、Dota 助手、曲库等。\n"
            "立即生效：字幕字号/透明度/历史上限/显示原文、浮层显隐、主题。"
        )
        self._btn_save.clicked.connect(self._on_save)
        row2.addWidget(self._btn_save)

        version = QLabel("v0.3")
        version.setObjectName("statusLabel")
        row2.addWidget(version)
        row2.addStretch()
        layout.addLayout(row2)
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
        self._sld_music_vol.setValue(70)
        self._sld_music_vol.setToolTip("音量")
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

        note = QLabel(
            "输出设备与麦克风同传共用「译文播放到」。"
            "播音乐时建议关掉麦克风语音输出，避免抢声道。"
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

    def _build_settings_panel(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("通用设置")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        save_tip = QLabel(
            "立即生效：字幕外观（字号/透明度等）、浮层显隐、主题。\n"
            "需点「保存」生效：语言、设备、VAD/质量档位、闪避、捕获、密钥/音色、Dota 等。"
            "未保存时切换标签或关闭窗口会提醒。"
        )
        save_tip.setObjectName("fieldLabel")
        save_tip.setWordWrap(True)
        layout.addWidget(save_tip)

        row = QHBoxLayout()
        row.addWidget(self._field_label("字号"))
        self._spn_font = QSpinBox()
        self._spn_font.setRange(12, 64)
        row.addWidget(self._spn_font)
        row.addWidget(self._field_label("透明度"))
        self._spn_opacity = QDoubleSpinBox()
        self._spn_opacity.setRange(0.3, 1.0)
        self._spn_opacity.setSingleStep(0.05)
        row.addWidget(self._spn_opacity)
        row.addStretch()
        layout.addLayout(row)

        self._chk_use_volc = QCheckBox("火山同传（唯一线路）")
        self._chk_use_volc.setChecked(True)
        self._chk_use_volc.setEnabled(False)
        self._chk_use_volc.setToolTip("本地链路已移除，仅通过火山 AST 2.0 同传")
        layout.addWidget(self._chk_use_volc)

        preset_row = QHBoxLayout()
        preset_row.addWidget(self._field_label("质量档位"))
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
            "低延迟：更短挂起\n"
            "更改后需重新开启通道"
        )
        self._cmb_quality.currentIndexChanged.connect(self._on_quality_preset_changed)
        preset_row.addWidget(self._cmb_quality, 1)
        layout.addLayout(preset_row)

        vad_col = QVBoxLayout()
        vad_col.setSpacing(4)
        self._chk_vad = QCheckBox("麦克风 VAD（减空噪幻觉）")
        self._chk_vad.setChecked(True)
        self._chk_vad.setToolTip(
            "仅作用于麦克风通道。静音/空噪不送火山。\n更改后需重新开启通道。"
        )
        vad_col.addWidget(self._chk_vad)
        self._chk_vad_game = QCheckBox("游戏/视频 VAD（默认关）")
        self._chk_vad_game.setChecked(False)
        self._chk_vad_game.setToolTip(
            "作用于游戏字幕通道。视频/游戏人声容易被误拦，默认关闭。\n"
            "若枪战空噪幻觉严重再打开，并建议灵敏度选「宽松」。"
        )
        vad_col.addWidget(self._chk_vad_game)
        vad_row = QHBoxLayout()
        vad_row.addLayout(vad_col, 1)
        vad_row.addWidget(self._field_label("灵敏度"))
        self._cmb_vad_sens = QComboBox()
        self._prep_combo(self._cmb_vad_sens)
        self._cmb_vad_sens.addItem("宽松", "low")
        self._cmb_vad_sens.addItem("标准", "medium")
        self._cmb_vad_sens.addItem("严格", "high")
        self._cmb_vad_sens.setToolTip(
            "宽松：小声也送（易漏噪）\n"
            "标准：推荐（麦克风）\n"
            "严格：只送明显人声"
        )
        vad_row.addWidget(self._cmb_vad_sens)
        layout.addLayout(vad_row)

        duck_row = QHBoxLayout()
        duck_row.addWidget(self._field_label("原文闪避"))
        self._cmb_original_audio = QComboBox()
        self._prep_combo(self._cmb_original_audio)
        self._cmb_original_audio.addItem("压低其他应用", "duck")
        self._cmb_original_audio.addItem("静音其他应用", "mute")
        self._cmb_original_audio.addItem("不处理（混听）", "mix")
        self._cmb_original_audio.setToolTip(
            "播放译文语音时，对其他应用做闪避（仅 Windows）。\n"
            "需开启麦克风/游戏「语音输出」才生效。"
        )
        duck_row.addWidget(self._cmb_original_audio, 1)
        duck_row.addWidget(self._field_label("压低"))
        self._spn_duck_gain = QDoubleSpinBox()
        self._spn_duck_gain.setRange(0.0, 1.0)
        self._spn_duck_gain.setSingleStep(0.05)
        self._spn_duck_gain.setDecimals(2)
        self._spn_duck_gain.setToolTip("duck 模式下其他应用音量（0=近乎静音，1=不压）")
        duck_row.addWidget(self._spn_duck_gain)
        layout.addLayout(duck_row)

        cap_row = QHBoxLayout()
        cap_row.addWidget(self._field_label("游戏捕获"))
        self._cmb_capture_backend = QComboBox()
        self._prep_combo(self._cmb_capture_backend)
        self._cmb_capture_backend.addItem("自动（优先免驱动）", "auto")
        self._cmb_capture_backend.addItem("免驱动（排除本应用）", "driverless")
        self._cmb_capture_backend.addItem("经典 Loopback", "loopback")
        self._cmb_capture_backend.setToolTip(
            "Windows：免驱动 = process-exclude，译文不会被再识别。\n"
            "自动：可用则免驱动，否则经典 Loopback。\n"
            "Linux 仍使用 monitor。"
        )
        cap_row.addWidget(self._cmb_capture_backend, 1)
        layout.addLayout(cap_row)

        rotate_row = QHBoxLayout()
        rotate_row.addWidget(self._field_label("会话轮转(分)"))
        self._spn_rotate = QSpinBox()
        self._spn_rotate.setRange(0, 120)
        self._spn_rotate.setToolTip("定期重建火山会话；0=关闭。默认 12 分钟。")
        rotate_row.addWidget(self._spn_rotate)
        rotate_row.addStretch()
        layout.addLayout(rotate_row)

        tip_rate = QLabel(
            "提示：「同传语速」只影响合成语音快慢，不改变识别准确度。"
            "译不准多半是口音/源语言/背景音乐，可加热词或换更清晰音源。"
        )
        tip_rate.setObjectName("fieldLabel")
        tip_rate.setWordWrap(True)
        layout.addWidget(tip_rate)

        voice_row = QHBoxLayout()
        voice_row.addWidget(self._field_label("同传音色"))
        self._cmb_volc_voice = QComboBox()
        self._cmb_volc_voice.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        for sid, label in VOLC_VOICE_OPTIONS:
            self._cmb_volc_voice.addItem(label, sid)
        self._cmb_volc_voice.setToolTip(
            "开启「语音输出」后生效。\n"
            "原音色：复刻说话人声音（默认）\n"
            "公版音色：仅目标语为中文/英文时可用\n"
            "更改后需重新开启通道"
        )
        voice_row.addWidget(self._cmb_volc_voice, 1)
        self._btn_pick_voice = QPushButton("音色库…")
        self._btn_pick_voice.setObjectName("ghostButton")
        self._btn_pick_voice.setToolTip("打开豆包 / Seed TTS 2.0 / Qwen 音色选择（可拖动悬浮试听）")
        self._btn_pick_voice.clicked.connect(self._on_open_voice_picker)
        voice_row.addWidget(self._btn_pick_voice)
        layout.addLayout(voice_row)

        rate_row = QHBoxLayout()
        rate_row.addWidget(self._field_label("同传语速"))
        self._spn_speech_rate = QSpinBox()
        self._spn_speech_rate.setRange(-50, 100)
        self._spn_speech_rate.setSingleStep(10)
        self._spn_speech_rate.setToolTip(
            "火山 AST 语速：-50≈0.5x，0=正常，100≈2.0x。更改后需重新开启通道。"
        )
        rate_row.addWidget(self._spn_speech_rate)
        rate_row.addWidget(self._field_label("历史上限"))
        self._spn_history = QSpinBox()
        self._spn_history.setRange(0, 40)
        self._spn_history.setToolTip(
            "字幕浮层最多保留的已定稿句数；实际显示行数随窗口高度自动增减（拉高可回看更多）"
        )
        rate_row.addWidget(self._spn_history)
        rate_row.addStretch()
        layout.addLayout(rate_row)

        corpus_row = QHBoxLayout()
        self._chk_show_original = QCheckBox("浮层显示原文")
        self._chk_show_original.setChecked(True)
        corpus_row.addWidget(self._chk_show_original)
        self._btn_corpus = QPushButton("热词/术语")
        self._btn_corpus.setObjectName("ghostButton")
        self._btn_corpus.clicked.connect(self._on_edit_corpus)
        corpus_row.addWidget(self._btn_corpus)
        self._btn_vb_hint = QPushButton("检测虚拟声卡")
        self._btn_vb_hint.setObjectName("ghostButton")
        self._btn_vb_hint.setToolTip("一键检测 VB-Cable / 虚拟声卡链路")
        self._btn_vb_hint.clicked.connect(self._on_device_guide)
        corpus_row.addWidget(self._btn_vb_hint)
        corpus_row.addStretch()
        layout.addLayout(corpus_row)

        self._lbl_corpus = QLabel("热词 0 / 术语 0")
        self._lbl_corpus.setObjectName("fieldLabel")
        layout.addWidget(self._lbl_corpus)

        self._txt_volc_key = QLineEdit()
        self._txt_volc_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_volc_key.setPlaceholderText("火山 API Key")
        layout.addWidget(self._txt_volc_key)

        self._txt_volc_token = QLineEdit()
        self._txt_volc_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_volc_token.setPlaceholderText("兼容 Token（可选）")
        layout.addWidget(self._txt_volc_token)

        iam_tip = QLabel(
            "查询资源包剩余量需要火山 IAM AccessKey/SecretKey（与语音 API Key 不同）。"
            "可不填；不填时仍显示本地 UsageResponse 累计用量。"
        )
        iam_tip.setObjectName("fieldLabel")
        iam_tip.setWordWrap(True)
        layout.addWidget(iam_tip)
        self._txt_volc_iam_ak = QLineEdit()
        self._txt_volc_iam_ak.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_volc_iam_ak.setPlaceholderText("IAM AccessKey（可选，查剩余额度）")
        layout.addWidget(self._txt_volc_iam_ak)
        self._txt_volc_iam_sk = QLineEdit()
        self._txt_volc_iam_sk.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_volc_iam_sk.setPlaceholderText("IAM SecretKey（可选）")
        layout.addWidget(self._txt_volc_iam_sk)
        self._txt_volc_console_app = QLineEdit()
        self._txt_volc_console_app.setPlaceholderText("控制台 AppID（可选，数字）")
        layout.addWidget(self._txt_volc_console_app)
        pack_row = QHBoxLayout()
        self._lbl_pack_remain = QLabel(self._pack_remain_text)
        self._lbl_pack_remain.setObjectName("fieldLabel")
        self._lbl_pack_remain.setWordWrap(True)
        pack_row.addWidget(self._lbl_pack_remain, 1)
        btn_refresh_pack = QPushButton("刷新额度")
        btn_refresh_pack.setObjectName("ghostButton")
        btn_refresh_pack.clicked.connect(self._on_refresh_pack_quota)
        pack_row.addWidget(btn_refresh_pack)
        layout.addLayout(pack_row)

        layout.addWidget(self._field_label("Dota 助手（语音教练）"))
        coach_row = QHBoxLayout()
        self._chk_dota_coach = QCheckBox("启用")
        self._chk_dota_coach.setChecked(True)
        self._chk_dota_coach.setToolTip(
            "快捷键待命后，把麦克风定稿原文发到本机 Dota Tracker /ai/ask"
        )
        coach_row.addWidget(self._chk_dota_coach)
        self._cmb_dota_mode = QComboBox()
        self._prep_combo(self._cmb_dota_mode)
        self._cmb_dota_mode.addItem("普通", "normal")
        self._cmb_dota_mode.addItem("加速", "turbo")
        self._cmb_dota_mode.setToolTip("影响 AI 时间节奏建议")
        coach_row.addWidget(self._cmb_dota_mode)
        coach_row.addStretch()
        layout.addLayout(coach_row)
        self._txt_dota_url = QLineEdit()
        self._txt_dota_url.setPlaceholderText(DEFAULT_COACH_URL)
        self._txt_dota_url.setToolTip("Dota Tracker 的 /ai/ask 地址")
        layout.addWidget(self._txt_dota_url)
        coach_tip = QLabel(
            "用法：先开麦克风通道 → Ctrl+Alt+C 待命 → 说话等定稿 → 自动发送；再按一次取消。"
        )
        coach_tip.setObjectName("appSubtitle")
        coach_tip.setWordWrap(True)
        layout.addWidget(coach_tip)

        self._btn_stop = QPushButton("停止全部")
        self._btn_stop.setObjectName("dangerButton")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)
        layout.addWidget(self._btn_stop)

        layout.addStretch(1)
        return card

    def _build_log_panel(self) -> QFrame:
        card = self._card()
        card.setObjectName("logSidePanel")
        card.setMinimumWidth(260)
        card.setMaximumWidth(340)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_title = QLabel("运行状态")
        log_title.setObjectName("sectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("ghostButton")
        btn_clear.setToolTip("清空状态日志")
        btn_clear.clicked.connect(self._on_clear_log)
        log_header.addWidget(btn_clear)
        btn_transcript = QPushButton("记录")
        btn_transcript.setObjectName("ghostButton")
        btn_transcript.setToolTip("查看本地翻译留档，支持搜索与导出")
        btn_transcript.clicked.connect(self._on_open_transcripts)
        log_header.addWidget(btn_transcript)
        layout.addLayout(log_header)

        self._side_status = QLabel("空闲")
        self._side_status.setObjectName("fieldLabel")
        self._side_status.setWordWrap(True)
        layout.addWidget(self._side_status)

        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(160)
        layout.addWidget(self._log_view, 1)
        return card

    def _on_clear_log(self) -> None:
        self._log_view.clear()
        self._append_log("日志已清空")

    def _card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return frame

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
        self.setStyleSheet(get_stylesheet(self._theme))
        self._btn_theme.setText("浅色" if self._theme == "dark" else "深色")

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme()
        self._config = self._config.model_copy(update={"theme_mode": self._theme})
        self._persist_config()
        self._btn_theme.setText("浅色" if self._theme == "dark" else "深色")
        self._append_log(f"已切换到{'深色' if self._theme == 'dark' else '浅色'}模式")

    def _append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_view.append(f"[{ts}] {message}")
        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
        side = getattr(self, "_side_status", None)
        if side is not None:
            # Keep latest line visible above the scroll log
            short = message if len(message) <= 80 else message[:77] + "…"
            side.setText(short)

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

        rich_inputs: list[dict[str, Any]] = []
        rich_outputs: list[dict[str, Any]] = []
        try:
            from src.audio.stream import list_audio_devices

            devices = list_audio_devices()
            rich_inputs = list(devices.get("input", []))
            rich_outputs = list(devices.get("output", []))
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
            self._cmb_input.addItem(label, idx)
        for dev in rich_inputs:
            idx = dev.get("id")
            name = str(dev.get("name", ""))
            if idx in seen_mic:
                continue
            if is_virtual_or_loopback_input(name, idx):
                continue
            seen_mic.add(idx)
            self._cmb_input.addItem(format_device_label("mic", name, idx), idx)
        self._cmb_input.setToolTip(role_hint("mic") + "\n" + vb_cable_setup_hint())

        # ---- 译文播放到 ----
        self._cmb_output.clear()
        self._cmb_output.addItem(role_default_label("output"), None)
        seen_out: set[Any] = set()
        preferred_out: list[tuple[Any, str]] = []
        other_out: list[tuple[Any, str]] = []
        for dev in outputs:
            idx = dev["index"]
            if idx in seen_out:
                continue
            seen_out.add(idx)
            label = format_device_label("output", str(dev["name"]), idx)
            if "cable" in str(dev["name"]).lower() and "input" in str(dev["name"]).lower():
                preferred_out.append((idx, label))
            else:
                other_out.append((idx, label))
        for dev in rich_outputs:
            idx = dev.get("id")
            name = str(dev.get("name", ""))
            if idx in seen_out:
                continue
            seen_out.add(idx)
            label = format_device_label("output", name, idx)
            if "cable" in name.lower() and "input" in name.lower():
                preferred_out.append((idx, label))
            else:
                other_out.append((idx, label))
        for idx, label in preferred_out + other_out:
            self._cmb_output.addItem(label, idx)
        self._cmb_output.setToolTip(
            role_hint("output") + "\n队友听翻译：选 CABLE Input（需安装 VB-Cable）"
        )
        # 首次未选输出：优先 CABLE Input
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
        seen_lb: set[Any] = set()
        advanced = bool(getattr(self, "_chk_advanced", None) and self._chk_advanced.isChecked())

        for dev in rich_inputs:
            idx = dev.get("id")
            name = str(dev.get("name", ""))
            if idx in seen_lb:
                continue
            seen_lb.add(idx)
            label = format_device_label("loopback", name, idx)
            if is_vb_cable_capture(name, idx) or is_vb_cable_input(name, idx):
                vb_loop.append((idx, label))
            elif (
                "loopback" in f"{name} {idx}".lower()
                or ".monitor" in f"{name} {idx}".lower()
                or "wasapi_loopback:" in f"{name} {idx}".lower()
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
            label = format_device_label("loopback", name, idx)
            if is_vb_cable_capture(name, idx) or is_vb_cable_input(name, idx):
                vb_loop.append((idx, label))
            else:
                others.append((idx, label))

        for idx, label in preferred:
            self._cmb_loopback.addItem(label, idx)
        for idx, label in others:
            self._cmb_loopback.addItem(label, idx)
        # 虚拟线缆捕获仅高级模式列出，避免误选
        if advanced:
            for idx, label in vb_loop:
                self._cmb_loopback.addItem(label, idx)
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
        for dev in rich_inputs:
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
        for dev in rich_outputs:
            self._device_cache_outputs.append(
                {"name": str(dev.get("name", "")), "index": dev.get("id")}
            )

    # -------------------------------------------------------------- config
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
        running_mic = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.OUTBOUND)
        )
        running_game = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.INBOUND)
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

        self._chk_show_mic.setEnabled(mic_on or running_mic)
        self._chk_show_game.setEnabled(game_on or running_game)
        # Channel buttons always clickable (start/stop that channel)
        self._btn_mic.setEnabled(mic_on or running_mic)
        self._btn_game.setEnabled(game_on or running_game)

    def _on_show_overlay_toggled(self, *_args: Any) -> None:
        if self._pipeline is None:
            # Preview overlays only if already visible
            if self._overlays:
                self._sync_overlays_visibility(preview=True)
            return
        self._sync_overlays_visibility(preview=False)

    def _load_config_into_ui(self) -> None:
        cfg = self._config
        self._theme = cfg.theme_mode  # type: ignore[assignment]
        self._apply_theme()

        self._select_lang(self._cmb_mic_source, cfg.source_language)
        self._select_lang(self._cmb_mic_target, cfg.target_language)
        self._select_lang(self._cmb_game_source, cfg.target_language)
        self._select_lang(self._cmb_game_target, cfg.source_language)

        self._chk_enable_mic.setChecked(cfg.enable_mic)
        self._chk_enable_game.setChecked(cfg.enable_game)
        self._chk_show_mic.setChecked(cfg.show_mic_subtitle)
        self._chk_show_game.setChecked(cfg.show_game_subtitle)
        self._chk_play_mic.setChecked(cfg.play_mic_voice)
        self._chk_play_game.setChecked(cfg.play_game_voice)
        self._spn_font.setValue(cfg.subtitle_font_size)
        self._spn_opacity.setValue(cfg.subtitle_opacity)
        self._chk_use_volc.setChecked(True)
        self._txt_volc_key.setText(cfg.volc_api_key)
        self._txt_volc_token.setText(cfg.volc_access_token)
        self._txt_volc_iam_ak.setText(getattr(cfg, "volc_iam_ak", "") or "")
        self._txt_volc_iam_sk.setText(getattr(cfg, "volc_iam_sk", "") or "")
        self._txt_volc_console_app.setText(getattr(cfg, "volc_console_app_id", "") or "")
        self._chk_vad.setChecked(bool(getattr(cfg, "vad_enabled", True)))
        self._chk_vad_game.setChecked(bool(getattr(cfg, "vad_game_enabled", False)))
        self._select_combo_data(
            self._cmb_vad_sens, getattr(cfg, "vad_sensitivity", "medium") or "medium"
        )
        self._vad_open_ms = int(getattr(cfg, "vad_open_ms", 80) or 80)
        self._vad_hangover_ms = int(getattr(cfg, "vad_hangover_ms", 600) or 600)
        self._cmb_quality.blockSignals(True)
        self._select_combo_data(
            self._cmb_quality, getattr(cfg, "quality_preset", "balanced") or "balanced"
        )
        self._cmb_quality.blockSignals(False)
        self._select_combo_data(
            self._cmb_original_audio, getattr(cfg, "original_audio", "duck") or "duck"
        )
        self._spn_duck_gain.setValue(float(getattr(cfg, "duck_gain", 0.2) or 0.2))
        self._select_combo_data(
            self._cmb_capture_backend, getattr(cfg, "capture_backend", "auto") or "auto"
        )
        self._spn_rotate.setValue(int(getattr(cfg, "volc_session_rotate_minutes", 12) or 0))
        self._chk_dota_coach.setChecked(bool(getattr(cfg, "dota_coach_enabled", True)))
        self._txt_dota_url.setText(getattr(cfg, "dota_coach_url", "") or DEFAULT_COACH_URL)
        self._select_combo_data(
            self._cmb_dota_mode, getattr(cfg, "dota_coach_mode", "normal") or "normal"
        )
        self._select_combo_data(self._cmb_volc_voice, getattr(cfg, "volc_speaker_id", "") or "")
        self._spn_speech_rate.setValue(int(getattr(cfg, "volc_speech_rate", 0) or 0))
        self._spn_history.setValue(int(getattr(cfg, "subtitle_history_lines", 2) or 0))
        self._chk_show_original.setChecked(bool(getattr(cfg, "show_original_in_overlay", True)))
        self._chk_advanced.blockSignals(True)
        self._chk_advanced.setChecked(bool(getattr(cfg, "show_advanced_devices", False)))
        self._chk_advanced.blockSignals(False)
        # Re-enumerate so advanced loopback list matches checkbox
        self._populate_devices()
        self._select_combo_data(self._cmb_input, cfg.input_device)
        if cfg.output_device is not None:
            self._select_combo_data(self._cmb_output, cfg.output_device)
        self._select_combo_data(self._cmb_loopback, cfg.loopback_device)
        self._apply_advanced_visibility()
        self._corpus_hotwords = list(getattr(cfg, "hotwords", None) or [])
        self._corpus_glossary = dict(getattr(cfg, "glossary", None) or {})
        self._refresh_corpus_label()
        self._chk_music_auto_next.setChecked(bool(getattr(cfg, "music_auto_next", True)))
        folder = (getattr(cfg, "music_folder", "") or "").strip()
        if folder:
            self._load_music_folder(Path(folder).expanduser(), select_first=True, quiet=True)

        # Keep language pairs linked both ways (wire once)
        self._syncing_langs = False
        if not self._lang_signals_wired:
            self._cmb_mic_source.currentIndexChanged.connect(self._sync_lang_pairs_from_mic)
            self._cmb_mic_target.currentIndexChanged.connect(self._sync_lang_pairs_from_mic)
            self._cmb_game_source.currentIndexChanged.connect(self._sync_lang_pairs_from_game)
            self._cmb_game_target.currentIndexChanged.connect(self._sync_lang_pairs_from_game)
            self._lang_signals_wired = True
        self._on_enable_toggled()
        self._update_save_button()

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
                "有未保存的修改。\n"
                "需保存后生效：语言、设备、语音输出、VAD/质量档位、原文闪避、"
                "捕获方式、会话轮转、火山密钥/音色/语速、Dota 助手、曲库等。\n"
                "立即生效（仍建议保存以持久化）：字幕外观、浮层显隐。"
            )
        else:
            btn.setToolTip(
                "设置已与本地配置同步。\n"
                "通道相关项保存后，需重新开启通道才会应用到正在运行的会话。"
            )

    def _commit_from_ui(self, *, persist: bool = True) -> bool:
        """UI draft → self._config (+ disk)."""
        try:
            self._config = self._collect_config_from_ui()
            if persist:
                save_config(self._config)
            self._update_save_button()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False

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

    def _on_tab_changed(self, index: int) -> None:
        prev = self._current_tab_index
        if index == prev or self._suppress_tab_guard:
            self._current_tab_index = index
            return
        if not self._is_dirty():
            self._current_tab_index = index
            return

        choice = self._ask_unsaved("切换标签")
        if choice == "save":
            if not self._commit_from_ui(persist=True):
                self._revert_tab(prev)
                return
            self._current_tab_index = index
            self._append_log("配置已保存")
            return
        if choice == "discard":
            self._load_config_into_ui()
            self._current_tab_index = index
            self._append_log("已丢弃未保存的修改")
            return
        self._revert_tab(prev)

    def _revert_tab(self, index: int) -> None:
        self._suppress_tab_guard = True
        self._tabs.setCurrentIndex(index)
        self._suppress_tab_guard = False
        self._current_tab_index = index

    def _collect_config_from_ui(self) -> AppConfigModel:
        return AppConfigModel(
            source_language=self._cmb_mic_source.currentData(),
            target_language=self._cmb_mic_target.currentData(),
            subtitle_font_size=self._spn_font.value(),
            subtitle_opacity=self._spn_opacity.value(),
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
            use_volc=True,
            volc_api_key=self._txt_volc_key.text().strip(),
            volc_access_token=self._txt_volc_token.text().strip(),
            volc_speaker_id=self._cmb_volc_voice.currentData() or "",
            volc_speech_rate=self._spn_speech_rate.value(),
            volc_iam_ak=self._txt_volc_iam_ak.text().strip(),
            volc_iam_sk=self._txt_volc_iam_sk.text().strip(),
            volc_console_app_id=self._txt_volc_console_app.text().strip(),
            hotwords=list(getattr(self, "_corpus_hotwords", None) or []),
            glossary=dict(getattr(self, "_corpus_glossary", None) or {}),
            subtitle_history_lines=self._spn_history.value(),
            show_original_in_overlay=self._chk_show_original.isChecked(),
            overlay_locked=self._config.overlay_locked,
            theme_mode=self._theme,
            hotkeys=self._config.hotkeys,
            music_folder=str(self._music_folder) if self._music_folder else "",
            music_auto_next=self._chk_music_auto_next.isChecked(),
            show_advanced_devices=self._chk_advanced.isChecked(),
            vad_enabled=self._chk_vad.isChecked(),
            vad_game_enabled=self._chk_vad_game.isChecked(),
            vad_sensitivity=self._cmb_vad_sens.currentData() or "medium",
            vad_open_ms=int(getattr(self, "_vad_open_ms", 80) or 80),
            vad_hangover_ms=int(getattr(self, "_vad_hangover_ms", 600) or 600),
            quality_preset=self._cmb_quality.currentData() or "balanced",
            capture_backend=self._cmb_capture_backend.currentData() or "auto",
            original_audio=self._cmb_original_audio.currentData() or "duck",
            duck_gain=float(self._spn_duck_gain.value()),
            volc_session_rotate_minutes=int(self._spn_rotate.value()),
            dota_coach_enabled=self._chk_dota_coach.isChecked(),
            dota_coach_url=self._txt_dota_url.text().strip() or DEFAULT_COACH_URL,
            dota_coach_mode=self._cmb_dota_mode.currentData() or "normal",
            dota_coach_arm_seconds=int(getattr(self._config, "dota_coach_arm_seconds", 12) or 12),
        )

    def _on_quality_preset_changed(self) -> None:
        from src.core.quality_presets import apply_quality_preset

        params = apply_quality_preset(self._cmb_quality.currentData() or "balanced")
        self._chk_vad.setChecked(params.vad_enabled)
        self._chk_vad_game.setChecked(params.vad_game_enabled)
        self._select_combo_data(self._cmb_vad_sens, params.vad_sensitivity)
        self._vad_open_ms = params.vad_open_ms
        self._vad_hangover_ms = params.vad_hangover_ms
        self._append_log(
            f"质量档位 → {self._cmb_quality.currentText()} "
            f"(open={params.vad_open_ms}ms hangover={params.vad_hangover_ms}ms)"
        )

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
        self._config = self._config.model_copy(
            update={
                "hotwords": list(self._corpus_hotwords),
                "glossary": dict(self._corpus_glossary),
            }
        )
        self._persist_config()
        self._append_log(
            f"热词/术语已更新：热词 {len(self._corpus_hotwords)} / 术语 {len(self._corpus_glossary)}"
            "（已保存；重新开启通道后生效）"
        )

    def _on_typed_translate(self) -> None:
        from src.gui.typed_dialog import TypedTranslateDialog
        from src.utils.logger import SubtitleLogger

        dlg = TypedTranslateDialog(
            source=self._cmb_mic_source.currentData() or "zh",
            target=self._cmb_mic_target.currentData() or "en",
            parent=self,
        )

        def _persist(original: str, translated: str) -> None:
            try:
                if self._pipeline is not None:
                    self._pipeline.log_typed_translation(original, translated)
                else:
                    if getattr(self, "_archive_logger", None) is None:
                        self._archive_logger = SubtitleLogger(Path(self._config.log_dir))
                    self._archive_logger.log_typed(original, translated)
                self._append_log("打字翻译已写入留档")
            except Exception as exc:
                logger.warning(f"打字翻译留档失败: {exc}")

        dlg.translated.connect(_persist)
        dlg.exec()

    def _on_open_transcripts(self) -> None:
        from src.gui.transcript_dialog import TranscriptDialog

        log_dir = Path(self._config.log_dir).expanduser()
        dlg = TranscriptDialog(log_dir, parent=self)
        dlg.exec()

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

    def _on_pipeline_usage(self, source: str, payload: dict) -> None:
        try:
            self._usage_tracker.feed_usage_dict(source or "mic", payload)
        except Exception as exc:
            logger.debug(f"usage feed failed: {exc}")

    def _on_refresh_pack_quota(self) -> None:
        from src.core.volc_console import fetch_resource_packs, fetch_quota_monitoring, summarize_quota_payload

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

    def _on_open_voice_picker(self) -> None:
        from src.gui.voice_selector_dialog import VoiceSelectorDialog

        current = self._cmb_volc_voice.currentData() or ""
        if self._voice_dialog is not None:
            try:
                self._voice_dialog.raise_()
                self._voice_dialog.activateWindow()
                return
            except RuntimeError:
                self._voice_dialog = None

        dlg = VoiceSelectorDialog(current_voice=current, parent=self, catalog="doubao")
        self._voice_dialog = dlg

        def _apply(sid: str) -> None:
            # Ensure combo has the option (Seed / Qwen IDs may be new)
            found = False
            for i in range(self._cmb_volc_voice.count()):
                if self._cmb_volc_voice.itemData(i) == sid:
                    self._cmb_volc_voice.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                label = sid or "原音色（复刻）"
                self._cmb_volc_voice.addItem(label, sid)
                self._cmb_volc_voice.setCurrentIndex(self._cmb_volc_voice.count() - 1)
            self._append_log(f"已选择音色：{sid or '原音色复刻'}（请点「保存」后重新开通道）")
            self._update_save_button()

        dlg.voice_selected.connect(_apply)
        dlg.show()

    def _on_device_guide(self) -> None:
        self._on_detect_vb_cable()

    def _on_advanced_toggled(self, checked: bool) -> None:
        mic = self._cmb_input.currentData()
        out = self._cmb_output.currentData()
        lb = self._cmb_loopback.currentData()
        self._apply_advanced_visibility()
        self._populate_devices()
        self._select_combo_data(self._cmb_input, mic)
        if out is not None:
            self._select_combo_data(self._cmb_output, out)
        elif self._config.output_device is None:
            pass  # populate already auto-picked CABLE
        if lb is not None:
            self._select_combo_data(self._cmb_loopback, lb)
        self._config = self._config.model_copy(update={"show_advanced_devices": bool(checked)})
        self._persist_config()
        self._append_log(
            "已开启高级设备选项" if checked else "已隐藏高级设备选项（使用自动推荐）"
        )

    def _apply_advanced_visibility(self) -> None:
        advanced = self._chk_advanced.isChecked()
        if hasattr(self, "_output_row"):
            self._output_row.setVisible(advanced)
        if hasattr(self, "_loopback_row"):
            self._loopback_row.setVisible(advanced)

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
        self._append_log(
            "已自动避让：游戏声音改选系统 Loopback，避免与同传虚拟线冲突"
        )
        show_toast("已自动避开虚拟线冲突")

    def _validate_devices_for_start(self, channel: Literal["mic", "game"]) -> bool:
        self._try_auto_avoid_shared_cable(channel)
        mic_active = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.OUTBOUND)
        )
        game_active = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.INBOUND)
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
            dlg = HotkeyDialog(self._config.hotkeys, self)
            accepted = dlg.exec() == dlg.DialogCode.Accepted
            if accepted:
                result = dlg.result_config()
                if result is not None:
                    self._config = self._config.model_copy(update={"hotkeys": result})
                    self._persist_config()
                    self._append_log("快捷键已更新并保存")
        finally:
            self._hotkeys_capturing = False
            self._apply_hotkeys()

    def _on_hotkey(self, action: str) -> None:
        if getattr(self, "_hotkeys_capturing", False):
            return
        # Debounce double-fire from global + in-window shortcuts
        now = time.monotonic()
        last = self._hotkey_guard.get(action, 0.0)
        if now - last < 0.35:
            return
        self._hotkey_guard[action] = now

        handlers = {
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
        }
        handler = handlers.get(action)
        if handler is None:
            return
        label = HOTKEY_LABELS.get(action, action)
        self._append_log(f"快捷键触发：{label}")
        # Dota 教练有自己的待命/结果 toast，避免叠两层
        if action != "dota_coach_ask":
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
    def _on_geometry_changed(self, direction: Direction, geom: tuple[int, int, int, int]) -> None:
        self._config.subtitle_window_positions[direction.value] = geom
        self._persist_config()

    def _on_lock_changed(self, direction: Direction, locked: bool) -> None:
        self._config.overlay_locked[direction.value] = locked
        label = "麦克风" if direction == Direction.OUTBOUND else "游戏"
        self._append_log(f"{label}字幕已{'锁定（点击穿透）' if locked else '解锁'}")
        self._refresh_unlock_button()
        self._persist_config()

    def _any_overlay_locked(self) -> bool:
        for direction, overlay in self._overlays.items():
            if overlay.is_locked():
                return True
        # Also respect persisted state when overlay not yet created
        for key, locked in (self._config.overlay_locked or {}).items():
            if locked and key in {"outbound", "inbound"}:
                # If overlay exists, already counted; if not, still locked in config
                direction = Direction.OUTBOUND if key == "outbound" else Direction.INBOUND
                if direction not in self._overlays:
                    return True
        return False

    def _refresh_unlock_button(self) -> None:
        btn = getattr(self, "_btn_unlock", None)
        if btn is None:
            return
        locked = self._any_overlay_locked()
        btn.setEnabled(locked)
        btn.setText("解锁字幕" if locked else "字幕未锁")

    def _on_unlock_overlays(self) -> None:
        unlocked = 0
        for direction in (Direction.OUTBOUND, Direction.INBOUND):
            overlay = self._overlays.get(direction)
            if overlay is not None and overlay.is_locked():
                overlay.set_locked(False)
                unlocked += 1
            elif self._config.overlay_locked.get(direction.value, False):
                self._config.overlay_locked[direction.value] = False
                unlocked += 1
        if unlocked:
            self._persist_config()
            self._append_log("字幕浮层已解锁，可拖拽 / 右键菜单")
        else:
            self._append_log("当前没有锁定的字幕浮层")
        self._refresh_unlock_button()

    def _ensure_overlay(self, direction: Direction) -> SubtitleOverlay:
        overlay = self._overlays.get(direction)
        if overlay is not None:
            return overlay
        locked = bool(self._config.overlay_locked.get(direction.value, False))
        overlay = SubtitleOverlay(
            direction,
            font_size=self._spn_font.value(),
            opacity=self._spn_opacity.value(),
            locked=locked,
            history_lines=self._spn_history.value(),
            show_original=self._chk_show_original.isChecked(),
            on_geometry_changed=self._on_geometry_changed,
            on_lock_changed=self._on_lock_changed,
        )
        pos = self._config.subtitle_window_positions.get(direction.value)
        if pos:
            overlay.restore_geometry_tuple(pos)
        overlay.show()
        self._overlays[direction] = overlay
        self._refresh_unlock_button()
        return overlay

    def _close_overlays(self) -> None:
        for overlay in self._overlays.values():
            overlay.close()
        self._overlays.clear()
        self._refresh_unlock_button()

    def _on_preview_overlays(self) -> None:
        # Ensure at least one overlay checkbox is on for preview
        if not self._chk_show_mic.isChecked() and not self._chk_show_game.isChecked():
            self._chk_show_game.setChecked(True)
            self._chk_show_mic.setChecked(True)
        self._sync_overlays_visibility(preview=True)
        # Unlock so user can drag/resize after a previous lock
        for overlay in self._overlays.values():
            if overlay.is_locked():
                overlay.set_locked(False)
        self._refresh_unlock_button()
        self._append_log("已打开字幕浮层，可拖拽 / 缩放 / 双击锁定；锁定后点「解锁字幕」")

    def _on_look_changed(self, *_args: Any) -> None:
        for overlay in self._overlays.values():
            overlay.set_font_size(self._spn_font.value())
            overlay.set_opacity(self._spn_opacity.value())
            overlay.set_history_lines(self._spn_history.value())
            overlay.set_show_original(self._chk_show_original.isChecked())
        self._update_save_button()

    # ------------------------------------------------------------ pipeline
    def _on_channel_toggle(self, channel: Literal["mic", "game"]) -> None:
        """Toggle one independent channel without touching the other."""
        direction = Direction.OUTBOUND if channel == "mic" else Direction.INBOUND
        active = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(direction)
        )
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

        try:
            if not pipeline.wants_volc():
                raise RuntimeError("请先填写火山 API Key 并保存")
            pipeline.start_channel(direction, play_voice=play_voice)
            if not self._timer.isActive():
                self._timer.start()
            self._sync_overlays_visibility(preview=False)
            self._refresh_channel_buttons()
            self._badge.setText("火山")
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._btn_stop.setEnabled(True)
            self._append_log(f"{label}通道已启动（火山）")
            if play_voice:
                self._append_log(f"{label}已开启语音输出")
            self._on_enable_toggled()
        except Exception as exc:
            self._append_log(f"{label}启动失败: {exc}")
            QMessageBox.critical(self, "启动失败", str(exc))
            if self._pipeline is not None and not self._pipeline.active_channels():
                with contextlib.suppress(Exception):
                    self._pipeline.stop()
                self._pipeline = None
                self._timer.stop()
                self._btn_stop.setEnabled(False)
                self._progress.setVisible(False)
            self._refresh_channel_buttons()
            self._on_enable_toggled()

    def _stop_one_channel(self, channel: Literal["mic", "game"]) -> None:
        if self._pipeline is None:
            return
        direction = Direction.OUTBOUND if channel == "mic" else Direction.INBOUND
        label = "麦克风" if channel == "mic" else "游戏字幕"
        if not self._pipeline.is_channel_active(direction):
            self._append_log(f"{label}通道本来就未运行")
            self._refresh_channel_buttons()
            return

        self._pipeline.stop_channel(direction)
        # Close only that overlay
        overlay = self._overlays.pop(direction, None)
        if overlay is not None:
            overlay.close()

        self._append_log(f"{label}通道已关闭")
        self._refresh_channel_buttons()

        if not self._pipeline.active_channels():
            self._timer.stop()
            with contextlib.suppress(Exception):
                self._pipeline.stop()
            self._pipeline = None
            self._btn_stop.setEnabled(False)
            self._progress.setVisible(False)
            self._status_label.setText("状态：已停止")
            self._latency_label.setText("延迟：—")
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
            self._pipeline is None
            or not self._pipeline.is_channel_active(Direction.OUTBOUND)
        ):
            self._start_one_channel("mic")
        if self._chk_enable_game.isChecked() and (
            self._pipeline is None
            or not self._pipeline.is_channel_active(Direction.INBOUND)
        ):
            self._start_one_channel("game")

    def _refresh_channel_buttons(self) -> None:
        mic_on = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.OUTBOUND)
        )
        game_on = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.INBOUND)
        )

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
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()

        self._btn_stop.setEnabled(mic_on or game_on)
        self._btn_mic.setEnabled(True)
        self._btn_game.setEnabled(True)

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
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        self._close_overlays()
        self._refresh_channel_buttons()
        self._on_enable_toggled()
        self._progress.setVisible(False)
        self._status_label.setText("状态：已停止")
        self._latency_label.setText("延迟：—")
        self._badge.setText("Idle")
        self._append_log("已停止全部通道")

    def _sync_overlays_visibility(self, *, preview: bool = False) -> None:
        wanted: list[Direction] = []
        mic_running = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.OUTBOUND)
        )
        game_running = (
            self._pipeline is not None
            and self._pipeline.is_channel_active(Direction.INBOUND)
        )
        if self._chk_show_mic.isChecked() and (preview or mic_running):
            wanted.append(Direction.OUTBOUND)
        if self._chk_show_game.isChecked() and (preview or game_running):
            wanted.append(Direction.INBOUND)

        for direction in list(self._overlays):
            if direction not in wanted:
                self._overlays[direction].close()
                del self._overlays[direction]

        for direction in wanted:
            overlay = self._ensure_overlay(direction)
            overlay.set_font_size(self._spn_font.value())
            overlay.set_opacity(self._spn_opacity.value())
            overlay.set_history_lines(self._spn_history.value())
            overlay.set_show_original(self._chk_show_original.isChecked())
            if preview:
                if direction == Direction.OUTBOUND:
                    overlay.set_text("你好，能听到吗？", "Hello, can you hear me?")
                else:
                    overlay.set_text("Enemy spotted mid.", "中路发现敌人。")

    def _start_channels(self, *, mic: bool, game: bool) -> None:
        """Compatibility helper used by older call paths."""
        if mic:
            self._start_one_channel("mic")
        if game:
            self._start_one_channel("game")

    def _on_process_tick(self) -> None:
        if self._pipeline is not None:
            self._pipeline.process_tick()

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
            if (
                self._dota_coach_armed
                and entry.is_final
                and self._chk_dota_coach.isChecked()
            ):
                text = (entry.original_text or "").strip()
                if text and text != "…":
                    self._disarm_dota_coach(silent=True)
                    self._send_dota_coach(text)
        else:
            self._set_preview(self._game_preview, entry.original_text, entry.translated_text)
            show = self._chk_show_game.isChecked()

        if not show:
            # Hide overlay for this direction if present
            overlay = self._overlays.pop(entry.direction, None)
            if overlay is not None:
                overlay.close()
            return
        overlay = self._ensure_overlay(entry.direction)
        overlay.set_text(
            entry.original_text,
            entry.translated_text,
            is_final=entry.is_final,
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
        self._latency_label.setText(f"延迟：{latency_ms} ms")

    def _on_error(self, message: str) -> None:
        self._append_log(f"错误: {message}")
        QMessageBox.warning(self, "运行错误", message)

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
        self._config = self._config.model_copy(
            update={
                "music_folder": str(self._music_folder) if self._music_folder else "",
                "music_auto_next": self._chk_music_auto_next.isChecked(),
            }
        )
        self._persist_config()
        self._append_log("曲库文件夹已保存")

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
        if index < 0 or self._music_switching:
            return
        was_playing = self._music.is_playing
        self._load_music_index(index, autoplay=was_playing)

    def _load_music_index(self, index: int, *, autoplay: bool = False) -> bool:
        if index < 0 or index >= len(self._music_tracks):
            return False
        path = self._music_tracks[index]
        self._music_switching = True
        try:
            name, dur = self._music.load(path)
        except Exception as exc:
            self._music_switching = False
            QMessageBox.warning(self, "无法打开音频", f"{path.name}\n{exc}")
            return False

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
        self._append_log(f"已选曲目：{name}")

        if autoplay:
            return self._start_music_playback(show_conflict_warn=False)
        return True

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
        show_toast(self._music_tracks[index].stem if 0 <= index < len(self._music_tracks) else "切歌")

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

    def _start_music_playback(self, *, show_conflict_warn: bool = True) -> bool:
        if not self._music.is_loaded:
            if self._cmb_music_track.currentData():
                ok = self._load_music_index(
                    self._cmb_music_track.currentIndex(), autoplay=False
                )
                if not ok:
                    return False
            else:
                QMessageBox.information(self, "音乐分享", "请先选择文件夹和曲目。")
                return False

        out = self._cmb_output.currentData()
        out_name = self._cmb_output.currentText()
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

        self._show_music_sidebar()
        self._append_log(f"音乐分享播放中 → {out_name}")
        show_toast("音乐分享中")
        return True

    def _on_music_play(self) -> None:
        self._start_music_playback(show_conflict_warn=True)

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
            if choice == "save":
                if not self._commit_from_ui(persist=True):
                    if event is not None:
                        event.ignore()
                    return
            # discard: keep self._config as last saved; positions already patched in
        with contextlib.suppress(Exception):
            self._music.stop()
        self._hide_music_sidebar()
        if self._music_sidebar is not None:
            self._music_sidebar.close()
            self._music_sidebar = None
        self._hotkeys.stop()
        self._on_stop()
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
