"""Cloud TTS backends, Edge voice options and the economy TTS factory (network mocked)."""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from src.engines.pipeline.cloud_asr import pcm16_to_wav
from src.engines.pipeline.cloud_tts import (
    CosyVoiceTts,
    DoubaoTts,
    FallbackTts,
    OpenAICompatTts,
    speed_to_percent,
)
from src.engines.pipeline.engine import build_tts, economy_stack_label
from src.engines.pipeline.sherpa_tts import MeloSherpaTts
from src.engines.pipeline.tts import AutoTts, EdgePcmTts
from src.engines.pipeline.tts_catalog import (
    DEFAULT_TTS_VOICES,
    TTS_ENGINE_ORDER,
    doubao_resource_id,
    tts_engine,
    voice_fields,
)
from src.models.config import TTS_BACKENDS, AppConfigModel

PCM = b"\x01\x00\x02\x00" * 400


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _doubao_stream(*events: dict) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


def test_catalog_covers_every_configurable_engine() -> None:
    assert set(TTS_ENGINE_ORDER) == set(TTS_BACKENDS)
    for engine_id, (zh, en) in DEFAULT_TTS_VOICES.items():
        zh_field, en_field = voice_fields(engine_id) or ("", "")
        assert getattr(AppConfigModel(), zh_field) == zh
        assert getattr(AppConfigModel(), en_field) == en
        ids = {v.id for v in tts_engine(engine_id).voices_zh}
        assert zh in ids
    assert voice_fields("melo") is None
    # xiaozhi-esp32-server defaults are offered
    assert "zh-CN-XiaoxiaoNeural" in {v.id for v in tts_engine("edge").voices_zh}
    assert "zh_female_wanwanxiaohe_moon_bigtts" in {v.id for v in tts_engine("doubao").voices_zh}


def test_unknown_tts_backend_falls_back_to_kokoro() -> None:
    assert AppConfigModel(economy_tts_backend="nope").economy_tts_backend == "kokoro"


def test_doubao_resource_follows_voice_generation() -> None:
    assert doubao_resource_id("zh_female_xiaohe_uranus_bigtts") == "seed-tts-2.0"
    assert doubao_resource_id("zh_female_wanwanxiaohe_moon_bigtts") == "seed-tts-1.0"


def test_doubao_streams_pcm_with_api_key() -> None:
    seen: list[httpx.Request] = []
    half = len(PCM) // 2

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = _doubao_stream(
            {"code": 0, "data": base64.b64encode(PCM[:half]).decode()},
            {"code": 0, "data": None, "sentence": {"text": "你好"}},
            {"code": 0, "data": base64.b64encode(PCM[half:]).decode()},
            {"code": 20000000, "message": "OK", "data": None},
        )
        return httpx.Response(200, content=body)

    tts = DoubaoTts(api_key="k", speed=0.9, client=_client(handler))
    tts.start()
    assert tts.synthesize("你好", language="zh") == PCM
    payload = json.loads(seen[0].content)
    assert payload["req_params"]["speaker"] == "zh_female_wanwanxiaohe_moon_bigtts"
    assert payload["req_params"]["audio_params"] == {
        "format": "pcm",
        "sample_rate": 16000,
        "speech_rate": -10,
    }
    assert seen[0].headers["X-Api-Key"] == "k"
    assert seen[0].headers["X-Api-Resource-Id"] == "seed-tts-1.0"

    tts.synthesize("hello", language="en")
    assert json.loads(seen[1].content)["req_params"]["speaker"].startswith(
        "zh_female_shuangkuaisisi"
    )


def test_doubao_legacy_credentials_and_error_code() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_doubao_stream({"code": 45000000, "message": "bad"}))

    tts = DoubaoTts(app_id="123", access_token="tok", client=_client(handler))
    tts.start()
    assert tts.synthesize("你好", language="zh") is None
    assert seen[0].headers["X-Api-App-Id"] == "123"
    assert seen[0].headers["X-Api-Access-Key"] == "tok"
    assert "X-Api-Key" not in seen[0].headers
    assert DoubaoTts().configured is False


