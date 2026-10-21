"""Resumable downloads keep good partial bytes and retry only what is worth retrying."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from src.utils import http_download
from src.utils.http_download import DownloadError, fetch_resumable


def _serve(monkeypatch, responses: list[dict]) -> list[str | None]:
    seen: list[str | None] = []

    @contextmanager
    def fake_stream(_method, _url, *, headers=None, **_kw):
        seen.append((headers or {}).get("Range"))
        spec = responses.pop(0)
        status = spec.get("status", 200)

        def raise_for_status() -> None:
            if status >= 400:
                raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]

        yield SimpleNamespace(
            status_code=status,
            headers=spec.get("headers", {}),
            raise_for_status=raise_for_status,
            iter_bytes=lambda _n: iter([spec.get("body", b"")]),
        )

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return seen


def test_html_error_page_does_not_overwrite_partial(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "m.part"
    dest.write_bytes(b"good-bytes")
    _serve(monkeypatch, [{"headers": {"content-type": "text/html"}, "body": b"<html>"}])

    with pytest.raises(DownloadError):
        fetch_resumable("https://x/m", dest)
    assert dest.read_bytes() == b"good-bytes"


def test_server_errors_are_retried_and_resume(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "m.part"
    dest.write_bytes(b"abc")
    seen = _serve(
        monkeypatch,
        [
            {"status": 503},
            {
                "status": 206,
                "headers": {"content-length": "3", "content-range": "bytes 3-5/6"},
                "body": b"def",
            },
        ],
    )

    fetch_resumable("https://x/m", dest)

    assert dest.read_bytes() == b"abcdef"
    assert seen == ["bytes=3-", "bytes=3-"]


def test_416_counts_as_done_only_when_sizes_match(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "m.part"
    dest.write_bytes(b"abcdef")
    _serve(monkeypatch, [{"status": 416, "headers": {"content-range": "bytes */6"}}])
    fetch_resumable("https://x/m", dest)
    assert dest.read_bytes() == b"abcdef"

    _serve(
        monkeypatch,
        [
            {"status": 416, "headers": {"content-range": "bytes */4"}},
            {"headers": {"content-length": "4"}, "body": b"wxyz"},
        ],
    )
    fetch_resumable("https://x/m", dest)
    assert dest.read_bytes() == b"wxyz"


def test_short_body_is_resumed(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "m.part"
    _serve(
        monkeypatch,
        [
            {"headers": {"content-length": "6"}, "body": b"abc"},
            {
                "status": 206,
                "headers": {"content-length": "3", "content-range": "bytes 3-5/6"},
                "body": b"def",
            },
        ],
    )

    fetch_resumable("https://x/m", dest)

    assert dest.read_bytes() == b"abcdef"


def test_client_errors_are_not_retried(tmp_path, monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(http_download, "_sleep", sleeps.append)
    _serve(monkeypatch, [{"status": 404}])

    with pytest.raises(httpx.HTTPStatusError):
        fetch_resumable("https://x/m", tmp_path / "m.part")
    assert sleeps == []
