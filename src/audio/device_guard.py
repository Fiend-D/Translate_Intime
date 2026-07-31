"""Device selection helpers: hide risky devices, detect VB-Cable, block feedback loops."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field
from typing import Any

from src.gui.device_labels import classify_device

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class DeviceIssue:
    level: str  # "error" | "warn" | "info"
    message: str


@dataclass
class VbCableReport:
    """Result of one-click virtual-cable link detection."""

    has_cable_input: bool = False
    has_cable_output: bool = False
    cable_input_id: Any | None = None
    cable_input_name: str = ""
    cable_output_id: Any | None = None
    cable_output_name: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.has_cable_input and self.has_cable_output

    def summary(self) -> str:
        lines = [
            "虚拟声卡链路检测",
            "",
            f"CABLE Input（译文应播到这里）：{'✓ ' + self.cable_input_name if self.has_cable_input else '✗ 未找到'}",
            f"CABLE Output（游戏麦克风应选这个）：{'✓ ' + self.cable_output_name if self.has_cable_output else '✗ 未找到'}",
            "",
        ]
        if self.ok:
            lines.append("链路完整。推荐：")
            lines.append("真实麦克风 → 本应用 → CABLE Input → CABLE Output → 游戏/Discord 麦克风")
            lines.append("游戏字幕捕获请选「系统扬声器 Loopback」，不要选 CABLE Output。")
        else:
            if IS_WINDOWS:
                lines.append("未检测到完整 VB-Cable。请安装：https://vb-audio.com/Cable/")
                lines.append("安装后重启应用再点「检测虚拟声卡」。")
            else:
                lines.append("Linux 可用 Pulse/PipeWire 虚拟 sink 代替；译文输出到虚拟 sink，")
                lines.append("游戏麦克风用其 monitor；字幕捕获用真实扬声器的 monitor。")
        if self.notes:
            lines.append("")
            lines.extend(self.notes)
        lines.append("")
        lines.append(vb_cable_setup_hint())
        return "\n".join(lines)


_VIRTUAL_MIC_PATTERNS = (
    r"cable\s*output",
    r"vb-audio",
    r"\.monitor\b",
    r"wasapi_loopback:",
    r"wasapi_proc_exclude:",
    r"\[loopback\]",
    r"null\s*sink",
    r"translator_virtual",
    r"stereo\s*mix",
    r"what\s*u\s*hear",
    r"wave\s*out\s*mix",
)


def _blob(name: Any, device_id: Any = None) -> str:
    return f"{name} {device_id}".lower()


def is_virtual_or_loopback_input(name: Any, device_id: Any = None) -> bool:
    """True if this input is unsafe as a real microphone (feedback risk)."""
    text = _blob(name, device_id)
    kind = classify_device(str(name or ""), device_id)
    if kind in {"system_loopback", "system_monitor", "vb_capture", "null_sink", "app_virtual"}:
        return True
    return any(re.search(p, text) for p in _VIRTUAL_MIC_PATTERNS)


def is_vb_cable_input(name: Any, device_id: Any = None) -> bool:
    """CABLE Input / Voicemeeter virtual sink that apps play into (teammates hear via
    CABLE Output / Voicemeeter Out Bn)."""
    text = _blob(name, device_id)
    kind = classify_device(str(name or ""), device_id)
    if kind in {"vb_to_game", "app_virtual"}:
        return True
    if re.search(r"cable\s*input|vb-audio.*input|translator_virtual_sink", text):
        return True
    # Voicemeeter: main VAIO Input, AUX Input, VAIO3 Input, In 1..5
    return bool(
        re.search(
            r"voicemeeter\s+(aux\s+)?input\b|voicemeeter\s+vaio3\s+input|"
            r"voicemeeter\s+in\s+\d",
            text,
        )
    )


def is_vb_cable_capture(name: Any, device_id: Any = None) -> bool:
    """CABLE Output / Voicemeeter Out [A|B]n — what games use as mic; must NOT be a
    game-subtitle capture source because the app's own TTS would loop back."""
    text = _blob(name, device_id)
    kind = classify_device(str(name or ""), device_id)
    if kind == "vb_capture":
        return True
    if re.search(r"cable\s*output|vb-audio.*output", text):
        return True
    # Voicemeeter Out A1..A5 / B1..B3 or Voicemeeter Out 1..8 (Point N)
    return bool(re.search(r"voicemeeter\s+out\b", text))


