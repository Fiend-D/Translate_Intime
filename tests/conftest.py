"""Shared pytest fixtures."""

import tempfile
from pathlib import Path

import pytest

from src.models.config import AppConfigModel


@pytest.fixture
def temp_config_dir() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def default_config(temp_config_dir: Path) -> AppConfigModel:
    return AppConfigModel(
        log_dir=str(temp_config_dir / "logs"),
        volc_api_key="00000000-0000-0000-0000-000000000000",
    )
