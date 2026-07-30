"""Translation archive dialog tests."""

from __future__ import annotations

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from src.gui.transcript_dialog import TranscriptDialog


def _write_archive(path) -> None:
    path.write_text(
        "# Translator InTime 翻译记录\n"
        "[2026-07-30T18:00:00+08:00] INBOUND | "
        "ORIGINAL: Hello world | TRANSLATED: 你好，世界\n"
        "[2026-07-30T18:00:03+08:00] OUTBOUND | "
        "ORIGINAL: See you later | TRANSLATED: 回头见\n",
        encoding="utf-8",
    )


def test_transcript_dialog_loads_and_filters(tmp_path, qtbot) -> None:
    _write_archive(tmp_path / "archive_2026-07-30.txt")
    dialog = TranscriptDialog(tmp_path)
    qtbot.addWidget(dialog)

    assert dialog._table.rowCount() == 2
    dialog._txt_search.setText("world")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 2).text() == "Hello world"


def test_transcript_dialog_exports_filtered_rows(tmp_path, qtbot, monkeypatch) -> None:
    _write_archive(tmp_path / "archive_2026-07-30.txt")
    dialog = TranscriptDialog(tmp_path)
    qtbot.addWidget(dialog)
    dialog._txt_search.setText("later")
    output = tmp_path / "export.md"

    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "Markdown (*.md)"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    dialog._export_current()

    exported = output.read_text(encoding="utf-8")
    assert "See you later" in exported
    assert "Hello world" not in exported