def is_system_loopback_capture(name: Any, device_id: Any = None) -> bool:
    text = _blob(name, device_id)
    kind = classify_device(str(name or ""), device_id)
    if kind in {"system_loopback", "system_monitor"}:
        # exclude virtual sinks' monitors that are the cable path itself
        if is_vb_cable_input(name, device_id) or is_vb_cable_capture(name, device_id):
            return False
        return not ("translator_virtual" in text and "monitor" in text)
    return bool(
        re.search(r"wasapi_loopback:|wasapi_proc_exclude:|\[loopback\]|\.monitor\b", text)
        and not is_vb_cable_capture(name, device_id)
        and "cable" not in text
    )


def is_process_exclude_capture(name: Any, device_id: Any = None) -> bool:
    """True for the Windows driverless loopback that excludes this app."""
    return "wasapi_proc_exclude:" in _blob(name, device_id)


def _endpoint_key(name: Any, device_id: Any = None) -> str:
    """Normalize render/capture labels enough to compare physical endpoints."""
    text = _blob(name, device_id)
    text = re.sub(r"wasapi_loopback:", " ", text)
    text = re.sub(r"wasapi_proc_exclude:", " ", text)
    text = re.sub(r"\[(?:loopback|默认|推荐)[^\]]*\]", " ", text)
    text = re.sub(
        r"\b(?:loopback|monitor|default|默认|系统声音|回采|免驱动|排除本应用)\b", " ", text
    )
    text = re.sub(r"^(?:🔊|🎧|🔁)\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def shares_physical_output_path(
    *,
    output_name: str,
    output_device: Any,
    loopback_name: str,
    loopback_device: Any,
) -> bool:
    """True when playback output is the same physical endpoint being looped back."""
    if is_process_exclude_capture(loopback_name, loopback_device):
        return False
    if shares_virtual_cable_path(
        output_name=output_name,
        output_device=output_device,
        loopback_name=loopback_name,
        loopback_device=loopback_device,
    ):
        return True
    if not is_system_loopback_capture(loopback_name, loopback_device):
        return False
    out_key = _endpoint_key(output_name, output_device)
    lb_key = _endpoint_key(loopback_name, loopback_device)
    if not out_key or not lb_key:
        return False
    if (
        output_device is not None
        and loopback_device is not None
        and str(output_device) == str(loopback_device)
    ):
        return True
    return out_key in lb_key or lb_key in out_key


def shares_virtual_cable_path(
    *,
    output_name: str,
    output_device: Any,
    loopback_name: str,
    loopback_device: Any,
) -> bool:
    """True if TTS output and game capture are on the same VB-Cable virtual line."""
    out_is_vb = is_vb_cable_input(output_name, output_device)
    lb_is_vb = is_vb_cable_capture(loopback_name, loopback_device) or is_vb_cable_input(
        loopback_name, loopback_device
    )
    if out_is_vb and lb_is_vb:
        return True
    return bool(
        output_device is not None
        and loopback_device is not None
        and str(output_device) == str(loopback_device)
    )


def find_preferred_vb_output(devices: list[dict[str, Any]]) -> Any | None:
    """Pick CABLE Input / Voicemeeter Input (VAIO) / virtual sink from output list.

    Order of preference:
      1. CABLE Input (classic, name collision-free)
      2. Voicemeeter main VAIO Input
      3. Voicemeeter AUX Input
      4. Voicemeeter In 1..5 / VAIO3 Input
    """
    scored: list[tuple[int, Any]] = []
    for d in devices:
        name = d.get("name", "")
        did = d.get("index", d.get("id"))
        text = _blob(name, did)
        if not is_vb_cable_input(name, did):
            continue
        if re.search(r"cable\s*input", text):
            scored.append((0, did))
        elif re.search(r"voicemeeter input\b", text) and "aux" not in text and "vaio3" not in text:
            scored.append((1, did))
        elif "aux" in text:
            scored.append((2, did))
        elif "vaio3" in text:
            scored.append((3, did))
        else:
            scored.append((4, did))
    scored.sort(key=lambda x: x[0])
    return scored[0][1] if scored else None


