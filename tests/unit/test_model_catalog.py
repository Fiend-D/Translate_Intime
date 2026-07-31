"""Unit tests for offline model catalog labels and defaults."""

from __future__ import annotations

from src.engines.pipeline.model_catalog import (
    KOKORO_OPTIONS,
    NLLB_OPTIONS,
    format_kokoro_info,
    format_option_label,
    format_ram_label,
    nllb_option_by_id,
    recommended_nllb_option,
)
from src.models.config import AppConfigModel


def test_nllb_options_include_recommended() -> None:
    assert any(opt.recommended for opt in NLLB_OPTIONS)
    rec = recommended_nllb_option()
    assert rec.recommended is True
    assert "600M" in rec.title


def test_format_option_label_contains_mb_and_ram() -> None:
    opt = recommended_nllb_option()
    label = format_option_label(opt)
    assert "MB" in label
    assert "内存" in label
    assert "★推荐" in label
    assert str(opt.download_mb) in label


def test_format_option_label_non_recommended() -> None:
    opt = next(o for o in NLLB_OPTIONS if not o.recommended)
    label = format_option_label(opt)
    assert "MB↓" in label
    assert "★推荐" not in label


def test_nllb_ids_match_config_default() -> None:
    cfg = AppConfigModel()
    assert nllb_option_by_id(cfg.economy_nllb_model) is not None
    assert cfg.economy_nllb_model == recommended_nllb_option().id
    assert cfg.economy_offline_setup_done is False


def test_nllb_option_by_id_unknown() -> None:
    assert nllb_option_by_id("does-not-exist") is None
    assert nllb_option_by_id(None) is None


def test_format_ram_label() -> None:
    assert format_ram_label(500) == "~500MB"
    assert format_ram_label(1200) == "~1.2GB"
    assert format_ram_label(2800) == "~2.8GB"


def test_kokoro_info_line() -> None:
    assert KOKORO_OPTIONS
    info = format_kokoro_info()
    assert "Kokoro" in info
    assert "350" in info or "MB" in info
    assert "内存" in info


def test_offline_model_dialog_default_selection() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # keep ref

    from src.gui.offline_model_dialog import OfflineModelDialog

    dlg = OfflineModelDialog()
    assert dlg.selected_nllb_model_id() == recommended_nllb_option().id

    dlg2 = OfflineModelDialog(current_model_id="mijuanlo/nllb-200-distilled-1.3B-int8-ct2")
    assert dlg2.selected_nllb_model_id() == ("mijuanlo/nllb-200-distilled-1.3B-int8-ct2")