def test_openai_compat_posts_speech_and_decodes_wav() -> None:
    seen: list[httpx.Request] = []
    pcm_24k = (np.sin(np.linspace(0, 100, 2400)) * 8000).astype(np.int16).tobytes()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=pcm16_to_wav(pcm_24k, sample_rate=24000))

    tts = OpenAICompatTts(
        url="http://127.0.0.1:8880/v1/",
        model="tts-1",
        api_key="sk",
        voice_zh="zf_xiaoxiao",
        voice_en="af_bella",
        client=_client(handler),
    )
    tts.start()
    pcm = tts.synthesize("你好", language="zh")
    assert str(seen[0].url) == "http://127.0.0.1:8880/v1/audio/speech"
    body = json.loads(seen[0].content)
    assert (body["voice"], body["model"], body["response_format"]) == (
        "zf_xiaoxiao",
        "tts-1",
        "wav",
    )
    assert seen[0].headers["Authorization"] == "Bearer sk"
    assert pcm is not None and abs(len(pcm) // 2 - 1600) <= 2
    assert OpenAICompatTts.speech_url("https://x/v1/audio/speech") == "https://x/v1/audio/speech"
    assert OpenAICompatTts(url="").configured is False


def test_openai_compat_http_error_returns_none() -> None:
    tts = OpenAICompatTts(url="http://x/v1", client=_client(lambda r: httpx.Response(401)))
    tts.start()
    assert tts.synthesize("hi", language="en") is None


def test_cosyvoice_uses_sdk_with_voice_and_rate(monkeypatch) -> None:
    calls: list[dict] = []

    class _Synth:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

        def call(self, text: str, timeout_millis: int | None = None) -> bytes:
            return PCM + b"\x00"

    class _Sdk:
        SpeechSynthesizer = _Synth

        class AudioFormat:
            PCM_16000HZ_MONO_16BIT = "pcm16k"

    tts = CosyVoiceTts(api_key="ds", speed=1.2)
    tts._sdk = _Sdk
    tts._started = True
    assert tts.synthesize("hello", language="en") == PCM
    assert calls[0]["voice"] == "loongbella_v2"
    assert calls[0]["format"] == "pcm16k"
    assert calls[0]["speech_rate"] == pytest.approx(1.2)


class _StubTts:
    def __init__(self, *, configured: bool = True, out: bytes | None = b"pp") -> None:
        self.configured = configured
        self.out = out
        self.calls = 0

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def synthesize(self, text: str, *, language: str) -> bytes | None:
        self.calls += 1
        return self.out


def test_fallback_tts_uses_edge_when_primary_fails_or_missing() -> None:
    good = FallbackTts(_StubTts(out=b"p1"), _StubTts(out=b"fb"), name="x")
    assert good.synthesize("a", language="zh") == b"p1"
    failing = FallbackTts(_StubTts(out=None), _StubTts(out=b"fb"), name="x")
    assert failing.synthesize("a", language="zh") == b"fb"
    missing_primary = _StubTts(configured=False)
    missing = FallbackTts(missing_primary, _StubTts(out=b"fb"), name="x")
    assert missing.synthesize("a", language="zh") == b"fb"
    assert missing_primary.calls == 0


def test_edge_voice_and_rate_options() -> None:
    edge = EdgePcmTts(voice_zh="zh-CN-YunxiNeural", voice_en="", speed=0.9)
    assert edge._voices["zh"] == "zh-CN-YunxiNeural"
    assert edge._voices["en"] == "en-US-JennyNeural"
    assert edge._rate == "-10%"
    assert speed_to_percent(1.3) == 30


@pytest.mark.parametrize(
    ("engine_id", "primary"),
    [
        ("doubao", DoubaoTts),
        ("cosyvoice", CosyVoiceTts),
        ("openai", OpenAICompatTts),
        ("melo", MeloSherpaTts),
    ],
)
def test_build_tts_wraps_engine_with_edge_fallback(engine_id: str, primary: type) -> None:
    cfg = AppConfigModel(
        economy_tts_backend=engine_id,
        volc_api_key="volc",
        economy_dashscope_api_key="ds",
        economy_openai_tts_url="http://127.0.0.1:8880/v1",
        economy_doubao_voice_zh="zh_female_xiaohe_uranus_bigtts",
    )
    tts = build_tts(cfg)
    assert isinstance(tts, FallbackTts)
    assert isinstance(tts.inner, primary)
    assert isinstance(tts._fallback, EdgePcmTts)
    if engine_id == "doubao":
        assert tts.inner._voice_zh == "zh_female_xiaohe_uranus_bigtts"


def test_build_tts_local_and_edge() -> None:
    edge = build_tts(
        AppConfigModel(economy_tts_backend="edge", economy_edge_voice_zh="zh-HK-HiuMaanNeural")
    )
    assert isinstance(edge, EdgePcmTts) and edge._voices["zh"] == "zh-HK-HiuMaanNeural"
    kokoro = build_tts(
        AppConfigModel(economy_tts_backend="kokoro", economy_kokoro_voice_zh="zf_002")
    )
    assert isinstance(kokoro, AutoTts)
    assert kokoro._kokoro._voice_zh == "zf_002"


def test_economy_stack_label_names_selected_engines(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    cfg = AppConfigModel(
        economy_asr_backend="doubao_flash", economy_mt_backend="llm", economy_tts_backend="doubao"
    )
    assert (
        economy_stack_label(cfg) == "经济模式：豆包极速识别 + 大模型翻译 + 豆包语音合成（火山引擎）"
    )


def test_melo_loads_local_model_and_resamples(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    from src.engines.pipeline import sherpa_tts

    monkeypatch.setattr(sherpa_tts, "_MIN_MODEL_BYTES", 1)
    model_dir = tmp_path / "vits-melo-tts-zh_en"
    (model_dir / "dict").mkdir(parents=True)
    for name in ("model.onnx", "tokens.txt", "lexicon.txt", "date.fst", "number.fst"):
        (model_dir / name).write_text("x", encoding="utf-8")
    captured: dict = {}

    class _Tts:
        def __init__(self, config) -> None:
            captured["config"] = config

        def generate(self, text: str, sid: int, speed: float):
            captured["speed"] = speed
            return SimpleNamespace(samples=np.zeros(44100, dtype=np.float32), sample_rate=44100)

    fake = SimpleNamespace(
        OfflineTts=_Tts,
        OfflineTtsConfig=lambda **kw: kw,
        OfflineTtsModelConfig=lambda **kw: kw,
        OfflineTtsVitsModelConfig=lambda **kw: kw,
    )
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)

    tts = MeloSherpaTts(speed=1.1, model_dir=model_dir)
    tts._tts = tts._load()
    tts._started = True
    pcm = tts.synthesize("你好 world", language="zh")
    assert pcm is not None and len(pcm) == 16000 * 2
    vits = captured["config"]["model"]["vits"]
    assert vits["model"].endswith("model.onnx") and vits["dict_dir"].endswith("dict")
    assert captured["config"]["rule_fsts"].count(".fst") == 2
    assert captured["speed"] == pytest.approx(1.1)
    assert tts.synthesize("。", language="zh") is None


def test_melo_download_skips_mirror_error_pages(tmp_path, monkeypatch) -> None:
    import contextlib
    import io
    import tarfile

    from src.engines.pipeline import sherpa_tts

    monkeypatch.setattr(sherpa_tts, "_MIN_MODEL_BYTES", 1)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name in ("model.onnx", "tokens.txt"):
            info = tarfile.TarInfo(f"{sherpa_tts.MELO_MODEL}/{name}")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    bodies = [b"403 Forbidden", buf.getvalue()]
    monkeypatch.setitem(
        sherpa_tts._ARCHIVE_SHA256, sherpa_tts.MELO_MODEL, hashlib.sha256(bodies[1]).hexdigest()
    )
    urls: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def raise_for_status(self) -> None: ...

        def iter_bytes(self, _size: int):
            yield self._body

    @contextlib.contextmanager
    def fake_stream(method: str, url: str, **_kw):
        urls.append(url)
        yield _Resp(bodies[len(urls) - 1])

    monkeypatch.setattr(sherpa_tts.httpx, "stream", fake_stream)
    assert sherpa_tts._download_archive(sherpa_tts.MELO_MODEL, tmp_path)
    assert len(urls) == 2
    assert sherpa_tts.melo_model_ready(tmp_path / sherpa_tts.MELO_MODEL)
    assert not list(tmp_path.glob("*.part"))


def test_model_download_resumes_after_dropped_connection(tmp_path, monkeypatch) -> None:
    from contextlib import contextmanager

    from src.engines.pipeline import sherpa_tts

    payload = b"x" * 300
    ranges: list[str | None] = []

    @contextmanager
    def fake_stream(_method, _url, *, headers=None, **_kw):
        start = 0
        rng = (headers or {}).get("Range")
        ranges.append(rng)
        if rng:
            start = int(rng.removeprefix("bytes=").rstrip("-"))

        def body():
            yield payload[start : start + 100]
            if len(ranges) == 1:
                raise httpx.ReadError("peer closed")
            yield payload[start + 100 :]

        status = 206 if start else 200
        yield SimpleNamespace(
            status_code=status, raise_for_status=lambda: None, iter_bytes=lambda _n: body()
        )

    monkeypatch.setattr(sherpa_tts.httpx, "stream", fake_stream)
    dest = tmp_path / "model.part"

    sherpa_tts._fetch_resumable("https://example.invalid/model.tar.bz2", dest)

    assert ranges == [None, "bytes=100-"]
    assert dest.read_bytes() == payload


def test_melo_skips_lfs_pointer_int8_model(tmp_path) -> None:
    from src.engines.pipeline import sherpa_tts

    (tmp_path / "model.int8.onnx").write_text("version https://git-lfs.github.com/spec/v1\n")
    (tmp_path / "tokens.txt").write_text("a 1\n")
    assert not sherpa_tts.melo_model_ready(tmp_path)

    (tmp_path / "model.onnx").write_bytes(b"\0" * sherpa_tts._MIN_MODEL_BYTES)
    assert sherpa_tts.melo_model_ready(tmp_path)
    assert sherpa_tts._melo_model_file(tmp_path) == tmp_path / "model.onnx"
