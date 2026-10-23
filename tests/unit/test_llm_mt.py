"""LLM / Hy-MT2 translation backends, cache and fallback (HTTP mocked)."""

from __future__ import annotations

import json

import httpx

from src.engines.pipeline.engine import build_economy_backends, task_device
from src.engines.pipeline.llm_mt import (
    CachedMt,
    ChatCompletionMt,
    FallbackMt,
    HyMtLocal,
    clean_llm_output,
    hymt_prompt,
)
from src.models.config import AppConfigModel


class _CountingMt:
    def __init__(self, reply: str | None = "hello") -> None:
        self.calls = 0
        self.started = False
        self.reply = reply

    @property
    def configured(self) -> bool:
        return True

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def translate(self, text: str, *, source_lang: str, target_lang: str) -> str | None:
        del text, source_lang, target_lang
        self.calls += 1
        return self.reply


def _chat_client(replies: list[str], seen: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {"url": str(request.url), "headers": request.headers, **json.loads(request.content)}
        )
        content = replies.pop(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_cache_hits_skip_backend_and_normalize_whitespace() -> None:
    inner = _CountingMt()
    mt = CachedMt(inner, max_entries=2)
    assert mt.translate("你好  世界", source_lang="zh", target_lang="en") == "hello"
    assert mt.translate("你好 世界", source_lang="zh", target_lang="en") == "hello"
    assert inner.calls == 1
    mt.translate("a", source_lang="zh", target_lang="en")
    mt.translate("b", source_lang="zh", target_lang="en")
    mt.translate("你好 世界", source_lang="zh", target_lang="en")
    assert inner.calls == 4


def test_cache_does_not_store_failures() -> None:
    inner = _CountingMt(reply=None)
    mt = CachedMt(inner)
    assert mt.translate("x", source_lang="en", target_lang="zh") is None
    assert mt.translate("x", source_lang="en", target_lang="zh") is None
    assert inner.calls == 2


def test_fallback_starts_lazily_and_only_on_failure() -> None:
    primary, fallback = _CountingMt("ok"), _CountingMt("backup")
    mt = FallbackMt(primary, fallback)
    mt.start()
    assert mt.translate("x", source_lang="en", target_lang="zh") == "ok"
    assert fallback.started is False
    primary.reply = None
    assert mt.translate("x", source_lang="en", target_lang="zh") == "backup"
    assert fallback.started is True


class _LoadingMt(_CountingMt):
    def __init__(self) -> None:
        super().__init__(reply=None)
        self.loading = True

    @property
    def configured(self) -> bool:
        return not self.loading

    @property
    def warming_up(self) -> bool:
        return self.loading


def test_loading_primary_holds_text_instead_of_using_fallback() -> None:
    primary, fallback = _LoadingMt(), _CountingMt("online")
    mt = FallbackMt(primary, fallback)
    mt.start()

    for _ in range(5):
        assert mt.translate("x", source_lang="en", target_lang="zh") is None
    assert mt.warming_up is True
    assert fallback.calls == 0

    primary.loading = False
    primary.reply = "local"
    assert mt.warming_up is False
    assert mt.translate("x", source_lang="en", target_lang="zh") == "local"


def test_fallback_that_is_still_loading_reports_warming_up() -> None:
    primary, fallback = _CountingMt(None), _LoadingMt()
    mt = FallbackMt(primary, fallback)
    mt.start()

    assert mt.translate("x", source_lang="en", target_lang="zh") is None
    assert mt.warming_up is True


def test_hymt_falls_back_to_local_translation_not_online() -> None:
    from src.engines.pipeline.mt import AutoMt

    _, mt, _ = build_economy_backends(AppConfigModel(economy_mt_backend="hymt"))
    fallback_mt = mt.inner._fallback if isinstance(mt, CachedMt) else mt._fallback
    assert isinstance(fallback_mt, AutoMt)


def test_chat_completion_sends_context_glossary_and_key() -> None:
    seen: list[dict] = []
    client = _chat_client(["Push mid now.", "“Then Roshan.”"], seen)
    mt = ChatCompletionMt(
        base_url="https://llm.example/v1/",
        model="deepseek-chat",
        api_key="sk-test",
        glossary={"肉山": "Roshan"},
        context_turns=2,
        client=client,
    )
    mt.start()
    assert mt.translate("现在推中", source_lang="zh", target_lang="en") == "Push mid now."
    assert mt.translate("然后打肉山", source_lang="zh", target_lang="en") == "Then Roshan."

    assert seen[0]["url"] == "https://llm.example/v1/chat/completions"
    assert seen[0]["headers"]["authorization"] == "Bearer sk-test"
    assert seen[0]["model"] == "deepseek-chat"
    second = seen[1]["messages"]
    assert "肉山 → Roshan" in second[0]["content"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "现在推中"
    assert second[2]["content"] == "Push mid now."


def test_chat_completion_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"error": "boom"})

    mt = ChatCompletionMt(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3:8b",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    mt.start()
    assert mt.translate("hello", source_lang="en", target_lang="zh") is None


def test_clean_llm_output_strips_reasoning_and_labels() -> None:
    assert clean_llm_output("<think>hmm</think>\n译文：你好") == "你好"
    assert clean_llm_output('"Hello there"') == "Hello there"
    assert clean_llm_output("「出发」") == "出发"


def test_hymt_prompt_uses_official_templates() -> None:
    assert hymt_prompt("hello", "zh").startswith("将以下文本翻译为中文")
    assert "into English" in hymt_prompt("你好", "en")


def test_hymt_without_llama_cpp_reports_unconfigured(tmp_path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "llama_cpp":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    mt = HyMtLocal(model_dir=tmp_path, auto_download=False)
    assert mt._load() is False
    assert mt.configured is False


def test_factory_builds_cached_llm_with_fallback(tmp_path) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path),
        economy_asr_backend="local",
        economy_mt_backend="llm",
        economy_llm_base_url="https://llm.example/v1",
        economy_llm_model="m",
        economy_mt_device="cpu",
        device_preference="cuda",
    )
    _asr, mt, _tts = build_economy_backends(config)
    assert isinstance(mt, CachedMt)
    assert isinstance(mt.inner, FallbackMt)
    assert task_device(config, "mt") == "cpu"
    assert task_device(config, "asr") == "cuda"


def test_factory_cache_can_be_disabled(tmp_path) -> None:
    config = AppConfigModel(
        log_dir=str(tmp_path),
        economy_asr_backend="local",
        economy_mt_backend="mymemory",
        economy_mt_cache_size=0,
    )
    _asr, mt, _tts = build_economy_backends(config)
    assert not isinstance(mt, CachedMt)


def test_hymt_model_choice_drives_primary_and_source(tmp_path) -> None:
    from src.engines.pipeline.llm_mt import HYMT_MODELS, hymt_model
    from src.engines.pipeline.model_sources import hymt_source

    assert hymt_model("missing") is HYMT_MODELS[0]
    config = AppConfigModel(
        log_dir=str(tmp_path), economy_mt_backend="hymt", economy_hymt_model="hy-mt2-7b"
    )
    _, mt, _ = build_economy_backends(config)
    fallback = mt.inner if isinstance(mt, CachedMt) else mt
    assert isinstance(fallback, FallbackMt)
    primary = fallback._primary
    assert isinstance(primary, HyMtLocal)
    assert primary.model_path.name == "Hy-MT2-7B-Q4_K_M.gguf"
    assert primary._download_mb == 4625
    source = hymt_source("hy-mt2-7b")
    assert "Hy-MT2-7B-GGUF" in source.origin
    assert source.size_mb == 4625
