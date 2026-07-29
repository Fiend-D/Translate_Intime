"""Unit tests for proxy URL normalization used by model downloads."""

from __future__ import annotations

import os

from src.utils.proxy_env import normalize_proxy_url, prepare_download_proxy_env


def test_normalize_proxy_url_socks_to_socks5() -> None:
    assert (
        normalize_proxy_url("socks://127.0.0.1:7890/")
        == "socks5://127.0.0.1:7890/"
    )
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
    monkeypatch.setenv("https_proxy", "socks://127.0.0.1:7890/")
    changed = prepare_download_proxy_env()
    assert os.environ["ALL_PROXY"] == "socks5://127.0.0.1:7890"
    assert os.environ["https_proxy"] == "socks5://127.0.0.1:7890/"
    assert changed["ALL_PROXY"] == "socks5://127.0.0.1:7890"
    assert changed["https_proxy"] == "socks5://127.0.0.1:7890/"
