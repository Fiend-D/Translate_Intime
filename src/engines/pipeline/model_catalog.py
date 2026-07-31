"""Central catalog of offline MT/TTS model options for economy mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class OfflineModelOption:
    id: str  # HF id or logical id
    title: str  # short Chinese title
    role: Literal["asr", "mt", "tts"]
    download_mb: int  # approximate download
    ram_mb: int  # approximate runtime RAM (CPU int8)
    quality: str  # e.g. "良好 / 推荐日常"
    note: str  # one-line tip
    recommended: bool = False


NLLB_OPTIONS: list[OfflineModelOption] = [
    OfflineModelOption(
        id="JustFrederik/nllb-200-distilled-600M-ct2-int8",
        title="NLLB 600M INT8",
        role="mt",
        download_mb=650,
        ram_mb=1200,
        quality="良好，英中日常够用",
        note="桌面推荐：下载快、内存占用低",
        recommended=True,
    ),
    OfflineModelOption(
        id="mijuanlo/nllb-200-distilled-1.3B-int8-ct2",
        title="NLLB 1.3B INT8",
        role="mt",
        download_mb=1400,
        ram_mb=2800,
        quality="更好，专名/长句更稳",
        note="内存 ≥8GB 更舒适；首次下载较慢",
        recommended=False,
    ),
]

KOKORO_OPTIONS: list[OfflineModelOption] = [
    OfflineModelOption(
        id="kokoro-onnx",
        title="Kokoro-82M ONNX",
        role="tts",
        download_mb=350,
        ram_mb=500,
        quality="自然，英中本地语音",
        note="英文 v1.0 + 中文 v1.1，约几百 MB",
        recommended=True,
    ),
]

# 本地 ASR 选项.
# - 流式 (OnlineRecognizer): id 与 sherpa_asr.py 的 _MODEL_REGISTRY key 一致
# - 离线 (OfflineRecognizer): id 与 _OFFLINE_MODEL_REGISTRY key 一致
# "auto" 为智能路由: 英文输入用纯英文模型, 中文输入用双语模型.
ASR_OPTIONS: list[OfflineModelOption] = [
    OfflineModelOption(
        id="windows-live-captions",
        title="Windows Live Captions (系统级)",
        role="asr",
        download_mb=0,
        ram_mb=0,
        quality="优秀，接近辅助字幕，噪声鲁棒",
        note="★推荐Win11：系统级ASR，零占用，高准确率",
        recommended=True,
    ),
    OfflineModelOption(
        id="faster-whisper-medium",
        title="Whisper medium 多语言",
        role="asr",
        download_mb=770,
        ram_mb=1500,
        quality="优秀，接近辅助字幕，噪声鲁棒",
        note="跨平台：中英文准确率最高，CPU可跑",
        recommended=False,
    ),
    OfflineModelOption(
        id="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        title="SenseVoice 多语言 离线",
        role="asr",
        download_mb=240,
        ram_mb=600,
        quality="良好，自带标点大小写",
        note="体积小速度快；游戏BGM段有噪点",
        recommended=False,
    ),
    OfflineModelOption(
        id="faster-whisper-large-v3",
        title="Whisper large-v3 多语言",
        role="asr",
        download_mb=1550,
        ram_mb=3000,
        quality="最高准确率",
        note="需要 GPU 才能低延迟；CPU 较慢",
        recommended=False,
    ),
    OfflineModelOption(
        id="auto",
        title="智能路由（英文→英文模型 / 中文→双语模型）",
        role="asr",
        download_mb=280,
        ram_mb=800,
        quality="英文识别准，中文兼容混说",
        note="流式低延迟：自动按语言路由",
        recommended=False,
    ),
    OfflineModelOption(
        id="sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        title="Zipformer 中英双语 流式",
        role="asr",
        download_mb=200,
        ram_mb=500,
        quality="良好，中英混说识别稳",
        note="单一双语模型；纯英文可能被误判为中文",
        recommended=False,
    ),
    OfflineModelOption(
        id="sherpa-onnx-streaming-zipformer-en-2023-06-26",
        title="Zipformer 纯英文 流式",
        role="asr",
        download_mb=80,
        ram_mb=300,
        quality="英文识别准确",
        note="仅英文；中文无法识别",
        recommended=False,
    ),
]


def nllb_option_by_id(model_id: str | None) -> OfflineModelOption | None:
    if not model_id:
        return None
    needle = model_id.strip()
    for opt in NLLB_OPTIONS:
        if opt.id == needle:
            return opt
    return None


def asr_option_by_id(model_id: str | None) -> OfflineModelOption | None:
    if not model_id:
        return None
    needle = model_id.strip()
    for opt in ASR_OPTIONS:
        if opt.id == needle:
            return opt
    return None


def recommended_nllb_option() -> OfflineModelOption:
    for opt in NLLB_OPTIONS:
        if opt.recommended:
            return opt
    return NLLB_OPTIONS[0]


def recommended_asr_option() -> OfflineModelOption:
    for opt in ASR_OPTIONS:
        if opt.recommended:
            return opt
    return ASR_OPTIONS[0]


def format_ram_label(ram_mb: int) -> str:
    """Human-readable RAM estimate, e.g. ~1.2GB or ~500MB."""
    if ram_mb >= 1000:
        gb = ram_mb / 1000.0
        text = f"{gb:.1f}".rstrip("0").rstrip(".")
        return f"~{text}GB"
    return f"~{ram_mb}MB"


def format_option_label(opt: OfflineModelOption) -> str:
    """Combo label: title + disk/RAM + optional recommended mark."""
    ram = format_ram_label(opt.ram_mb)
    label = f"{opt.title}（约{opt.download_mb}MB↓ / {ram}内存）"
    if opt.recommended:
        label += "★推荐"
    return label


def format_kokoro_info(opt: OfflineModelOption | None = None) -> str:
    """Read-only info line for the fixed Kokoro TTS stack."""
    item = opt or (KOKORO_OPTIONS[0] if KOKORO_OPTIONS else None)
    if item is None:
        return "语音：Kokoro（本地）"
    ram = format_ram_label(item.ram_mb)
    return f"语音：{item.title} · 下载约 {item.download_mb}MB · 运行约 {ram.replace('~', '')} 内存"
