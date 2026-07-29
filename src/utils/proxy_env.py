"""Normalize proxy environment variables for huggingface_hub / urllib downloads.

Clash and similar tools often export ``socks://127.0.0.1:7890``, which many
Python HTTP stacks reject with ``Unknown scheme for proxy URL``. Hugging Face
and requests understand ``socks5://`` when PySocks is installed.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

from src.utils.logger import logger

_PROXY_VARS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


def normalize_proxy_url(url: str) -> str:
    """Rewrite socks:// → socks5://; leave other schemes unchanged."""
    raw = (url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    scheme = (parsed.scheme or "").lower()
    if scheme == "socks":
        return urlunparse(parsed._replace(scheme="socks5"))
    if scheme == "socks5h":
        return raw
    return raw


def prepare_download_proxy_env() -> dict[str, str]:
    """Normalize proxy env in-process. Returns map of vars that were changed."""
    changed: dict[str, str] = {}
    for name in _PROXY_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        fixed = normalize_proxy_url(value)
        if fixed != value:
            os.environ[name] = fixed
            changed[name] = fixed
    if changed:
        logger.info(
            "已规范化代理协议 socks:// → socks5://（"
            + ", ".join(f"{k}={v}" for k, v in changed.items())
            + "）"
        )
    return changed


def ensure_socks_support() -> bool:
    """Return True if SOCKS proxies can be used (PySocks available)."""
    try:
        import socks  # noqa: F401  # PySocks

        return True
    except ImportError:
        return False


def prepare_model_download_env() -> None:
    """Call before huggingface_hub / urlretrieve downloads that may use SOCKS."""
    prepare_download_proxy_env()
    needs_socks = False
    for name in _PROXY_VARS:
        value = (os.environ.get(name) or "").lower()
        if value.startswith("socks5://") or value.startswith("socks5h://"):
            needs_socks = True
            break
    if needs_socks and not ensure_socks_support():
        logger.warning(
            "检测到 SOCKS 代理，但未安装 PySocks。"
            "请执行: pip install PySocks"
            "；或把代理改成 http://127.0.0.1:端口（Clash 的 HTTP 端口）"
        )
