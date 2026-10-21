"""Hugging Face downloads must pass the endpoint explicitly and fall back in order."""

from __future__ import annotations

import pytest

from src.utils import download_progress, hf_download


@pytest.fixture(autouse=True)
def _clean_progress(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    download_progress.clear()
    yield
    download_progress.clear()


def test_user_endpoint_is_tried_first_without_duplicates(monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co/")

    assert hf_download.hf_endpoints() == ["https://huggingface.co", "https://hf-mirror.com"]


def test_download_falls_back_to_next_endpoint(tmp_path) -> None:
    calls: list[str] = []

    def fetch(*, repo_id: str, local_dir: str, endpoint: str, **_kwargs) -> str:
        calls.append(endpoint)
        if endpoint == "https://hf-mirror.com":
            raise OSError("mirror down")
        return local_dir

    result = hf_download._download(
        fetch, repo_id="org/model", local_dir=tmp_path / "m", label="M", key="m", total_mb=1
    )

    assert calls == ["https://hf-mirror.com", "https://huggingface.co"]
    assert result == str(tmp_path / "m")
    (task,) = download_progress.snapshot()
    assert task.state == "done"


def test_known_repo_downloads_pinned_revision(tmp_path) -> None:
    revisions: list[str | None] = []

    def fetch(*, revision: str | None, local_dir: str, **_kwargs) -> str:
        revisions.append(revision)
        return local_dir

    repo = "Systran/faster-whisper-medium"
    hf_download._download(fetch, repo_id=repo, local_dir=tmp_path, label="M", key="m", total_mb=0)
    hf_download._download(
        fetch, repo_id="org/custom", local_dir=tmp_path, label="M", key="c", total_mb=0
    )

    assert revisions == [hf_download.PINNED_REVISIONS[repo], None]


def test_download_reports_failure_after_every_endpoint(tmp_path) -> None:
    def fetch(**_kwargs) -> str:
        raise OSError("offline")

    with pytest.raises(RuntimeError, match="offline"):
        hf_download._download(
            fetch, repo_id="org/model", local_dir=tmp_path, label="M", key="m", total_mb=0
        )

    (task,) = download_progress.snapshot()
    assert task.state == "failed"
