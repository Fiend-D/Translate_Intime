"""Main-window page for managing quick phrases."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.models.config import PHRASE_MAX_CHARS, QuickPhrase, QuickPhraseGroup, QuickPhraseSettings

_ID_ROLE = Qt.ItemDataRole.UserRole


def parse_phrase_lines(text: str) -> list[QuickPhrase]:
    """One phrase per line; ``原文<TAB>译文`` keeps a saved translation."""
    phrases: list[QuickPhrase] = []
    for line in text.splitlines():
        original, _, translation = line.partition("\t")
        if original.strip():
            phrases.append(QuickPhrase(text=original, translation=translation))
    return phrases


def format_phrase_lines(phrases: list[QuickPhrase]) -> str:
    lines = [f"{p.text}\t{p.translation}" if p.translation else p.text for p in phrases]
    return "\n".join(lines) + ("\n" if lines else "")


class PhraseEditDialog(QDialog):
    def __init__(
        self, phrase: QuickPhrase, groups: list[str], group: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑常用语")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self._text = QLineEdit(phrase.text)
        self._text.setMaxLength(PHRASE_MAX_CHARS)
        self._translation = QLineEdit(phrase.translation)
        self._translation.setPlaceholderText("留空则自动翻译")
        self._group = QComboBox()
        self._group.addItems(groups)
        self._group.setCurrentText(group)
        self._enter = QCheckBox("输入后自动按回车发送")
        self._enter.setChecked(phrase.auto_enter)
        form.addRow("原文", self._text)
        form.addRow("译文", self._translation)
        form.addRow("分组", self._group)
        form.addRow("", self._enter)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, str, bool]:
        return (
            self._text.text().strip(),
            self._translation.text().strip(),
            self._group.currentText(),
            self._enter.isChecked(),
        )


class PhrasePanel(QWidget):
    """Edits a copy of the phrase settings and reports every change."""

    settings_changed = pyqtSignal(object)  # QuickPhraseSettings
    # Showing the overlay is immediate, unlike the other settings that wait for 保存
    overlay_toggled = pyqtSignal(bool)
    retranslate_requested = pyqtSignal(list)  # phrase ids

    def __init__(self, settings: QuickPhraseSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings.model_copy(deep=True)
        self._target = ""
        self._status: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        tip = QLabel(
            "添加常用语后，在悬浮窗里点一下就会输入到当前聚焦的输入框（例如游戏聊天框）。"
            "开启翻译时输入的是译文，开启双语则原文和译文各占一行一起输入。"
            "点击一行即可选中，带 ⏎ 的常用语输入后会自动回车发送。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("appSubtitle")
        root.addWidget(tip)

        add_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setMaxLength(PHRASE_MAX_CHARS)
        self._input.setPlaceholderText(f"输入一句常用语（最多 {PHRASE_MAX_CHARS} 字），回车添加")
        self._input.textChanged.connect(self._update_counter)
        self._input.returnPressed.connect(self._on_add)
        self._counter = QLabel()
        self._counter.setObjectName("appSubtitle")
        self._btn_add = QPushButton("添加")
        self._btn_add.setObjectName("primaryButton")
        self._btn_add.clicked.connect(self._on_add)
        add_row.addWidget(self._input, 1)
        add_row.addWidget(self._counter)
        add_row.addWidget(self._btn_add)
        root.addLayout(add_row)

        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("分组"))
        self._cmb_group = QComboBox()
        self._cmb_group.currentIndexChanged.connect(self._on_group_selected)
        group_row.addWidget(self._cmb_group, 1)
        for text, slot in (
            ("新建分组", self._on_new_group),
            ("重命名", self._on_rename_group),
            ("删除分组", self._on_delete_group),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            group_row.addWidget(button)
        root.addLayout(group_row)

        option_row = QHBoxLayout()
        self._chk_translate = QCheckBox()
        self._chk_translate.toggled.connect(self._on_translate_toggled)
        self._chk_overlay = QCheckBox("显示悬浮窗")
        self._chk_overlay.setToolTip("悬浮窗不抢焦点：点击常用语时游戏输入框保持选中")
        self._chk_overlay.toggled.connect(self._on_overlay_toggled)
        self._chk_bilingual = QCheckBox("双语发送")
        self._chk_bilingual.setToolTip(
            "同时输入原文和译文，中间换行（原文在上）。\n"
            "部分游戏的聊天框是单行的，换行可能被吞掉或在换行处直接发出"
        )
        self._chk_bilingual.toggled.connect(self._on_bilingual_toggled)
        option_row.addWidget(self._chk_translate)
        option_row.addWidget(self._chk_bilingual)
        option_row.addWidget(self._chk_overlay)
        option_row.addStretch(1)
        root.addLayout(option_row)

        self._chk_all = QCheckBox("全选")
        self._chk_all.clicked.connect(self._on_select_all)
        root.addWidget(self._chk_all)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        # A click anywhere on a row toggles it; the check box only mirrors the selection.
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._list.setWordWrap(True)
        self._list.setSpacing(3)
        self._list.setToolTip("点击选中/取消；拖动排序；双击编辑；⏎＝输入后自动回车")
        model = self._list.model()
        if model is not None:
            model.rowsMoved.connect(self._on_rows_moved)
        self._list.itemSelectionChanged.connect(self._sync_checks)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self._list, 1)

        action_row = QHBoxLayout()
        for text, slot in (
            ("编辑", self._on_edit),
            ("自动回车", self._on_toggle_enter),
            ("删除", self._on_delete),
            ("重新翻译", self._on_retranslate),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            action_row.addWidget(button)
        action_row.addStretch(1)
        for text, slot in (("导入…", self._on_import), ("导出…", self._on_export)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            action_row.addWidget(button)
        root.addLayout(action_row)

        self._update_counter()
        self.set_target("", "")
        self._reload()

    # ----------------------------------------------------------------- public
    @property
    def settings(self) -> QuickPhraseSettings:
        return self._settings

    def set_settings(self, settings: QuickPhraseSettings) -> None:
        """Show settings changed elsewhere (overlay, translation results)."""
        self._settings = settings.model_copy(deep=True)
        self._reload()

    def set_target(self, code: str, label: str) -> None:
        """Language new translations are in (the mic channel's target)."""
        self._target = code
        text = f"添加时翻译为 {label}" if label else "添加时自动翻译"
        self._chk_translate.setText(text)
        self._chk_translate.setToolTip(
            "使用「设置 → 翻译」里选的引擎，目标语言与麦克风通道一致；关闭则原样输入原文"
        )

    def set_phrase_status(self, phrase_id: str, status: str) -> None:
        """Short note next to a phrase, e.g. "翻译中…" or "翻译失败"."""
        if status:
            self._status[phrase_id] = status
        else:
            self._status.pop(phrase_id, None)
        self._reload_items()

    def set_overlay_checked(self, checked: bool) -> None:
        self._chk_overlay.blockSignals(True)
        self._chk_overlay.setChecked(checked)
        self._chk_overlay.blockSignals(False)
        self._settings.show_overlay = checked

    def toggle_overlay(self) -> None:
        self._chk_overlay.toggle()

    # ---------------------------------------------------------------- helpers
    def _group(self) -> QuickPhraseGroup:
        return self._settings.active_group()

    def _emit(self) -> None:
        self.settings_changed.emit(self._settings.model_copy(deep=True))

    def _reload(self) -> None:
        self._cmb_group.blockSignals(True)
        self._cmb_group.clear()
        for group in self._settings.groups:
            self._cmb_group.addItem(group.name)
        self._cmb_group.setCurrentText(self._settings.active_group().name)
        self._cmb_group.blockSignals(False)
        for box, value in (
            (self._chk_translate, self._settings.translate),
            (self._chk_bilingual, self._settings.bilingual),
            (self._chk_overlay, self._settings.show_overlay),
        ):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self._chk_bilingual.setEnabled(self._settings.translate)
        self._reload_items()

    def _item_text(self, phrase: QuickPhrase) -> str:
        text = f"⏎ {phrase.text}" if phrase.auto_enter else phrase.text
        status = self._status.get(phrase.id, "") if self._settings.translate else ""
        return f"{text}  · {status}" if status else text

    def _item_tooltip(self, phrase: QuickPhrase) -> str:
        if not self._settings.translate:
            return phrase.text
        return f"{phrase.text}\n→ {phrase.translation or '（未翻译）'}"

    def _reload_items(self) -> None:
        selected = {item.data(_ID_ROLE) for item in self._list.selectedItems()}
        self._list.blockSignals(True)
        self._list.clear()
        for phrase in self._group().phrases:
            item = QListWidgetItem(self._item_text(phrase))
            item.setData(_ID_ROLE, phrase.id)
            item.setToolTip(self._item_tooltip(phrase))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            self._list.addItem(item)
            item.setSelected(phrase.id in selected)
        self._list.blockSignals(False)
        self._sync_checks()

    def _sync_checks(self) -> None:
        """Tick the check box of every selected row and update 全选."""
        self._list.blockSignals(True)
        count = self._list.count()
        picked = 0
        for i in range(count):
            item = self._list.item(i)
            if item is None:
                continue
            picked += item.isSelected()
            item.setCheckState(
                Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked
            )
        self._list.blockSignals(False)
        self._chk_all.setEnabled(count > 0)
        self._chk_all.setTristate(0 < picked < count)
        self._chk_all.setCheckState(
            Qt.CheckState.Checked
            if count and picked == count
            else Qt.CheckState.PartiallyChecked
            if picked
            else Qt.CheckState.Unchecked
        )

    def _selected_ids(self) -> list[str]:
        return [str(item.data(_ID_ROLE)) for item in self._list.selectedItems()]

    def _update_counter(self) -> None:
        self._counter.setText(f"{len(self._input.text())}/{PHRASE_MAX_CHARS}")

    def _unique_group_name(self, prompt: str, title: str, current: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, prompt, text=current)
        name = " ".join(name.split())[:20]
        if not ok or not name or name == current:
            return None
        if self._settings.group(name) is not None:
            QMessageBox.information(self, title, f"已经有名为「{name}」的分组")
            return None
        return name

    # ---------------------------------------------------------------- actions
    def add_phrase(self, text: str) -> QuickPhrase | None:
        text = " ".join(text.split())[:PHRASE_MAX_CHARS]
        if not text:
            return None
        group = self._group()
        if any(p.text == text for p in group.phrases):
            return None
        phrase = QuickPhrase(text=text)
        group.phrases.append(phrase)
        self._reload_items()
        self._emit()
        return phrase

    def _on_add(self) -> None:
        if self.add_phrase(self._input.text()) is not None:
            self._input.clear()

    def _on_group_selected(self, _index: int) -> None:
        name = self._cmb_group.currentText()
        if name and name != self._settings.current_group:
            self._settings.current_group = name
            self._reload_items()
            self._emit()

    def _on_new_group(self) -> None:
        name = self._unique_group_name("分组名称", "新建分组")
        if name is None:
            return
        self._settings.groups.append(QuickPhraseGroup(name=name))
        self._settings.current_group = name
        self._reload()
        self._emit()

    def _on_rename_group(self) -> None:
        group = self._group()
        name = self._unique_group_name("新的分组名称", "重命名分组", group.name)
        if name is None:
            return
        group.name = name
        self._settings.current_group = name
        self._reload()
        self._emit()

    def _on_delete_group(self) -> None:
        if len(self._settings.groups) < 2:
            QMessageBox.information(self, "删除分组", "至少保留一个分组")
            return
        group = self._group()
        if group.phrases:
            answer = QMessageBox.question(
                self, "删除分组", f"删除分组「{group.name}」和其中 {len(group.phrases)} 条常用语？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._settings.groups.remove(group)
        self._settings.current_group = self._settings.groups[0].name
        self._reload()
        self._emit()

    def _on_translate_toggled(self, checked: bool) -> None:
        self._settings.translate = checked
        self._chk_bilingual.setEnabled(checked)
        self._reload_items()
        self._emit()

    def _on_bilingual_toggled(self, checked: bool) -> None:
        self._settings.bilingual = checked
        self._emit()

    def _on_select_all(self) -> None:
        # Any partial selection counts as "not all", so one click selects everything.
        selected = len(self._list.selectedItems())
        if selected and selected == self._list.count():
            self._list.clearSelection()
        else:
            self._list.selectAll()
        self._sync_checks()

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._settings.show_overlay = checked
        self.overlay_toggled.emit(checked)

    def _on_rows_moved(self, *_args: object) -> None:
        order = [str(self._list.item(i).data(_ID_ROLE)) for i in range(self._list.count())]  # type: ignore[union-attr]
        group = self._group()
        by_id = {p.id: p for p in group.phrases}
        group.phrases = [by_id[i] for i in order if i in by_id]
        self._emit()

    def _on_toggle_enter(self) -> None:
        phrases = [p for i in self._selected_ids() if (p := self._settings.find(i)) is not None]
        if not phrases:
            return
        enable = not all(p.auto_enter for p in phrases)
        for phrase in phrases:
            phrase.auto_enter = enable
        self._reload_items()
        self._emit()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        # The two clicks toggled the row twice; edit exactly this one.
        self._edit_phrase(str(item.data(_ID_ROLE)))

    def _on_edit(self) -> None:
        ids = self._selected_ids()
        if ids:
            self._edit_phrase(ids[0])

    def _edit_phrase(self, phrase_id: str) -> None:
        phrase = self._settings.find(phrase_id)
        if phrase is None:
            return
        group = self._group()
        dialog = PhraseEditDialog(phrase, [g.name for g in self._settings.groups], group.name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text, translation, group_name, auto_enter = dialog.values()
        if not text:
            return
        if text != phrase.text and translation == phrase.translation:
            translation = ""  # stale: the original changed underneath it
        if translation != phrase.translation:
            # A hand-written translation counts as done for the current target.
            phrase.translated_to = self._target if translation else ""
        phrase.text, phrase.translation, phrase.auto_enter = text, translation, auto_enter
        if group_name != group.name:
            target = self._settings.group(group_name)
            if target is not None:
                group.phrases.remove(phrase)
                target.phrases.append(phrase)
        self._reload_items()
        self._emit()

    def _on_delete(self) -> None:
        ids = set(self._selected_ids())
        if not ids:
            return
        group = self._group()
        group.phrases = [p for p in group.phrases if p.id not in ids]
        self._reload_items()
        self._emit()

    def _on_retranslate(self) -> None:
        ids = self._selected_ids() or [p.id for p in self._group().phrases]
        if ids:
            self.retranslate_requested.emit(ids)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入常用语", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        group = self._group()
        known = {p.text for p in group.phrases}
        added = 0
        for phrase in parse_phrase_lines(raw):
            if phrase.text in known:
                continue
            known.add(phrase.text)
            if phrase.translation:
                phrase.translated_to = self._target
            group.phrases.append(phrase)
            added += 1
        self._reload_items()
        self._emit()
        QMessageBox.information(self, "导入常用语", f"已导入 {added} 条到「{group.name}」")

    def _on_export(self) -> None:
        group = self._group()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出常用语", f"常用语-{group.name}.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(format_phrase_lines(group.phrases), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))


class PhraseResultBridge(QObject):
    """Carries translation results from the worker thread to the GUI thread."""

    translated = pyqtSignal(object, object)  # PhraseRequest, str | None


__all__ = [
    "PhraseEditDialog",
    "PhrasePanel",
    "PhraseResultBridge",
    "format_phrase_lines",
    "parse_phrase_lines",
]
