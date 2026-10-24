"""Multi-source model downloads: source order, verification and fallback (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.utils import download_progress, model_fetch
from src.utils.http_download import DownloadError
from src.utils.model_fetch import ModelFile, RepoManifest


@pytest.fixture(autouse=True)
def _china_first(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    model_fetch.set_mirror_preference("china")
    yield
    model_fetch.set_mirror_preference("china")
    download_progress.clear()


def _file(name: str, payload: bytes) -> ModelFile:
    return ModelFile(name, len(payload), hashlib.sha256(payload).hexdigest())


def _manifest(*files: ModelFile) -> RepoManifest:
    return RepoManifest("org/model", "abc123", files, ("ms/model",))


def test_china_preference_tries_modelscope_then_mirror_then_origin() -> None:
    m = _manifest(_file("model.bin", b"x"))

    urls = model_fetch.ordered_urls(m.candidates(m.files[0]))

    assert urls == [
        "https://modelscope.cn/models/ms/model/resolve/master/model.bin",
        "https://hf-mirror.com/org/model/resolve/abc123/model.bin",
        "https://huggingface.co/org/model/resolve/abc123/model.bin",
    ]


def test_global_preference_tries_origin_first_and_keeps_backups() -> None:
    model_fetch.set_mirror_preference("global")
    m = _manifest(_file("model.bin", b"x"))

    urls = model_fetch.ordered_urls(m.candidates(m.files[0]))

    assert urls[0].startswith("https://huggingface.co/")
    assert {model_fetch.host_of(u) for u in urls} == {
        "huggingface.co",
        "modelscope.cn",
        "hf-mirror.com",
    }


def test_user_hf_endpoint_always_goes_first(monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://my-mirror.example/")

    urls = model_fetch.ordered_urls(model_fetch.huggingface_urls("org/m", "a.bin", "rev"))

    assert urls[0] == "https://my-mirror.example/org/m/resolve/rev/a.bin"


def test_github_assets_prefer_proxy_in_china_and_ghfast_last() -> None:
    url = "https://github.com/o/r/releases/download/v1/a.onnx"

    china = model_fetch.ordered_urls(model_fetch.github_urls(url))
    model_fetch.set_mirror_preference("global")
    overseas = model_fetch.ordered_urls(model_fetch.github_urls(url))

    assert china == [f"https://gh-proxy.com/{url}", url, f"https://ghfast.top/{url}"]
    assert overseas == [url, f"https://gh-proxy.com/{url}", f"https://ghfast.top/{url}"]


def test_bad_copy_is_discarded_and_next_source_used(tmp_path, monkeypatch) -> None:
    good = b"real weights"
    item = _file("model.bin", good)
    served: list[str] = []

    def fake_fetch(url: str, dest: Path, **_kw) -> None:
        served.append(model_fetch.host_of(url))
        dest.write_bytes(b"tampered!!!!" if "modelscope" in url else good)

    monkeypatch.setattr(model_fetch, "fetch_resumable", fake_fetch)

    _manifest(item).fetch(tmp_path, label="M", key="m")

    assert served == ["modelscope.cn", "hf-mirror.com"]
    assert (tmp_path / "model.bin").read_bytes() == good
    assert not (tmp_path / "model.bin.part").exists()
    assert model_fetch.files_ready(tmp_path, [item])


def test_partial_file_resumes_on_the_next_source(tmp_path, monkeypatch) -> None:
    payload = b"0123456789"
    item = _file("model.bin", payload)
    offsets: list[int] = []

    def fake_fetch(url: str, dest: Path, **_kw) -> None:
        offset = dest.stat().st_size if dest.exists() else 0
        offsets.append(offset)
        if "modelscope" in url:
            dest.write_bytes(payload[:4])
            raise DownloadError("connection dropped")
        with dest.open("ab") as fh:
            fh.write(payload[offset:])

    monkeypatch.setattr(model_fetch, "fetch_resumable", fake_fetch)

    _manifest(item).fetch(tmp_path, label="M", key="m")

    assert offsets == [0, 4]
    assert (tmp_path / "model.bin").read_bytes() == payload


def test_every_source_failing_raises_and_reports(tmp_path, monkeypatch) -> None:
    def fail(_url, _dest, **_kw):
        raise DownloadError("unreachable")

    monkeypatch.setattr(model_fetch, "fetch_resumable", fail)

    with pytest.raises(DownloadError, match="所有下载源均失败"):
        _manifest(_file("model.bin", b"x")).fetch(tmp_path, label="M", key="m")
    [task] = download_progress.snapshot()
    assert task.state == "failed"


def test_ready_files_are_not_downloaded_again(tmp_path, monkeypatch) -> None:
    done, todo = b"have it", b"need it"
    (tmp_path / "a.txt").write_bytes(done)
    calls: list[str] = []

    def fake_fetch(url: str, dest: Path, **_kw) -> None:
        calls.append(url.rsplit("/", 1)[-1])
        dest.write_bytes(todo)

    monkeypatch.setattr(model_fetch, "fetch_resumable", fake_fetch)

    _manifest(_file("a.txt", done), _file("b.txt", todo)).fetch(tmp_path, label="M", key="m")

    assert calls == ["b.txt"]


def test_wrong_size_file_is_not_ready(tmp_path) -> None:
    item = _file("model.bin", b"12345")
    (tmp_path / "model.bin").write_bytes(b"1234")

    assert not model_fetch.file_ready(tmp_path, item)


def test_every_manifest_entry_is_well_formed() -> None:
    from src.engines.pipeline import model_manifests as mm

    for table in (mm.WHISPER, mm.QWEN_ASR, mm.HYMT, mm.NLLB, mm.SHERPA_ASR):
        for manifest in table.values():
            assert manifest.files and len(manifest.revision) == 40
            for item in manifest.files:
                assert item.size > 0 and len(item.sha256) == 64


def test_model_dir_setting_overrides_default(tmp_path, monkeypatch) -> None:
    from src.engines.pipeline.nllb_mt import nllb_cache_dir
    from src.utils import resource_paths

    monkeypatch.delenv("SOUNDFERRY_RESOURCE_DIR", raising=False)
    try:
        resource_paths.set_model_dir(str(tmp_path / "models"))
        assert resource_paths.model_resource_root() == tmp_path / "models"
        assert nllb_cache_dir("a/b").is_relative_to(tmp_path / "models")
        resource_paths.set_model_dir("")
        assert resource_paths.model_resource_root() == resource_paths.default_model_resource_root()
    finally:
        resource_paths.set_model_dir(None)
