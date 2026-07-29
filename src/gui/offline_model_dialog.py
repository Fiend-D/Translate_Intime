"""First-run dialog: pick offline NLLB size for economy mode."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from src.engines.pipeline.model_catalog import (
    KOKORO_OPTIONS,
    NLLB_OPTIONS,
    OfflineModelOption,
    format_kokoro_info,
    format_ram_label,
    nllb_option_by_id,
    recommended_nllb_option,
)


class OfflineModelDialog(QDialog):
    """Choose NLLB tier; Kokoro TTS is fixed and shown as info only."""

    def __init__(
        self,
        *,
        current_model_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择经济模式离线模型")
        self.setMinimumWidth(480)
        self._radios: dict[str, QRadioButton] = {}

        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro = QLabel(
            "云端仅 Fun-ASR 识别；翻译与语音在本地。首次需下载模型。\n"
            "请按本机内存选择翻译模型规模（可稍后在设置中更改）。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("fieldLabel")
        root.addWidget(intro)

        section = QLabel("翻译模型（NLLB）")
        section.setObjectName("sectionTitle")
        root.addWidget(section)

        self._group = QButtonGroup(self)
        default_id = recommended_nllb_option().id
        if current_model_id and nllb_option_by_id(current_model_id):
            default_id = current_model_id.strip()

        for idx, opt in enumerate(NLLB_OPTIONS):
            row = self._build_nllb_row(opt)
            root.addWidget(row)
            radio = self._radios[opt.id]
            self._group.addButton(radio, idx)
            if opt.id == default_id:
                radio.setChecked(True)

        if not self._group.checkedButton() and NLLB_OPTIONS:
            self._radios[NLLB_OPTIONS[0].id].setChecked(True)

        kokoro_box = QFrame()
        kokoro_box.setObjectName("card")
        kokoro_layout = QVBoxLayout(kokoro_box)
        kokoro_layout.setContentsMargins(12, 10, 12, 10)
        kokoro_info = QLabel(format_kokoro_info(KOKORO_OPTIONS[0] if KOKORO_OPTIONS else None))
        kokoro_info.setWordWrap(True)
        kokoro_info.setObjectName("fieldLabel")
        kokoro_note = QLabel(
            (KOKORO_OPTIONS[0].note if KOKORO_OPTIONS else "")
            or "本地语音固定使用 Kokoro，无需选择。"
        )
        kokoro_note.setWordWrap(True)
        kokoro_note.setObjectName("appSubtitle")
        kokoro_layout.addWidget(kokoro_info)
        kokoro_layout.addWidget(kokoro_note)
        root.addWidget(kokoro_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("确定")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_nllb_row(self, opt: OfflineModelOption) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        title = opt.title
        if opt.recommended:
            title = f"{opt.title}  ★推荐"
        radio = QRadioButton(title)
        radio.setProperty("model_id", opt.id)
        self._radios[opt.id] = radio
        layout.addWidget(radio)

        ram = format_ram_label(opt.ram_mb).replace("~", "")
        # quality field may be "良好，…"; show first clause for the stats line
        quality_short = opt.quality.split("，", 1)[0].strip() or opt.quality
        stats = QLabel(
            f"下载约 {opt.download_mb}MB · 运行约 {ram} 内存 · 质量：{quality_short}"
        )
        stats.setWordWrap(True)
        stats.setObjectName("fieldLabel")
        stats.setContentsMargins(24, 0, 0, 0)
        layout.addWidget(stats)

        tip = QLabel(opt.note)
        tip.setWordWrap(True)
        tip.setObjectName("appSubtitle")
        tip.setContentsMargins(24, 0, 0, 0)
        layout.addWidget(tip)
        return wrap

    def selected_nllb_model_id(self) -> str:
        checked = self._group.checkedButton()
        if checked is not None:
            mid = checked.property("model_id")
            if isinstance(mid, str) and mid:
                return mid
        for opt_id, radio in self._radios.items():
            if radio.isChecked():
                return opt_id
        return recommended_nllb_option().id
