"""Settings dirty-state regression tests."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from src.gui.settings_dirty import wire_dirty_tracking


class _DirtyHarness(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.text = QLineEdit(self)
        self.combo = QComboBox(self)
        self.combo.addItems(["one", "two"])
        self.check = QCheckBox(self)
        self.spin = QSpinBox(self)
        self.double_spin = QDoubleSpinBox(self)
        self.combo_alias = self.combo
        self.dirty_checks = 0

    def _update_save_button(self) -> None:
        self.dirty_checks += 1


def test_all_settings_editor_types_trigger_dirty_refresh(qtbot) -> None:
    window = _DirtyHarness()
    qtbot.addWidget(window)
    wire_dirty_tracking(window, window._update_save_button)

    window.text.setText("changed")
    window.combo.setCurrentIndex(1)
    window.check.setChecked(True)
    window.spin.setValue(1)
    window.double_spin.setValue(1.0)

    assert window.dirty_checks == 5


def test_duplicate_control_alias_is_only_wired_once(qtbot) -> None:
    window = _DirtyHarness()
    qtbot.addWidget(window)
    wire_dirty_tracking(window, window._update_save_button)

    window.combo.setCurrentIndex(1)

    assert window.dirty_checks == 1
