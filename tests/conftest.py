"""Shared pytest fixtures."""

import tempfile
from pathlib import Path

import pytest

from src.models.config import AppConfigModel
from src.utils import config_manager


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


@pytest.fixture(autouse=True)
def _isolated_keyring(monkeypatch) -> MemoryKeyring:
    """No test may read or write the developer's real system keyring."""
    backend = MemoryKeyring()
    monkeypatch.setattr(config_manager, "keyring", backend)
    return backend


_CREDENTIAL_ENV_VARS = (
    "DASHSCOPE_API_KEY",
    "VOLC_API_KEY",
    "VOLC_APP_KEY",
    "VOLC_APP_ID",
    "VOLC_APPID",
    "VOLC_ACCESS_TOKEN",
    "VOLC_TOKEN",
)


@pytest.fixture(autouse=True)
def _no_env_credentials(monkeypatch) -> None:
    """A developer's real API keys in the shell must not change test outcomes."""
    for name in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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


@pytest.fixture(autouse=True)
def _no_download_backoff(monkeypatch) -> None:
    from src.utils import http_download

    monkeypatch.setattr(http_download, "_sleep", lambda _s: None)
