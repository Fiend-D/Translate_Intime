"""Model download progress registry and model source descriptions."""

from __future__ import annotations

import time

import pytest

from src.utils import download_progress as dp


@pytest.fixture(autouse=True)
def _clean_registry():
    dp.clear()
    yield
    dp.clear()


def test_resumed_bytes_are_not_counted_as_speed(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(dp.time, "monotonic", lambda: clock[0])
    dp.begin("k", "Kokoro", url="u", dest="/tmp/x", total=None)

    clock[0] = 101.0
    dp.update("k", 286_000_000, 325_000_000)
    clock[0] = 103.0
    dp.update("k", 288_000_000, 325_000_000)

    (task,) = dp.snapshot()
    assert task.speed == pytest.approx(1_000_000)


def test_streamed_bytes_report_fraction_and_finish() -> None:
    dp.begin("k", "Kokoro · model", url="https://example/x", dest="/tmp/x", total=None)
    dp.update("k", 50_000_000, 200_000_000)

    (task,) = dp.snapshot()
    assert task.fraction == pytest.approx(0.25)
    assert task.state == "running"
    assert dp.format_task(task).startswith("Kokoro · model 25%（50/200 MB")

    dp.finish("k", ok=True)
    (task,) = dp.snapshot()
    assert task.state == "done"
    assert task.fraction == 1.0
    assert dp.format_task(task) == "Kokoro · model 下载完成"


def test_watch_dir_measures_bytes_written_by_libraries(tmp_path) -> None:
    (tmp_path / "old.bin").write_bytes(b"x" * 100)
    dp.begin("hf", "NLLB", watch_dir=tmp_path, total=1000)
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "model.bin.incomplete").write_bytes(b"y" * 400)

    deadline = time.monotonic() + 5
    while (task := dp.snapshot()[0]).done != 400 and time.monotonic() < deadline:
        time.sleep(0.05)

    assert task.done == 400
    assert task.fraction == pytest.approx(0.4)


def test_tracked_marks_failure_and_reraises(tmp_path) -> None:
    with pytest.raises(OSError), dp.tracked("q", "Qwen3-ASR", watch_dir=tmp_path):
        raise OSError("connection reset")

    (task,) = dp.snapshot()
    assert task.state == "failed"
    assert "connection reset" in dp.format_task(task)


def test_unknown_total_shows_size_only() -> None:
    dp.begin("v", "Silero VAD")
    dp.update("v", 1_500_000)
    (task,) = dp.snapshot()
    assert task.fraction is None
    assert dp.format_task(task).startswith("Silero VAD（1.5 MB")


def test_melo_download_reports_progress(tmp_path, monkeypatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from src.engines.pipeline import sherpa_tts

    seen: list[tuple[int, int | None]] = []
    monkeypatch.setattr(
        sherpa_tts.download_progress,
        "update",
        lambda _k, done, total=None: seen.append((done, total)),
    )

    @contextmanager
    def fake_stream(*_a, **_kw):
        yield SimpleNamespace(
            status_code=200,
            headers={"content-length": "6"},
            raise_for_status=lambda: None,
            iter_bytes=lambda _n: iter([b"abc", b"def"]),
        )

    monkeypatch.setattr(sherpa_tts.httpx, "stream", fake_stream)
    sherpa_tts._fetch_resumable("https://x/y", tmp_path / "a.part", progress_key="tts:melo")

    assert seen == [(3, 6), (6, 6)]


def test_every_local_model_names_its_origin_and_folder() -> None:
    from src.engines.pipeline import model_sources as ms
    from src.engines.pipeline.model_catalog import ASR_OPTIONS, NLLB_OPTIONS

    sources = [
        ms.asr_source(o.id) for o in ASR_OPTIONS if o.id not in ("windows-live-captions", "auto")
    ]
    sources += [ms.nllb_source(o.id) for o in NLLB_OPTIONS]
    sources += [ms.hymt_source(), ms.kokoro_source(), ms.melo_source(), ms.silero_source()]

    for source in sources:
        assert source is not None
        assert source.origin.startswith("https://")
        text = ms.format_source(source)
        assert "下载：" in text and f"存放：{source.path}" in text
    assert ms.asr_source("windows-live-captions") is None