def find_preferred_system_loopback(devices: list[dict[str, Any]]) -> Any | None:
    """Prefer real speaker loopback/monitor and never VB-Cable or legacy fake IDs."""
    scored: list[tuple[int, Any]] = []
    for d in devices:
        name = str(d.get("name", ""))
        did = d.get("index", d.get("id"))
        if is_vb_cable_capture(name, did) or is_vb_cable_input(name, did):
            continue
        text = _blob(name, did)
        if "wasapi_proc_exclude:" in text:
            continue
        if "wasapi_loopback:" in text or "[loopback]" in text:
            scored.append((0, did))
        elif ".monitor" in text and "null" not in text:
            scored.append((1, did))
        elif is_system_loopback_capture(name, did):
            scored.append((2, did))
    scored.sort(key=lambda x: x[0])
    return scored[0][1] if scored else None


def resolve_capture_backend(
    backend: str,
    *,
    configured: Any | None = None,
    devices: list[dict[str, Any]] | None = None,
) -> Any | None:
    """Pick loopback device id from capture_backend + optional configured id."""
    if configured is not None and configured != "":
        if str(configured).startswith("wasapi_proc_exclude:"):
            configured = None  # migrate the legacy fake process-exclude selection
        else:
            return configured

    if devices is None:
        return None

    return find_preferred_system_loopback(devices)


