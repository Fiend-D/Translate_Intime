"""Typed text → translate → synthesize (edge-tts mp3). Playback is left to the UI."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx

from src.utils.logger import logger

_EDGE_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
}


async def translate_text(text: str, source: str, target: str) -> str:
    """Translate short text. Same language returns input. Uses MyMemory (no key)."""
    text = (text or "").strip()
    if not text:
        return ""
    if source == target:
        return text

    url = "https://api.mymemory.translated.net/get"
    params = {"q": text[:450], "langpair": f"{source}|{target}"}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        if not translated:
            raise RuntimeError(data.get("responseDetails") or "empty translation")
        return translated
    except Exception as exc:
        logger.warning(f"Typed translate failed: {exc}")
        raise RuntimeError(f"文本翻译失败: {exc}") from exc


async def synthesize_edge_tts(text: str, language: str) -> bytes:
    """Synthesize speech with edge-tts and keep the MP3 entirely in memory."""
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("未安装 edge-tts，请执行: pip install edge-tts") from exc

    voice = _EDGE_VOICES.get(language, _EDGE_VOICES["en"])
    communicate = edge_tts.Communicate(text, voice)
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk.get("data", b""))
    if not audio:
        raise RuntimeError("Edge TTS 未返回音频数据")
    return bytes(audio)


async def translate_and_synthesize(
    text: str,
    *,
    source: str,
    target: str,
) -> tuple[str, bytes | None]:
    """Translate then synthesize. Returns translated text and in-memory MP3 bytes."""
    translated = await translate_text(text, source, target)
    if not translated:
        return "", None
    audio = await synthesize_edge_tts(translated, target)
    return translated, audio


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run coroutine from sync Qt code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop — run in a fresh loop via asyncio.run is unsafe;
    # callers should offload to a worker thread that calls asyncio.run.
    coro.close()
    raise RuntimeError("run_async cannot be called from a thread with a running event loop")
