"""Typed text → translate → synthesize (edge-tts mp3). Playback is left to the UI."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

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
        translated = (
            (data.get("responseData") or {}).get("translatedText") or ""
        ).strip()
        if not translated:
            raise RuntimeError(data.get("responseDetails") or "empty translation")
        return translated
    except Exception as exc:
        logger.warning(f"Typed translate failed: {exc}")
        raise RuntimeError(f"文本翻译失败: {exc}") from exc


async def synthesize_edge_tts(text: str, language: str) -> Path:
    """Synthesize speech with edge-tts; returns path to a temp mp3 (caller must keep/delete)."""
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("未安装 edge-tts，请执行: pip install edge-tts") from exc

    voice = _EDGE_VOICES.get(language, _EDGE_VOICES["en"])
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(tmp_path))
    return tmp_path


async def translate_and_synthesize(
    text: str,
    *,
    source: str,
    target: str,
) -> tuple[str, Path | None]:
    """Translate then synthesize. Returns (translated_text, mp3_path|None)."""
    translated = await translate_text(text, source, target)
    if not translated:
        return "", None
    path = await synthesize_edge_tts(translated, target)
    return translated, path


def run_async(coro):
    """Run coroutine from sync Qt code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop — run in a fresh loop via asyncio.run is unsafe;
    # callers should offload to a worker thread that calls asyncio.run.
    return asyncio.run(coro)
