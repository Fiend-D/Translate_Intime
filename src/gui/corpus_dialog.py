"""Edit hotwords + glossary; optionally import from hotwords/*.txt."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.utils.hotword_files import list_hotword_files, parse_hotword_file


class CorpusDialog(QDialog):
    """Hotwords (recognition boost) + glossary (forced translation pairs)."""

    def __init__(
        self,
        *,
        hotwords: list[str] | None = None,
        glossary: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("热词 / 术语")
        self.setMinimumSize(520, 460)

        root = QVBoxLayout(self)
        tip = QLabel(
            "热词：提升专名识别（每行一个）。\n"
            "术语：强制翻译映射，格式 原文=译文（每行一对）。\n"
            "可从项目 hotwords/ 目录导入游戏词表。"
        )
        tip.setWordWrap(True)
        tip.setObjectName("fieldLabel")
        root.addWidget(tip)

        import_row = QHBoxLayout()
        self._cmb_file = QComboBox()
        self._cmb_file.addItem("选择词表文件…", None)
        for path in list_hotword_files():
            self._cmb_file.addItem(path.stem, str(path))
        btn_import = QPushButton("导入（追加）")
        btn_import.clicked.connect(self._on_import)
        import_row.addWidget(self._cmb_file, 1)
        import_row.addWidget(btn_import)
        root.addLayout(import_row)

        root.addWidget(QLabel("热词"))
        self._txt_hot = QTextEdit()
        self._txt_hot.setPlaceholderText("每行一个热词，例如：\n回防\nA点\nSmoke")
        self._txt_hot.setPlainText("\n".join(hotwords or []))
        root.addWidget(self._txt_hot, 1)

        root.addWidget(QLabel("术语 glossary"))
        self._txt_gloss = QTextEdit()
        self._txt_gloss.setPlaceholderText("原文=译文\n回防=rotate\n中路=mid")
        lines = [f"{k}={v}" for k, v in (glossary or {}).items()]
        self._txt_gloss.setPlainText("\n".join(lines))
        root.addWidget(self._txt_gloss, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_import(self) -> None:
        path = self._cmb_file.currentData()
        if not path:
            QMessageBox.information(self, "导入", "请先选择词表文件。")
            return
        try:
            hw, gl = parse_hotword_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return

        existing_hw = {
            line.strip()
            for line in self._txt_hot.toPlainText().splitlines()
            if line.strip()
        }
        for w in hw:
            if w not in existing_hw:
                existing_hw.add(w)
        self._txt_hot.setPlainText("\n".join(sorted(existing_hw, key=str.lower)))

        existing_gl = self._parse_glossary_text(self._txt_gloss.toPlainText())
        existing_gl.update(gl)
        self._txt_gloss.setPlainText("\n".join(f"{k}={v}" for k, v in existing_gl.items()))
        QMessageBox.information(
            self, "导入完成", f"已追加热词 {len(hw)} / 术语 {len(gl)}"
        )

    @staticmethod
    def _parse_glossary_text(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            src, tgt = line.split("=", 1)
            src, tgt = src.strip(), tgt.strip()
            if src:
                out[src] = tgt
        return out

    def result_corpus(self) -> tuple[list[str], dict[str, str]]:
        hotwords = [
            line.strip()
            for line in self._txt_hot.toPlainText().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        # de-dupe preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for w in hotwords:
            if w not in seen:
                seen.add(w)
                uniq.append(w)
        glossary = self._parse_glossary_text(self._txt_gloss.toPlainText())
        return uniq, glossary
