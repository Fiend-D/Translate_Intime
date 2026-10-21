"""Proxy helpers for huggingface_hub / urllib model downloads.

By default model downloads bypass system proxies (direct connection). Set
``TRANSLATOR_INTIME_USE_PROXY=1`` to keep proxies and only normalize
``socks://`` → ``socks5://`` (Clash often exports ``socks://``, which many
Python HTTP stacks reject).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
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
_USE_PROXY_FLAG = "TRANSLATOR_INTIME_USE_PROXY"
_bypass_logged = False


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


def redact_proxy_url(url: str) -> str:
    """Hide ``user:password@`` so proxy credentials never reach the log."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<proxy>"
    if not (parsed.username or parsed.password):
        return url
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=f"***@{host}"))


def use_proxy_for_downloads() -> bool:
    """True when TRANSLATOR_INTIME_USE_PROXY requests keeping system proxies."""
    val = (os.environ.get(_USE_PROXY_FLAG) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


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
            + ", ".join(f"{k}={redact_proxy_url(v)}" for k, v in changed.items())
            + "）"
        )
    return changed


def clear_proxy_env() -> dict[str, str]:
    """Remove proxy env vars. Returns previous values that were cleared."""
    saved: dict[str, str] = {}
    for name in _PROXY_VARS:
        if name in os.environ:
            saved[name] = os.environ.pop(name)
    return saved


def restore_proxy_env(saved: dict[str, str]) -> None:
    """Restore proxy env vars previously returned by ``clear_proxy_env``."""
    for name in _PROXY_VARS:
        os.environ.pop(name, None)
    for name, value in saved.items():
        os.environ[name] = value


def ensure_socks_support() -> bool:
    """Return True if SOCKS proxies can be used (PySocks available)."""
    try:
        import socks  # noqa: F401  # PySocks

        return True
    except ImportError:
        return False


def prepare_model_download_env() -> None:
    """Prepare env before huggingface_hub / urlretrieve downloads.

    Default: leave process-wide proxy variables untouched; callers that need
    direct access must wrap the download in ``without_proxy()``.
    With ``TRANSLATOR_INTIME_USE_PROXY=1``: keep proxies, normalize socks://.
    """
    global _bypass_logged
    if use_proxy_for_downloads():
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
        return

    if not _bypass_logged:
        _bypass_logged = True
        logger.info("模型下载将在下载上下文中临时绕过系统代理（直连）")


_without_proxy_lock = threading.Lock()
_without_proxy_depth = 0
_without_proxy_saved: dict[str, str] = {}


@contextmanager
def without_proxy() -> Iterator[None]:
    """Temporarily clear proxies for a download; restore previous values after.

    When ``TRANSLATOR_INTIME_USE_PROXY=1``, only normalizes socks:// and does
    not clear proxies.
    """
    if use_proxy_for_downloads():
        prepare_download_proxy_env()
        yield
        return

    global _without_proxy_depth, _without_proxy_saved
    # Downloads overlap on several threads: clear on first entry, restore on last exit.
    with _without_proxy_lock:
        if _without_proxy_depth == 0:
            _without_proxy_saved = clear_proxy_env()
        _without_proxy_depth += 1
    try:
        yield
    finally:
        with _without_proxy_lock:
            _without_proxy_depth -= 1
            if _without_proxy_depth == 0:
                restore_proxy_env(_without_proxy_saved)
                _without_proxy_saved = {}
