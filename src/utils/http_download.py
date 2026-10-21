"""Resumable HTTP downloads for large model files on slow or flaky links."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from src.utils import download_progress
from src.utils.logger import logger


class DownloadError(RuntimeError):
    """The server answered, but not with the requested file."""


class _RetryableError(Exception):
    pass


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _range_start(response: object) -> int | None:
    """First byte offset from ``Content-Range: bytes START-END/TOTAL``."""
    headers = getattr(response, "headers", None)
    value = headers.get("content-range") if headers is not None else None
    if not value or not value.startswith("bytes "):
        return None
    start = value[6:].split("-", 1)[0]
    return int(start) if start.isdigit() else None


def _range_total(response: object) -> int | None:
    """TOTAL from ``Content-Range: bytes START-END/TOTAL`` or ``bytes */TOTAL``."""
    headers = getattr(response, "headers", None)
    value = headers.get("content-range") if headers is not None else None
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


def _header(response: object, name: str) -> str:
    headers = getattr(response, "headers", None)
    return str(headers.get(name) or "") if headers is not None else ""


def fetch_resumable(
    url: str,
    dest: Path,
    *,
    attempts: int = 4,
    progress_key: str | None = None,
    trust_env: bool = True,
) -> None:
    """Download ``url`` into ``dest``, resuming with Range after dropped connections.

    A partial ``dest`` left by an earlier call (or another mirror of the same asset)
    is continued rather than restarted. Server errors (5xx/429) and short bodies are
    retried; a non-file answer (other 4xx, HTML error page) raises without touching
    the partial file.
    """
    for attempt in range(1, attempts + 1):
        offset = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with httpx.stream(
                "GET",
                url,
                headers=headers,
                follow_redirects=True,
                timeout=60.0,
                trust_env=trust_env,
            ) as r:
                if r.status_code == 416:
                    total = _range_total(r)
                    if total is not None and total == offset:
                        return
                    # The partial is longer than (or unrelated to) the remote file.
                    logger.warning(f"续传位置无效（本地 {offset}，远端 {total}），重新下载")
                    dest.unlink(missing_ok=True)
                    raise _RetryableError("416")
                if r.status_code == 429 or r.status_code >= 500:
                    raise _RetryableError(f"HTTP {r.status_code}")
                r.raise_for_status()
                if _header(r, "content-type").lower().startswith("text/html"):
                    raise DownloadError(f"镜像返回网页而不是文件（HTTP {r.status_code}）")
                resumed = bool(offset) and r.status_code == 206
                if resumed and _range_start(r) not in (None, offset):
                    # Appending bytes from the wrong offset would silently corrupt the file.
                    logger.warning(f"续传偏移不一致（{_range_start(r)} ≠ {offset}），重新下载")
                    resumed = False
                written = offset if resumed else 0
                length = _header(r, "content-length")
                total = written + int(length) if length.isdigit() else None
                with dest.open("ab" if resumed else "wb") as fh:
                    for chunk in r.iter_bytes(1024 * 256):
                        fh.write(chunk)
                        written += len(chunk)
                        if progress_key:
                            download_progress.update(progress_key, written, total)
                if total is not None and written < total:
                    raise _RetryableError(f"连接提前结束（{written}/{total}）")
            return
        except (httpx.TransportError, _RetryableError) as exc:
            if attempt == attempts:
                raise DownloadError(f"下载失败：{exc}") from exc
            size = dest.stat().st_size if dest.exists() else 0
            delay = min(2.0**attempt, 30.0)
            logger.info(
                f"模型下载中断，已下载 {size >> 20} MB，{delay:.0f} 秒后续传（第 {attempt} 次）: {exc}"
            )
            # Outages on these links last tens of seconds; instant retries all fail together.
            _sleep(delay)
