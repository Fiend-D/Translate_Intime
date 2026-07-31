"""Unit tests for proxy URL normalization used by model downloads."""

from __future__ import annotations

import os

from src.utils.proxy_env import (
    normalize_proxy_url,
    prepare_download_proxy_env,
    prepare_model_download_env,
    without_proxy,
)


def test_normalize_proxy_url_socks_to_socks5() -> None:
    assert normalize_proxy_url("socks://127.0.0.1:7890/") == "socks5://127.0.0.1:7890/"
    assert normalize_proxy_url("socks://127.0.0.1:7890") == "socks5://127.0.0.1:7890"
    assert (
        normalize_proxy_url("SOCKS://user:pass@127.0.0.1:7890")
        == "socks5://user:pass@127.0.0.1:7890"
    )


def test_normalize_proxy_url_leaves_other_schemes() -> None:
    assert normalize_proxy_url("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert normalize_proxy_url("https://127.0.0.1:7890") == "https://127.0.0.1:7890"
    assert normalize_proxy_url("socks5://127.0.0.1:7890") == "socks5://127.0.0.1:7890"
    assert normalize_proxy_url("socks5h://127.0.0.1:7890") == "socks5h://127.0.0.1:7890"
    assert normalize_proxy_url("") == ""
    assert normalize_proxy_url("   ") == ""


def test_prepare_download_proxy_env_rewrites_all_proxy(monkeypatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "socks://127.0.0.1:7890/")
    changed = prepare_download_proxy_env()
    changed_casefold = {name.casefold(): value for name, value in changed.items()}
    assert os.environ["ALL_PROXY"] == "socks5://127.0.0.1:7890"
    assert os.environ["https_proxy"] == "socks5://127.0.0.1:7890/"
    assert changed_casefold["all_proxy"] == "socks5://127.0.0.1:7890"
    assert changed_casefold["https_proxy"] == "socks5://127.0.0.1:7890/"


def test_prepare_model_download_env_preserves_proxy_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TRANSLATOR_INTIME_USE_PROXY", raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    prepare_model_download_env()
    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:7890"
    assert os.environ["https_proxy"] == "http://127.0.0.1:7890"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:7890"


def test_prepare_model_download_env_use_proxy_normalizes(monkeypatch) -> None:
    monkeypatch.setenv("TRANSLATOR_INTIME_USE_PROXY", "1")
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890")
    prepare_model_download_env()
    assert os.environ["ALL_PROXY"] == "socks5://127.0.0.1:7890"


def test_without_proxy_clears_and_restores(monkeypatch) -> None:
    monkeypatch.delenv("TRANSLATOR_INTIME_USE_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890")
    with without_proxy():
        assert "HTTP_PROXY" not in os.environ
        assert "ALL_PROXY" not in os.environ
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:7890"
