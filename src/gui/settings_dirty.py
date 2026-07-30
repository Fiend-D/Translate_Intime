"""Helpers for tracking unsaved Qt settings controls."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox


def wire_dirty_tracking(owner: object, callback: Callable[[], None]) -> None:
    """Connect every assigned settings editor to one dirty-state callback."""
    seen: set[int] = set()
    for control in vars(owner).values():
        if id(control) in seen:
            continue
        seen.add(id(control))
        if isinstance(control, QLineEdit):
            control.textChanged.connect(callback)
        elif isinstance(control, QComboBox):
            control.currentIndexChanged.connect(callback)
        elif isinstance(control, QCheckBox):
            control.toggled.connect(callback)
        elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
            control.valueChanged.connect(callback)


__all__ = ["wire_dirty_tracking"]
