"""Economy TTS engines and their voice lists (local and cloud).

Voice IDs marked ``xiaozhi`` are the defaults used by xiaozhi-esp32-server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TtsKind = Literal["local", "cloud", "auto"]


@dataclass(frozen=True)
class TtsVoice:
    id: str
    name: str
    note: str = ""

    @property
    def label(self) -> str:
        text = f"{self.name} · {self.id}"
        return f"{text}（{self.note}）" if self.note else text


@dataclass(frozen=True)
class TtsEngineInfo:
    id: str
    title: str
    kind: TtsKind
    needs: str
    note: str = ""
    voices_zh: tuple[TtsVoice, ...] = field(default_factory=tuple)
    voices_en: tuple[TtsVoice, ...] = field(default_factory=tuple)

    @property
    def has_voices(self) -> bool:
        return bool(self.voices_zh or self.voices_en)

    @property
    def combo_label(self) -> str:
        tag = {"local": "本地", "cloud": "云端", "auto": "自动"}[self.kind]
        return f"{tag} · {self.title}"


_XZ = "小智默认"

# A spread of the numbered speakers in voices-v1.1-zh.bin (55 female, 45 male in total).
_KOKORO_ZF = ("001", "002", "003", "004", "005", "006", "017", "019", "024", "032", "044", "059")
_KOKORO_ZM = ("009", "010", "011", "012", "013", "014", "020", "025", "031", "050", "065", "080")
KOKORO_VOICES_ZH = tuple(TtsVoice(f"zf_{n}", f"女声 {n}", "v1.1-zh") for n in _KOKORO_ZF) + tuple(
    TtsVoice(f"zm_{n}", f"男声 {n}", "v1.1-zh") for n in _KOKORO_ZM
)
KOKORO_VOICES_EN = (
    TtsVoice("af_bella", "Bella", "美式女声"),
    TtsVoice("af_heart", "Heart", "美式女声"),
    TtsVoice("af_nicole", "Nicole", "美式女声"),
    TtsVoice("af_sarah", "Sarah", "美式女声"),
    TtsVoice("af_sky", "Sky", "美式女声"),
    TtsVoice("am_adam", "Adam", "美式男声"),
    TtsVoice("am_michael", "Michael", "美式男声"),
    TtsVoice("bf_emma", "Emma", "英式女声"),
    TtsVoice("bm_george", "George", "英式男声"),
)

EDGE_VOICES_ZH = (
    TtsVoice("zh-CN-XiaoxiaoNeural", "晓晓", _XZ),
    TtsVoice("zh-CN-XiaoyiNeural", "晓伊"),
    TtsVoice("zh-CN-YunxiNeural", "云希"),
    TtsVoice("zh-CN-YunjianNeural", "云健"),
    TtsVoice("zh-CN-YunyangNeural", "云扬", "新闻播报"),
    TtsVoice("zh-CN-YunxiaNeural", "云夏", "少年"),
    TtsVoice("zh-CN-liaoning-XiaobeiNeural", "晓北", "东北话"),
    TtsVoice("zh-CN-shaanxi-XiaoniNeural", "晓妮", "陕西话"),
    TtsVoice("zh-HK-HiuMaanNeural", "曉曼", "粤语"),
    TtsVoice("zh-TW-HsiaoChenNeural", "曉臻", "台湾"),
)
EDGE_VOICES_EN = (
    TtsVoice("en-US-JennyNeural", "Jenny"),
    TtsVoice("en-US-AriaNeural", "Aria"),
    TtsVoice("en-US-GuyNeural", "Guy"),
    TtsVoice("en-US-AvaMultilingualNeural", "Ava", "多语种"),
    TtsVoice("en-US-AndrewMultilingualNeural", "Andrew", "多语种"),
    TtsVoice("en-GB-SoniaNeural", "Sonia", "英式"),
    TtsVoice("en-GB-RyanNeural", "Ryan", "英式"),
)

DOUBAO_VOICES_ZH = (
    TtsVoice("zh_female_wanwanxiaohe_moon_bigtts", "湾湾小何", "小智默认 · 1.0"),
    TtsVoice("zh_female_xiaohe_uranus_bigtts", "小何", "小智 V2 默认 · 2.0"),
    TtsVoice("zh_female_vv_uranus_bigtts", "Vivi", "2.0"),
    TtsVoice("zh_male_shaonianzixin_moon_bigtts", "少年梓辛", "中英"),
    TtsVoice("zh_female_shuangkuaisisi_moon_bigtts", "爽快思思", "中英"),
    TtsVoice("zh_male_wennuanahu_moon_bigtts", "温暖阿虎", "中英"),
    TtsVoice("zh_female_tianmeixiaoyuan_moon_bigtts", "甜美小源"),
    TtsVoice("zh_female_qingchezizi_moon_bigtts", "清澈梓梓"),
    TtsVoice("zh_female_linjianvhai_moon_bigtts", "邻家女孩"),
    TtsVoice("zh_male_yangguangqingnian_moon_bigtts", "阳光青年"),
    TtsVoice("zh_male_yuanboxiaoshu_moon_bigtts", "渊博小叔"),
    TtsVoice("zh_male_jingqiangkanye_moon_bigtts", "京腔侃爷", "北京话"),
)
DOUBAO_VOICES_EN = (
    TtsVoice("zh_female_shuangkuaisisi_moon_bigtts", "爽快思思 Skye", "中英"),
    TtsVoice("zh_male_wennuanahu_moon_bigtts", "温暖阿虎 Alvin", "中英"),
    TtsVoice("zh_male_shaonianzixin_moon_bigtts", "少年梓辛 Brayan", "中英"),
    TtsVoice("zh_female_vv_uranus_bigtts", "Vivi", "2.0 多语种"),
)

COSYVOICE_VOICES_ZH = (
    TtsVoice("longxiaochun_v2", "龙小淳", "小智阿里云默认"),
    TtsVoice("longcheng_v2", "龙橙", "小智百炼默认"),
    TtsVoice("longxiaoxia_v2", "龙小夏"),
    TtsVoice("longhua_v2", "龙华"),
    TtsVoice("longwan_v2", "龙婉"),
    TtsVoice("longshu_v2", "龙书", "播报"),
    TtsVoice("longyue_v2", "龙悦"),
)
COSYVOICE_VOICES_EN = (
    TtsVoice("loongbella_v2", "Bella", "中英"),
    TtsVoice("longxiaochun_v2", "龙小淳", "中英"),
    TtsVoice("longcheng_v2", "龙橙", "中英"),
)

OPENAI_VOICES = (
    TtsVoice("alloy", "alloy"),
    TtsVoice("onyx", "onyx", "小智 OpenAI 默认"),
    TtsVoice("nova", "nova"),
    TtsVoice("shimmer", "shimmer"),
    TtsVoice("zf_xiaoxiao", "zf_xiaoxiao", "Kokoro-FastAPI"),
)

TTS_ENGINES: dict[str, TtsEngineInfo] = {
    info.id: info
    for info in (
        TtsEngineInfo(
            id="kokoro",
            title="Kokoro-82M",
            kind="local",
            needs="无需账号，首次自动下载约 350MB",
            note="离线，中英自然，CPU 即可",
            voices_zh=KOKORO_VOICES_ZH,
            voices_en=KOKORO_VOICES_EN,
        ),
        TtsEngineInfo(
            id="melo",
            title="MeloTTS 中英混读（sherpa-onnx）",
            kind="local",
            needs="无需账号，首次自动下载约 170MB",
            note="离线单音色，中英夹杂句子更自然",
        ),
        TtsEngineInfo(
            id="edge",
            title="Edge TTS（微软，免费）",
            kind="cloud",
            needs="无需账号，需联网",
            note="小智服务端默认语音",
            voices_zh=EDGE_VOICES_ZH,
            voices_en=EDGE_VOICES_EN,
        ),
        TtsEngineInfo(
            id="doubao",
            title="豆包语音合成（火山引擎）",
            kind="cloud",
            needs="火山 API Key 或 AppID + Token，需开通语音合成大模型",
            note="小智官方音色「湾湾小何」",
            voices_zh=DOUBAO_VOICES_ZH,
            voices_en=DOUBAO_VOICES_EN,
        ),
        TtsEngineInfo(
            id="cosyvoice",
            title="CosyVoice（阿里云百炼）",
            kind="cloud",
            needs="DashScope API Key",
            note="小智 AliBL / 阿里云流式 TTS 同款",
            voices_zh=COSYVOICE_VOICES_ZH,
            voices_en=COSYVOICE_VOICES_EN,
        ),
        TtsEngineInfo(
            id="openai",
            title="OpenAI 兼容接口（自建 / 第三方）",
            kind="cloud",
            needs="接口地址；GPT-SoVITS / FishSpeech / IndexTTS / 硅基流动等",
            note="走 /v1/audio/speech",
            voices_zh=OPENAI_VOICES,
            voices_en=OPENAI_VOICES,
        ),
        TtsEngineInfo(
            id="auto",
            title="自动（Kokoro → Edge → 系统语音）",
            kind="auto",
            needs="本地优先，失败时联网回退",
        ),
    )
}

TTS_ENGINE_ORDER = ("kokoro", "melo", "edge", "doubao", "cosyvoice", "openai", "auto")

DEFAULT_TTS_VOICES: dict[str, tuple[str, str]] = {
    "kokoro": ("zf_001", "af_bella"),
    "edge": ("zh-CN-XiaoxiaoNeural", "en-US-JennyNeural"),
    "doubao": ("zh_female_wanwanxiaohe_moon_bigtts", "zh_female_shuangkuaisisi_moon_bigtts"),
    "cosyvoice": ("longxiaochun_v2", "loongbella_v2"),
    "openai": ("alloy", "alloy"),
}


def tts_engine(engine_id: str | None) -> TtsEngineInfo:
    return TTS_ENGINES.get((engine_id or "").strip().lower(), TTS_ENGINES["kokoro"])


def voice_fields(engine_id: str) -> tuple[str, str] | None:
    """Config field names (zh, en) holding this engine's voices."""
    if engine_id not in DEFAULT_TTS_VOICES:
        return None
    return f"economy_{engine_id}_voice_zh", f"economy_{engine_id}_voice_en"


def doubao_resource_id(voice: str) -> str:
    """Seed-TTS 2.0 voices end in ``_uranus_bigtts``; the rest are 1.0 big-model voices."""
    return "seed-tts-2.0" if "_uranus_" in (voice or "") else "seed-tts-1.0"


__all__ = [
    "DEFAULT_TTS_VOICES",
    "TTS_ENGINES",
    "TTS_ENGINE_ORDER",
    "TtsEngineInfo",
    "TtsVoice",
    "doubao_resource_id",
    "tts_engine",
    "voice_fields",
]
