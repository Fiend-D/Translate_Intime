"""Central catalog of offline MT/TTS model options for economy mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class OfflineModelOption:
    id: str  # HF id or logical id
    title: str  # short Chinese title
    role: Literal["mt", "tts"]
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


def nllb_option_by_id(model_id: str | None) -> OfflineModelOption | None:
    if not model_id:
        return None
    needle = model_id.strip()
    for opt in NLLB_OPTIONS:
        if opt.id == needle:
            return opt
    return None


def recommended_nllb_option() -> OfflineModelOption:
    for opt in NLLB_OPTIONS:
        if opt.recommended:
            return opt
    return NLLB_OPTIONS[0]


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
    return (
        f"语音：{item.title} · 下载约 {item.download_mb}MB · 运行约 {ram.replace('~', '')} 内存"
    )
