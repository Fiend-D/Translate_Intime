"""Dialog to customize global hotkeys."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.hotkey_edit import HotkeyEdit
from src.models.config import HotkeyConfig
from src.utils.hotkeys import (
    DEFAULT_HOTKEYS,
    HOTKEY_LABELS,
    combo_to_display,
    normalize_combo,
)


class HotkeyDialog(QDialog):
    def __init__(self, config: HotkeyConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("全局快捷键")
        self.setMinimumWidth(500)
        self.setMinimumHeight(560)
        self._edits: dict[str, HotkeyEdit] = {}
        self._result: HotkeyConfig | None = None

        root = QVBoxLayout(self)
        root.setSpacing(12)

        tip = QLabel(
            "快捷键在游戏/其他窗口前台时也生效。\n"
            "点击输入框后按下组合键；Esc 清空该项；需包含 Ctrl/Alt/Shift 等修饰键。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("fieldLabel")
        root.addWidget(tip)

        self._chk_enabled = QCheckBox("启用全局快捷键")
        self._chk_enabled.setChecked(config.enabled)
        root.addWidget(self._chk_enabled)

        form = QFormLayout()
        form.setSpacing(10)
        for action, label in HOTKEY_LABELS.items():
            edit = HotkeyEdit()
            edit.set_combo(getattr(config, action, DEFAULT_HOTKEYS.get(action, "")))
            self._edits[action] = edit
            form.addRow(label, edit)
        root.addLayout(form)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset_defaults)
        reset_row.addWidget(btn_reset)
        root.addLayout(reset_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_config(self) -> HotkeyConfig | None:
        return self._result

    def _reset_defaults(self) -> None:
        self._chk_enabled.setChecked(True)
        for action, edit in self._edits.items():
            edit.set_combo(DEFAULT_HOTKEYS.get(action, ""))

    def _on_accept(self) -> None:
        seen: dict[str, str] = {}
        data: dict[str, str] = {}
        for action, edit in self._edits.items():
            combo = normalize_combo(edit.combo())
            data[action] = combo
            if not combo:
                continue
            if combo in seen:
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"{combo_to_display(combo)} 同时绑定了「{HOTKEY_LABELS[seen[combo]]}」"
                    f"和「{HOTKEY_LABELS[action]}」。",
                )
                return
            seen[combo] = action

        self._result = HotkeyConfig(enabled=self._chk_enabled.isChecked(), **data)
        self.accept()
