"""
设置对话框 - 配置 ASR、翻译、TTS、音频设备
"""
import platform

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QLabel, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QGroupBox,
    QFormLayout, QDialogButtonBox, QMessageBox, QScrollArea
)
from PyQt6.QtCore import Qt

from src.utils.config import AppConfig
from src.audio.stream import list_audio_devices


IS_WINDOWS = platform.system() == "Windows"


class SettingsDialog(QDialog):
    """设置对话框"""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置")
        self.setMinimumSize(660, 560)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._create_asr_tab(), "语音识别")
        tabs.addTab(self._create_translation_tab(), "翻译")
        tabs.addTab(self._create_tts_tab(), "语音合成")
        tabs.addTab(self._create_audio_tab(), "音频设备")
        tabs.addTab(self._create_ui_tab(), "界面")
        layout.addWidget(tabs)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_asr_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 16, 12, 12)

        self._asr_backend = QComboBox()
        self._asr_backend.addItems(["auto", "funasr", "whisper", "qwen3"])
        self._asr_backend.setCurrentText(self.config.asr.backend)
        layout.addRow("ASR后端:", self._asr_backend)

        self._asr_local_priority = QComboBox()
        self._asr_local_priority.addItem("Fun-ASR-Nano 优先", "funasr")
        self._asr_local_priority.addItem("Whisper 优先", "whisper")
        self._asr_local_priority.addItem("Qwen3-ASR 优先", "qwen3")
        first_local = (self.config.asr.local_model_priority or ["funasr"])[0]
        self._select_combo_by_data(self._asr_local_priority, first_local)
        layout.addRow("本地模型优先:", self._asr_local_priority)

        self._asr_model = QComboBox()
        self._asr_model.addItems([
            "FunAudioLLM/Fun-ASR-Nano-2512",
            "Qwen/Qwen3-ASR-0.6B",
            "tiny", "base", "small", "medium", "large-v3"
        ])
        self._asr_model.setCurrentText(self.config.asr.model_size)
        if self.config.asr.backend == "funasr":
            self._asr_model.setCurrentText(self.config.asr.funasr_model)
        if self.config.asr.backend == "qwen3":
            self._asr_model.setCurrentText(self.config.asr.qwen3_model)
        layout.addRow("ASR模型:", self._asr_model)

        self._asr_device = QComboBox()
        self._asr_device.addItems(["auto", "cpu", "cuda"])
        layout.addRow("运行设备:", self._asr_device)

        self._asr_compute = QComboBox()
        self._asr_compute.addItems(["auto", "float16", "int8"])
        layout.addRow("计算精度:", self._asr_compute)

        self._asr_lang_src = QComboBox()
        self._asr_lang_src.addItems(["zh", "en", "ja", "ko", "auto"])
        layout.addRow("你的语言:", self._asr_lang_src)

        self._asr_lang_tgt = QComboBox()
        self._asr_lang_tgt.addItems(["en", "zh", "ja", "ko", "auto"])
        layout.addRow("游戏外语:", self._asr_lang_tgt)

        self._asr_beam = QSpinBox()
        self._asr_beam.setRange(1, 10)
        self._asr_beam.setValue(self.config.asr.beam_size)
        layout.addRow("Beam Size:", self._asr_beam)

        self._asr_vad = QCheckBox("启用VAD过滤")
        self._asr_vad.setChecked(self.config.asr.vad_filter)
        layout.addRow(self._asr_vad)

        self._asr_vocab = QLineEdit()
        self._asr_vocab.setPlaceholderText("游戏词汇提示，空格分隔，留空使用默认")
        self._asr_vocab.setText(self.config.asr.gaming_vocabulary)
        layout.addRow("游戏词库:", self._asr_vocab)

        return w

    def _create_translation_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(14)
        layout.setContentsMargins(16, 16, 16, 16)

        # 基础设置用 QFormLayout
        self._translation_basic_group = QGroupBox("基础设置")
        form = QFormLayout(self._translation_basic_group)
        self._configure_form_layout(form)

        self._trans_backend = QComboBox()
        self._trans_backend.addItems([
            "auto", "volc", "aliyun", "hunyuan", "openai", "deepl", "baidu", "microsoft", "google", "local"
        ])
        self._trans_backend.currentTextChanged.connect(self._on_backend_changed)
        form.addRow("翻译后端:", self._trans_backend)

        self._trans_use_cloud = QCheckBox("使用云端模型（火山 AST，关闭后使用本地 ASR+ 文本翻译）")
        self._trans_use_cloud.setChecked(self.config.translation.use_cloud_model)
        form.addRow(self._trans_use_cloud)

        self._trans_hunyuan_path = QLineEdit()
        self._trans_hunyuan_path.setPlaceholderText("留空则自动搜索 ./models 目录")
        self._trans_hunyuan_path.setText(self.config.translation.hunyuan_model_path)
        form.addRow("腾讯混元模型路径:", self._trans_hunyuan_path)

        self._trans_src = QComboBox()
        self._trans_src.addItems(["zh", "en", "ja", "ko", "fr", "de", "es", "ru"])
        form.addRow("源语言:", self._trans_src)

        self._trans_tgt = QComboBox()
        self._trans_tgt.addItems(["en", "zh", "ja", "ko", "fr", "de", "es", "ru"])
        form.addRow("目标语言:", self._trans_tgt)

        self._trans_timeout = QSpinBox()
        self._trans_timeout.setRange(1, 30)
        self._trans_timeout.setValue(self.config.translation.timeout)
        self._trans_timeout.setSuffix(" 秒")
        form.addRow("翻译超时:", self._trans_timeout)

        self._trans_proxy = QLineEdit()
        self._trans_proxy.setPlaceholderText("如 http://127.0.0.1:7890")
        self._trans_proxy.setText(self.config.translation.proxy)
        form.addRow("代理地址:", self._trans_proxy)
        layout.addWidget(self._translation_basic_group)

        # OpenAI
        self._openai_group = QGroupBox("OpenAI (付费，质量最高)")
        ol = QFormLayout(self._openai_group)
        self._configure_form_layout(ol)
        self._openai_key = QLineEdit()
        self._openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._openai_key.setPlaceholderText("sk-... (留空使用环境变量 OPENAI_API_KEY)")
        ol.addRow("API Key:", self._openai_key)
        self._openai_model = QComboBox()
        self._openai_model.addItems(["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo", "qwen2.5:7b"])
        self._openai_model.setEditable(True)
        ol.addRow("模型:", self._openai_model)
        self._openai_url = QLineEdit()
        self._openai_url.setPlaceholderText("https://api.openai.com/v1")
        ol.addRow("Base URL:", self._openai_url)
        layout.addWidget(self._openai_group)

        # DeepL 设置
        self._deepl_group = QGroupBox("DeepL (付费，翻译质量高)")
        dl = QFormLayout(self._deepl_group)
        self._configure_form_layout(dl)
        self._deepl_key = QLineEdit()
        self._deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._deepl_key.setPlaceholderText("DeepL API Key (留空使用环境变量 DEEPL_API_KEY)")
        dl.addRow("API Key:", self._deepl_key)
        layout.addWidget(self._deepl_group)

        # 百度翻译设置
        self._baidu_group = QGroupBox("百度翻译 (免费，国内首选)")
        bl = QFormLayout(self._baidu_group)
        self._configure_form_layout(bl)
        self._baidu_app_id = QLineEdit()
        self._baidu_app_id.setPlaceholderText("百度翻译开放平台免费注册")
        bl.addRow("APP ID:", self._baidu_app_id)
        self._baidu_secret = QLineEdit()
        self._baidu_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._baidu_secret.setPlaceholderText("密钥(Secret Key)")
        bl.addRow("Secret Key:", self._baidu_secret)
        layout.addWidget(self._baidu_group)

        # Ollama 设置
        self._ollama_group = QGroupBox("本地 Ollama (完全离线，免费)")
        oll = QFormLayout(self._ollama_group)
        self._configure_form_layout(oll)
        self._ollama_model = QLineEdit()
        self._ollama_model.setPlaceholderText("qwen2.5:7b")
        oll.addRow("模型名:", self._ollama_model)
        self._ollama_url = QLineEdit()
        self._ollama_url.setPlaceholderText("http://localhost:11434/v1")
        oll.addRow("API地址:", self._ollama_url)
        layout.addWidget(self._ollama_group)

        # 火山引擎设置
        self._volc_group = QGroupBox("火山引擎 AST 2.0 (国内直连，免费额度)")
        vl = QFormLayout(self._volc_group)
        self._configure_form_layout(vl)
        self._volc_app_id = QLineEdit()
        self._volc_app_id.setPlaceholderText("火山引擎控制台 API Key，例如 VOLC_APP_KEY")
        vl.addRow("API Key:", self._volc_app_id)
        self._volc_token = QLineEdit()
        self._volc_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._volc_token.setPlaceholderText("兼容旧配置，可留空")
        vl.addRow("兼容 Token:", self._volc_token)
        volc_tip = QLabel(
            "注册: console.volcengine.com -> 豆包语音 -> 同声传译 2.0\n"
            "   新版控制台请填写 API Key；旧的 APP ID 不能用于此接口"
        )
        volc_tip.setWordWrap(True)
        volc_tip.setObjectName("tipLabel")
        vl.addRow(volc_tip)
        layout.addWidget(self._volc_group)

        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        return w

    @staticmethod
    def _configure_form_layout(layout: QFormLayout) -> None:
        """统一设置翻译配置表单间距，避免输入框在小窗口里挤在一起。"""
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(12)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _create_tts_tab(self) -> QWidget:
        """TTS设置页"""
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 16, 12, 12)

        self._tts_backend = QComboBox()
        self._tts_backend.addItems(["edge-tts", "openai"])
        layout.addRow("TTS后端:", self._tts_backend)

        self._tts_voice_cn = QLineEdit()
        self._tts_voice_cn.setText(self.config.tts.voice)
        layout.addRow("中文声音:", self._tts_voice_cn)

        self._tts_voice_en = QLineEdit()
        self._tts_voice_en.setText(self.config.tts.target_voice)
        layout.addRow("英文声音:", self._tts_voice_en)

        self._tts_rate = QLineEdit()
        self._tts_rate.setText(self.config.tts.rate)
        layout.addRow("语速:", self._tts_rate)

        self._tts_volume = QLineEdit()
        self._tts_volume.setText(self.config.tts.volume)
        layout.addRow("音量:", self._tts_volume)

        return w

    def _create_audio_tab(self) -> QWidget:
        """音频设备设置页"""
        w = QWidget()
        layout = QFormLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 16, 12, 12)

        devices = list_audio_devices()

        # 麦克风
        self._audio_mic = QComboBox()
        self._audio_mic.addItem("系统默认", None)
        for dev in devices["input"]:
            self._audio_mic.addItem(f"[Mic] {dev['name']}", dev["id"])
        layout.addRow("麦克风 (你说中文):", self._audio_mic)

        # 游戏声音捕获
        self._audio_game = QComboBox()
        self._audio_game.addItem("系统默认", None)
        for dev in devices["input"]:
            if dev["input_channels"] > 0:
                self._audio_game.addItem(f"[Game] {dev['name']}", dev["id"])
        if not IS_WINDOWS:
            self._audio_game.addItem("[Virtual] translator_virtual_sink.monitor",
                                     "translator_virtual_sink.monitor")
        layout.addRow("游戏声音捕获:", self._audio_game)

        # TTS 输出设备
        self._audio_tts_out = QComboBox()
        self._audio_tts_out.addItem("系统默认扬声器（测试用）", None)
        for dev in devices["output"]:
            self._audio_tts_out.addItem(f"[Out] {dev['name']}", dev["id"])
        if not IS_WINDOWS:
            self._audio_tts_out.addItem("[Virtual] translator_virtual_sink (输出到游戏麦克风)",
                                        "translator_virtual_sink")
        layout.addRow("TTS输出设备:", self._audio_tts_out)

        btn_refresh = QPushButton("刷新设备列表")
        btn_refresh.clicked.connect(self._refresh_devices)
        layout.addRow(btn_refresh)

        tip = QLabel(
            "测试时选「系统默认扬声器」可直接听到翻译语音。\n"
            "正式使用时选「Virtual」将语音送入游戏麦克风。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("tipLabel")
        layout.addRow(tip)

        return w

    def _create_ui_tab(self) -> QWidget:
        """界面设置页"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(14)
        layout.setContentsMargins(12, 16, 12, 12)

        # 基础设置
        basic_group = QGroupBox("基础设置")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setSpacing(8)
        basic_layout.setContentsMargins(14, 14, 14, 14)

        self._ui_font_size = QSpinBox()
        self._ui_font_size.setRange(8, 24)
        self._ui_font_size.setValue(self.config.ui.font_size)
        basic_layout.addRow("字体大小:", self._ui_font_size)

        self._ui_always_top = QCheckBox("窗口置顶")
        self._ui_always_top.setChecked(self.config.ui.always_on_top)
        basic_layout.addRow(self._ui_always_top)

        self._ui_subtitle_opacity = QDoubleSpinBox()
        self._ui_subtitle_opacity.setRange(0.1, 1.0)
        self._ui_subtitle_opacity.setSingleStep(0.05)
        self._ui_subtitle_opacity.setValue(self.config.ui.subtitle_opacity)
        basic_layout.addRow("字幕透明度:", self._ui_subtitle_opacity)

        layout.addWidget(basic_group)

        # 悬浮字幕设置
        overlay_group = QGroupBox("悬浮字幕")
        overlay_layout = QFormLayout(overlay_group)
        overlay_layout.setSpacing(8)
        overlay_layout.setContentsMargins(14, 14, 14, 14)

        self._ui_show_game_subtitle = QCheckBox("显示游戏语音翻译悬浮窗")
        self._ui_show_game_subtitle.setChecked(self.config.ui.show_game_subtitle)
        overlay_layout.addRow(self._ui_show_game_subtitle)

        self._ui_show_mic_subtitle = QCheckBox("显示麦克风输入悬浮窗")
        self._ui_show_mic_subtitle.setChecked(self.config.ui.show_mic_subtitle)
        overlay_layout.addRow(self._ui_show_mic_subtitle)

        self._ui_subtitle_lines = QSpinBox()
        self._ui_subtitle_lines.setRange(2, 10)
        self._ui_subtitle_lines.setValue(min(self.config.ui.max_subtitle_lines, 10))
        overlay_layout.addRow("悬浮窗最大行数:", self._ui_subtitle_lines)

        layout.addWidget(overlay_group)

        # 语音输出设置
        tts_group = QGroupBox("语音输出")
        tts_layout = QFormLayout(tts_group)
        tts_layout.setSpacing(8)
        tts_layout.setContentsMargins(14, 14, 14, 14)

        self._ui_play_chinese = QCheckBox("播放游戏语音翻译（中文）")
        self._ui_play_chinese.setChecked(self.config.ui.play_chinese_voice)
        tts_layout.addRow(self._ui_play_chinese)

        self._ui_play_outbound = QCheckBox("播放麦克风翻译输出（外语）")
        self._ui_play_outbound.setChecked(self.config.ui.play_outbound_voice)
        tts_layout.addRow(self._ui_play_outbound)

        layout.addWidget(tts_group)

        return w

    def _on_backend_changed(self, backend: str) -> None:
        """翻译后端切换时显示/隐藏相关设置组"""
        self._openai_group.setVisible(backend in ("openai", "auto"))
        self._deepl_group.setVisible(backend in ("deepl", "auto"))
        self._baidu_group.setVisible(backend in ("baidu", "auto"))
        self._ollama_group.setVisible(backend in ("local", "auto"))
        self._volc_group.setVisible(backend in ("volc", "auto"))

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, value) -> None:
        """根据 data 值选中 ComboBox 项"""
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _refresh_devices(self) -> None:
        """刷新音频设备列表"""
        devices = list_audio_devices()

        # 保存当前选择
        mic_val = self._audio_mic.currentData()
        game_val = self._audio_game.currentData()
        tts_val = self._audio_tts_out.currentData()

        for combo, dev_list, kind, prefix in [
            (self._audio_mic, devices["input"], "input", "[Mic]"),
            (self._audio_game, devices["input"], "input", "[Game]"),
            (self._audio_tts_out, devices["output"], "output", "[Out]"),
        ]:
            combo.clear()
            combo.addItem("系统默认", None)
            for dev in dev_list:
                combo.addItem(f"{prefix} {dev['name']}", dev["id"])

        # 恢复 PulseAudio 别名
        self._audio_game.addItem("[Virtual] translator_virtual_sink.monitor",
                                 "translator_virtual_sink.monitor")
        self._audio_tts_out.addItem("[Virtual] translator_virtual_sink (输出到游戏麦克风)",
                                    "translator_virtual_sink")

        # 恢复选择
        self._select_combo_by_data(self._audio_mic, mic_val)
        self._select_combo_by_data(self._audio_game, game_val)
        self._select_combo_by_data(self._audio_tts_out, tts_val)

    def _load_config(self) -> None:
        """从配置加载到UI"""
        # ASR
        self._asr_backend.setCurrentText(self.config.asr.backend)
        first_local = (self.config.asr.local_model_priority or ["funasr"])[0]
        self._select_combo_by_data(self._asr_local_priority, first_local)
        self._asr_model.setCurrentText(self.config.asr.model_size)
        if self.config.asr.backend == "funasr":
            self._asr_model.setCurrentText(self.config.asr.funasr_model)
        self._asr_device.setCurrentText(self.config.asr.device)
        self._asr_compute.setCurrentText(self.config.asr.compute_type)
        self._asr_lang_src.setCurrentText(self.config.asr.source_language)
        self._asr_lang_tgt.setCurrentText(self.config.asr.target_language)
        self._asr_beam.setValue(self.config.asr.beam_size)
        self._asr_vad.setChecked(self.config.asr.vad_filter)

        # Translation
        self._trans_backend.setCurrentText(self.config.translation.backend)
        self._trans_use_cloud.setChecked(self.config.translation.use_cloud_model)
        self._trans_src.setCurrentText(self.config.translation.source_lang)
        self._trans_tgt.setCurrentText(self.config.translation.target_lang)
        self._trans_timeout.setValue(self.config.translation.timeout)
        self._trans_proxy.setText(self.config.translation.proxy)
        self._openai_key.setText(self.config.translation.openai_api_key)
        self._openai_model.setCurrentText(self.config.translation.openai_model)
        self._openai_url.setText(self.config.translation.openai_base_url)
        self._deepl_key.setText(self.config.translation.deepl_api_key)
        self._baidu_app_id.setText(self.config.translation.baidu_app_id)
        self._baidu_secret.setText(self.config.translation.baidu_secret_key)
        self._ollama_model.setText(self.config.translation.ollama_model)
        self._ollama_url.setText(self.config.translation.ollama_base_url)
        self._volc_app_id.setText(self.config.translation.volc_app_id)
        self._volc_token.setText(self.config.translation.volc_access_token)

        self._on_backend_changed(self.config.translation.backend)

        # Audio — 恢复上次选择的设备
        self._select_combo_by_data(self._audio_mic, self.config.audio.input_device)
        self._select_combo_by_data(self._audio_game, self.config.audio.game_output_device)
        self._select_combo_by_data(self._audio_tts_out, self.config.audio.output_device)

    def _on_save(self) -> None:
        """保存配置"""
        try:
            from src.utils.config import ConfigManager
            mgr = ConfigManager()

            # 保存 ASR 配置
            asr_backend = self._asr_backend.currentText()
            asr_model = self._asr_model.currentText()
            first_local = self._asr_local_priority.currentData()
            if first_local == "funasr":
                local_priority = ["funasr", "whisper", "qwen3"]
            elif first_local == "whisper":
                local_priority = ["whisper", "funasr", "qwen3"]
            else:
                local_priority = ["qwen3", "funasr", "whisper"]
            whisper_models = {"tiny", "base", "small", "medium", "large-v3"}
            funasr_models = {"FunAudioLLM/Fun-ASR-Nano-2512"}
            qwen3_models = {"Qwen/Qwen3-ASR-0.6B"}
            mgr.update("asr",
                backend=asr_backend,
                local_model_priority=local_priority,
                model_size=asr_model if asr_model in whisper_models else self.config.asr.model_size,
                funasr_model=asr_model if asr_model in funasr_models else self.config.asr.funasr_model,
                qwen3_model=asr_model if asr_model in qwen3_models else self.config.asr.qwen3_model,
                device=self._asr_device.currentText(),
                compute_type=self._asr_compute.currentText(),
                source_language=self._asr_lang_src.currentText(),
                target_language=self._asr_lang_tgt.currentText(),
                beam_size=self._asr_beam.value(),
                vad_filter=self._asr_vad.isChecked(),
                gaming_vocabulary=self._asr_vocab.text(),
            )

            # 保存翻译配置
            mgr.update("translation",
                backend=self._trans_backend.currentText(),
                use_cloud_model=self._trans_use_cloud.isChecked(),
                source_lang=self._trans_src.currentText(),
                target_lang=self._trans_tgt.currentText(),
                timeout=self._trans_timeout.value(),
                proxy=self._trans_proxy.text(),
                openai_api_key=self._openai_key.text(),
                openai_model=self._openai_model.currentText(),
                openai_base_url=self._openai_url.text(),
                deepl_api_key=self._deepl_key.text(),
                baidu_app_id=self._baidu_app_id.text(),
                baidu_secret_key=self._baidu_secret.text(),
                ollama_model=self._ollama_model.text(),
                ollama_base_url=self._ollama_url.text(),
                volc_app_id=self._volc_app_id.text(),
                volc_access_token=self._volc_token.text(),
                hunyuan_model_path=self._trans_hunyuan_path.text(),
            )

            # 保存 TTS 配置
            mgr.update("tts",
                backend=self._tts_backend.currentText(),
                voice=self._tts_voice_cn.text(),
                target_voice=self._tts_voice_en.text(),
                rate=self._tts_rate.text(),
                volume=self._tts_volume.text(),
            )

            # 保存 UI 配置
            mgr.update("ui",
                font_size=self._ui_font_size.value(),
                always_on_top=self._ui_always_top.isChecked(),
                max_subtitle_lines=self._ui_subtitle_lines.value(),
                play_chinese_voice=self._ui_play_chinese.isChecked(),
                play_outbound_voice=self._ui_play_outbound.isChecked(),
                subtitle_opacity=self._ui_subtitle_opacity.value(),
                show_game_subtitle=self._ui_show_game_subtitle.isChecked(),
                show_mic_subtitle=self._ui_show_mic_subtitle.isChecked(),
            )

            # 保存音频设备选择
            mgr.update("audio",
                input_device=self._audio_mic.currentData(),
                game_output_device=self._audio_game.currentData(),
                output_device=self._audio_tts_out.currentData(),
            )

            QMessageBox.information(self, "设置已保存", "配置已保存，重启翻译后生效。")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存配置时出错: {e}")