def detect_vb_cable_link(
    *,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> VbCableReport:
    """One-click scan for CABLE Input / CABLE Output (or Linux virtual sink equivalents)."""
    report = VbCableReport()

    for d in outputs:
        name = str(d.get("name", ""))
        did = d.get("index", d.get("id"))
        if is_vb_cable_input(name, did):
            report.has_cable_input = True
            report.cable_input_id = did
            report.cable_input_name = name
            break

    for d in inputs:
        name = str(d.get("name", ""))
        did = d.get("index", d.get("id"))
        if is_vb_cable_capture(name, did):
            report.has_cable_output = True
            report.cable_output_id = did
            report.cable_output_name = name
            break

    # Linux: virtual sink as "input" to game may appear as monitor on input list
    if not report.has_cable_input:
        for d in outputs:
            name = str(d.get("name", ""))
            did = d.get("index", d.get("id"))
            if "translator_virtual" in _blob(name, did) or "null_sink" in _blob(name, did):
                report.has_cable_input = True
                report.cable_input_id = did
                report.cable_input_name = name
                report.notes.append("检测到本地虚拟 sink，可作为译文输出。")
                break

    return report


def validate_channel_devices(
    channel: str,
    *,
    input_device: Any,
    input_name: str,
    output_device: Any,
    output_name: str,
    loopback_device: Any,
    loopback_name: str,
    play_mic_voice: bool = False,
    play_game_voice: bool = False,
    mic_channel_active: bool = False,
    game_channel_active: bool = False,
) -> list[DeviceIssue]:
    """Return blocking/warning issues before starting a channel."""
    issues: list[DeviceIssue] = []

    if channel == "mic":
        if is_virtual_or_loopback_input(input_name, input_device):
            issues.append(
                DeviceIssue(
                    "error",
                    "麦克风选成了虚拟回环/系统回采，容易自反馈啸叫。请改选真实麦克风。",
                )
            )
        # Starting mic→cable while game is capturing the same virtual line
        if (
            play_mic_voice
            and is_vb_cable_input(output_name, output_device)
            and game_channel_active
            and shares_virtual_cable_path(
                output_name=output_name,
                output_device=output_device,
                loopback_name=loopback_name,
                loopback_device=loopback_device,
            )
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "游戏字幕正在捕获虚拟声卡，与同传输出共用一条虚拟线，"
                    "会把译文再识别成字幕。请把「游戏声音」改成系统扬声器 Loopback，"
                    "或先停游戏字幕。",
                )
            )
        if (
            output_device is not None
            and loopback_device is not None
            and str(output_device) == str(loopback_device)
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "译文输出设备与游戏捕获源是同一设备，必现回灌。请分开选择。",
                )
            )
        if (
            play_mic_voice
            and game_channel_active
            and shares_physical_output_path(
                output_name=output_name,
                output_device=output_device,
                loopback_name=loopback_name,
                loopback_device=loopback_device,
            )
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "麦克风译文正在播到游戏字幕捕获的同一扬声器/耳机 Loopback，"
                    "会被再次识别。请把译文输出到 CABLE Input / 另一设备。",
                )
            )
        if (
            play_mic_voice
            and game_channel_active
            and output_device is None
            and is_system_loopback_capture(loopback_name, loopback_device)
            and not is_process_exclude_capture(loopback_name, loopback_device)
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "已开启译文语音但输出设备为默认，同时游戏字幕使用经典 Loopback。"
                    "默认扬声器很可能被再次捕获。请明确选择 CABLE Input / 另一只设备，"
                    "经典 Loopback 不能排除本应用。",
                )
            )
        if IS_WINDOWS and output_device is None and play_mic_voice:
            issues.append(
                DeviceIssue(
                    "warn",
                    "已开语音输出但未指定设备。建议安装 VB-Cable 并选 CABLE Input，"
                    "游戏麦克风选 CABLE Output。",
                )
            )
        elif play_mic_voice and output_name and not is_vb_cable_input(output_name, output_device):
            issues.append(
                DeviceIssue(
                    "info",
                    "译文输出不是 CABLE Input。自己听没问题；若要让游戏队友听到，请改选 CABLE Input。",
                )
            )

    if channel == "game":
        if loopback_device is None and not (loopback_name and "默认" not in loopback_name):
            issues.append(
                DeviceIssue(
                    "warn",
                    "未明确选择游戏声音来源，将使用默认捕获；若无字幕请改选系统扬声器/耳机的 Loopback。",
                )
            )
        if is_vb_cable_capture(loopback_name, loopback_device) or is_vb_cable_input(
            loopback_name, loopback_device
        ):
            if mic_channel_active and is_vb_cable_input(output_name, output_device):
                issues.append(
                    DeviceIssue(
                        "error",
                        "游戏字幕捕获源是虚拟声卡，而麦克风同传正往同一条虚拟线播译文，"
                        "会互相干扰。请改选「系统正在播放的声音 / Loopback」。",
                    )
                )
            else:
                issues.append(
                    DeviceIssue(
                        "warn",
                        "游戏捕获源像是虚拟声卡。同传若也走 CABLE，字幕会吃到译文。"
                        "建议改选系统扬声器 Loopback。",
                    )
                )
        if (
            play_game_voice or (play_mic_voice and mic_channel_active)
        ) and shares_physical_output_path(
            output_name=output_name,
            output_device=output_device,
            loopback_name=loopback_name,
            loopback_device=loopback_device,
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "语音/音乐输出与游戏字幕捕获共用同一扬声器/耳机 Loopback，"
                    "会造成回灌或回声。请把译文/音乐输出到 "
                    "CABLE Input 或另一只设备。",
                )
            )
        if (
            (play_game_voice or (play_mic_voice and mic_channel_active))
            and output_device is None
            and is_system_loopback_capture(loopback_name, loopback_device)
            and not is_process_exclude_capture(loopback_name, loopback_device)
        ):
            issues.append(
                DeviceIssue(
                    "error",
                    "语音输出为默认设备，游戏字幕使用经典 Loopback，无法保证不会捕获本应用声音。"
                    "请明确选择 CABLE Input / 另一个不被捕获的输出设备。",
                )
            )

    return issues


def vb_cable_setup_hint() -> str:
    if IS_WINDOWS:
        return (
            "VB-Cable 推荐链路：\n"
            "真实麦克风 → 本应用 → CABLE Input →（系统）CABLE Output → 游戏/语音软件麦克风\n"
            "游戏字幕：选扬声器/耳机的 Loopback，不要选 CABLE Output。\n"
            "下载：https://vb-audio.com/Cable/"
        )
    return (
        "Linux 可用 Pulse/PipeWire 空沉或虚拟 sink 代替 VB-Cable；\n"
        "译文输出到虚拟 sink，游戏捕获该 sink 的 monitor。\n"
        "游戏字幕请捕获真实扬声器的 monitor，避免与虚拟 sink 回环。"
    )
