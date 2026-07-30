"""本地流式 ASR via sherpa-onnx (ONNX Runtime, 低延迟).

sherpa-onnx 是下一代 Kaldi 团队 (k2-fsa) 的 ONNX 流式语音识别运行时,
复用项目已有的 onnxruntime, 无需 PyTorch / TensorFlow.
模型缓存在项目内 resource/asr/<slug>/, 与 NLLB / Kokoro / Silero VAD 风格一致.

多模型路由:
  英文输入 → 纯英文流式 Zipformer (识别准确, 不会被中文 token 干扰)
  中文输入 → 中英双语流式 Zipformer (兼容中英混说)
  根据 recognize(language=...) 参数自动路由, 无需用户手动切换.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np

from src.utils.logger import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_ROOT = _PROJECT_ROOT / "resource" / "asr"
_LOG_INTERVAL_SEC = 30.0
_CHUNK = 1024 * 256

# HuggingFace 镜像源, 与 nllb_mt.py 保持一致 (优先 hf-mirror.com)
_HF_HOSTS = (
    "https://hf-mirror.com",
    "https://huggingface.co",
)

# 各模型文件的最低体积 (字节)。小于该值视为未下完/损坏。
# 数值取自官方 HF Content-Length 的约 95%, 允许镜像轻微差异。
_MIN_FILE_BYTES: dict[str, dict[str, int]] = {
    "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20": {
        "encoder-epoch-99-avg-1.int8.onnx": 170_000_000,
        "decoder-epoch-99-avg-1.int8.onnx": 12_000_000,
        "joiner-epoch-99-avg-1.int8.onnx": 3_000_000,
        "tokens.txt": 40_000,
    },
    "sherpa-onnx-streaming-zipformer-en-2023-06-26": {
        "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx": 65_000_000,
        "decoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx": 1_200_000,
        "joiner-epoch-99-avg-1-chunk-16-left-64.int8.onnx": 200_000,
        "tokens.txt": 4_000,
    },
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17": {
        "model.int8.onnx": 200_000_000,
        "tokens.txt": 100_000,
    },
}


def _hf_urls(repo: str, fname: str) -> list[str]:
    """生成 HF resolve URL 列表, 镜像优先."""
    return [f"{h.rstrip('/')}/{repo}/resolve/main/{fname}" for h in _HF_HOSTS]


def _looks_like_onnx(path: Path) -> bool:
    """粗检 ONNX protobuf 头 (含量化模型的 onnx.quantize 标记)."""
    try:
        with path.open("rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if len(head) < 16:
        return False
    # 常见: IR version protobuf + producer_name "onnx..." / "onnx.quantize"
    return b"onnx" in head


def _file_ok(path: Path, *, min_bytes: int = 1) -> bool:
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < min_bytes:
        return False
    if path.suffix == ".onnx" and not _looks_like_onnx(path):
        return False
    return True


def _purge_model_files(info: dict[str, Any], cache_dir: Path) -> None:
    """删除缓存中的模型文件 (含 .part), 以便强制重下."""
    for fname in info.get("files", {}).values():
        for candidate in (cache_dir / fname, cache_dir / f"{fname}.part"):
            try:
                if candidate.exists():
                    candidate.unlink()
                    logger.info(f"已删除损坏/不完整 ASR 文件: {candidate}")
            except OSError as exc:
                logger.warning(f"无法删除 {candidate}: {exc}")


# ---- 流式模型注册表 -------------------------------------------------------
# 每个模型对应一个 HuggingFace 仓库, 内含:
#   encoder / decoder / joiner / tokens 四个文件
# 优先使用 int8 量化版, 体积减半且识别精度基本无损.
# sherpa-onnx 流式 Transducer 模型支持在线增量识别, 延迟通常 <200ms.

_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    # 中英双语流式 Zipformer (覆盖中英混说场景)
    "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20": {
        "title": "Zipformer 中英双语 流式",
        "lang": "bilingual",
        "download_mb": 200,
        "ram_mb": 500,
        "quality": "良好，中英混说识别稳",
        "note": "中文方向推荐：覆盖中英混说",
        "recommended_for_zh": True,
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "files": {
            "encoder": "encoder-epoch-99-avg-1.int8.onnx",
            "decoder": "decoder-epoch-99-avg-1.int8.onnx",
            "joiner": "joiner-epoch-99-avg-1.int8.onnx",
            "tokens": "tokens.txt",
        },
    },
    # 纯英文流式 Zipformer (英文识别准确, 不会被中文 token 干扰)
    "sherpa-onnx-streaming-zipformer-en-2023-06-26": {
        "title": "Zipformer 英文 流式",
        "lang": "en",
        "download_mb": 80,
        "ram_mb": 300,
        "quality": "英文识别准确，不会误判为中文",
        "note": "英文方向推荐：纯英文模型，准确度高",
        "recommended_for_en": True,
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "files": {
            "encoder": "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
            "decoder": "decoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
            "joiner": "joiner-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
            "tokens": "tokens.txt",
        },
    },
}

# ---- Offline 模型注册表 --------------------------------------------------
# SenseVoice (达摩院 FunASR): 多语言 (中英日韩粤), 噪声鲁棒, 自带标点和大小写.
# 使用 OfflineRecognizer, 需整段送入, 延迟略高于流式但对游戏实况识别更准.

_OFFLINE_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17": {
        "title": "SenseVoice 多语言 离线",
        "lang": "auto",
        "download_mb": 240,
        "ram_mb": 600,
        "quality": "优秀，噪声鲁棒，自带标点大小写",
        "note": "推荐游戏实况：多语言，对 BGM/音效抗干扰",
        "recommended": True,
        "repo": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "files": {
            "model": "model.int8.onnx",
            "tokens": "tokens.txt",
        },
        "kind": "sense_voice",
    },
}

# 语言 → 模型 id 的自动映射 (model_id="auto" 时使用, 仅流式)
_LANG_MODEL_MAP: dict[str, str] = {
    "en": "sherpa-onnx-streaming-zipformer-en-2023-06-26",
    "zh": "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
}
# 需要预加载的语言 (流式模型)
_PRELOAD_LANGS = ("zh", "en")


def model_slug(model_id: str) -> str:
    """Filesystem-safe directory name."""
    raw = (model_id or "").strip().rstrip("/")
    slug = raw.split("/")[-1] if "/" in raw else raw
    return re.sub(r"[^\w.\-]+", "_", slug) or "sherpa_asr"


def sherpa_cache_dir(model_id: str) -> Path:
    return _CACHE_ROOT / model_slug(model_id)


def list_local_asr_model_ids() -> list[str]:
    return list(_MODEL_REGISTRY.keys()) + list(_OFFLINE_MODEL_REGISTRY.keys())


def local_asr_model_info(model_id: str) -> dict[str, Any] | None:
    mid = (model_id or "").strip()
    return _MODEL_REGISTRY.get(mid) or _OFFLINE_MODEL_REGISTRY.get(mid)


def _is_offline_model(model_id: str) -> bool:
    """True if model_id is in the offline registry (SenseVoice etc.)."""
    return (model_id or "").strip() in _OFFLINE_MODEL_REGISTRY


# 英文流式 Zipformer (LibriSpeech 训练) 输出全大写, 需后处理恢复大小写.
# 规则: 整体转小写 → 句首字母大写 → 还原 "i" → "I" (含缩写 i'm, i've, i'll...).
_I_PATTERN = re.compile(r"\bi(?=['\s]|$)", re.IGNORECASE)


def _recase_english(text: str) -> str:
    """把 LibriSpeech 模型的全大写输出转回正常大小写."""
    out = text.lower()
    # 句首大写 (按 . ! ? 切分)
    out = re.sub(
        r"(^|[.!?]\s+)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )
    # 还原 "i" → "I" (单独的 i, 包括 i'm, i've, i'll, i'd)
    out = _I_PATTERN.sub("I", out)
    return out


# SenseVoice 输出会带事件/情感标签: <|BGM|> <|Laughter|> <|HAPPY|> <|Noise|> 等.
# 这些标签对翻译无用, 且会干扰 MT, 需过滤.
_SENSE_TAG_PATTERN = re.compile(r"<\|[^|]+\|>")

# SenseVoice (offline) 对尾部静音/低能量段易产生语气词幻听.
# 表现: 句末标点后出现孤立语气词 (如 "你好。啊", "谢谢。嗯"), 甚至交替连续
#       ("你好。啊。嗯"). 这些语气词前有终止标点, 大概率是模型幻觉, 予以裁剪.
# 正则: 以标点 group(1) 开头, 后接任意数量的 "语气词+可选标点" 序列, 到结尾.
# 替换为 group(1) (保留第一个标点). 不影响正常语气词 (无标点在前, 如 "你好啊").
_TRAILING_FILLER_PATTERN = re.compile(
    r"([。！？.!?])(?:\s*[啊呀呢吧嘛嗯哼哦噢唉哎呜哇了的一是的在]\s*[。！？.!?]?)*\s*$"
)


def _clean_sensevoice_output(text: str) -> str:
    """过滤 SenseVoice 输出的事件标签和尾部幻听语气词."""
    if not text:
        return text
    # 1. 去掉 <|...|> 事件/情感标签
    out = _SENSE_TAG_PATTERN.sub("", text)
    # 2. 裁剪句末标点后的交替语气词序列 (幻听)
    #    一轮即可匹配整段交替序列; 循环兜底以防边缘情况
    prev = None
    while prev != out:
        prev = out
        out = _TRAILING_FILLER_PATTERN.sub(r"\1", out)
    return out.strip()


# 尾部静音裁剪: 从源头减少 SenseVoice 对低能量尾段的语气词幻听.
# 扫描 PCM 尾部, 按 32ms 窗口计算 RMS, 找到最后一个高于阈值的窗口,
# 保留到该窗口后 +80ms 余量, 其后截断. 安全限制: 最多裁剪 40%, 保留 ≥300ms.
_TRIM_WINDOW_MS = 32
_TRIM_MARGIN_MS = 80
_TRIM_RMS_THRESHOLD = 0.01  # 约 -40dB, 低于此视为静音/底噪
_TRIM_MIN_KEEP_MS = 300
_TRIM_MAX_RATIO = 0.4


def _trim_trailing_silence(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """裁剪 PCM float32 尾部低能量段, 减少模型对静音的幻听."""
    if samples.size == 0:
        return samples
    win = max(64, int(_TRIM_WINDOW_MS * sample_rate / 1000.0))
    margin = int(_TRIM_MARGIN_MS * sample_rate / 1000.0)
    min_keep = int(_TRIM_MIN_KEEP_MS * sample_rate / 1000.0)
    max_trim = int(samples.size * _TRIM_MAX_RATIO)
    if samples.size <= min_keep + win:
        return samples
    # 扫描下限: 不低于 min_keep, 且不超过 max_trim 比例
    scan_floor = max(min_keep, samples.size - max_trim)
    # 从尾向头扫描, 找最后一个 RMS 高于阈值的窗口.
    # 初始 last_loud_end = scan_floor: 若整个扫描范围都是静音, 则裁剪到下限+余量.
    last_loud_end = scan_floor
    pos = samples.size
    while pos - win >= scan_floor:
        pos -= win
        chunk = samples[pos : pos + win]
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        if rms >= _TRIM_RMS_THRESHOLD:
            last_loud_end = pos + win
            break
    keep_end = min(samples.size, last_loud_end + margin)
    if keep_end < min_keep:
        keep_end = min_keep
    if keep_end >= samples.size:
        return samples
    return samples[:keep_end]


# BGM 检测: 游戏实况中持续 BGM/音效段能量持续, 无明显停顿,
# SenseVoice 会误判为孤立标点/单字符 (如 "." "F." "The.").
# 核心判据: 人声段有明显的"说话-停顿"交替 (相对静音窗口多),
#           BGM 段能量持续无静音间隙 (相对静音窗口少).
# "相对静音" = 窗口 RMS < 中位数 × 0.3 (相对于本段能量的低谷).
_BGM_WINDOW_MS = 100
_BGM_MIN_WINDOWS = 20  # 至少 2 秒才判断
_BGM_MIN_RMS = 0.005  # 整体最大 RMS 低于此 → 纯静音, 不算 BGM
_BGM_QUIET_RATIO = 0.3  # 窗口 RMS < 中位数 × 此值 → "相对静音窗口"
_BGM_MAX_QUIET_FRAC = 0.15  # 相对静音窗口占比 < 此值 → BGM (无停顿)


def _is_likely_bgm(samples: np.ndarray, sample_rate: int) -> bool:
    """检测音频段是否像纯 BGM/音效 (无明显人声停顿).

    返回 True 表示应跳过识别, 避免产生 "." "F." "The." 等噪点输出.
    """
    if samples.size == 0:
        return False
    win = max(160, int(_BGM_WINDOW_MS * sample_rate / 1000.0))
    n_windows = samples.size // win
    if n_windows < _BGM_MIN_WINDOWS:
        return False  # 片段太短, 不做判断, 交给模型
    rms_list: list[float] = []
    for i in range(n_windows):
        chunk = samples[i * win : (i + 1) * win]
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        rms_list.append(rms)
    rms_arr = np.array(rms_list, dtype=np.float64)
    r_max = float(rms_arr.max())
    # 整体能量过低 → 纯静音, 不是 BGM (交给尾部裁剪处理)
    if r_max < _BGM_MIN_RMS:
        return False
    r_median = float(np.median(rms_arr))
    if r_median < 1e-5:
        r_median = 1e-5
    # 统计"相对静音"窗口: RMS 低于中位数 × 0.3
    # 人声段有明显的说话-停顿交替, 相对静音窗口多 (占比 > 15%);
    # BGM 段能量持续, 相对静音窗口少 (占比 < 15%).
    quiet_threshold = r_median * _BGM_QUIET_RATIO
    quiet_count = int(np.sum(rms_arr < quiet_threshold))
    quiet_frac = quiet_count / n_windows
    return quiet_frac < _BGM_MAX_QUIET_FRAC


# 无效输出过滤: SenseVoice 对纯 BGM/噪声段会输出孤立标点或单字符.
# 这类输出无翻译价值, 且会刷屏, 予以过滤.
# 规则:
#   - 纯标点 → 过滤
#   - 实质字符 < 2 → 过滤 (如 "F." "啊.")
#   - 单个英文单词 (无空格) 且 ≤4 字符 → 过滤 (如 "The." "No." "Yeah.")
#     游戏噪点常是这种孤立短词; 真实语音至少是一个短语
#   - 中文 ≥2 字 → 保留 (中文信息密度高, "你好" "谢谢" 都是有效输出)
_PUNCT_ONLY_PATTERN = re.compile(r"^[\s。！？.!?，,、；;：:~～…—\-]+$")
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _is_meaningless_output(text: str) -> bool:
    """判断 ASR 输出是否无意义 (纯标点 / 单字符 / 孤立短词).

    过滤游戏 BGM 段的噪点输出: "." "F." "No." "The." "Yeah." 等.
    保留有效短输出: "你好" "Hello world" "谢谢" 等.
    """
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    # 纯标点
    if _PUNCT_ONLY_PATTERN.match(stripped):
        return True
    # 去掉所有标点和空白后的实质字符
    letters = re.sub(r"[\s\W_]+", "", stripped, flags=re.UNICODE)
    if len(letters) < 2:
        return True
    # 含中日韩字符 → 保留 (中文信息密度高, 2 字即有效)
    if _CJK_PATTERN.search(stripped):
        return False
    # 纯英文/拉丁字符: 单个单词 (无空格) 且 ≤4 字符 → 噪点
    # 真实语音至少是短语 (含空格) 或较长单词
    if " " not in stripped and len(letters) <= 4:
        return True
    return False


class SherpaOnnxAsr:
    """本地 ASR (sherpa-onnx) — 流式 + 离线模型.

    - 流式 (OnlineRecognizer): 多模型按语言路由, 英文→英文模型, 中文→双语模型
    - 离线 (OfflineRecognizer): SenseVoice 单模型支持多语言, 噪声鲁棒, 自带标点
    - 懒下载: 首次调用时从镜像下载 ONNX 模型到 resource/asr/<slug>/
    - 热词: 流式 Transducer 支持 (hotwords_file + modified_beam_search)
    - 复用 onnxruntime, 无 PyTorch / TensorFlow 依赖
    """

    def __init__(
        self,
        *,
        model_id: str = "auto",
        auto_download: bool = True,
        device_preference: str = "auto",
        hotwords: list[str] | None = None,
        hotwords_score: float = 2.0,
    ) -> None:
        self._model_id = (model_id or "auto").strip().lower()
        self._auto_download = auto_download
        self._device_preference = (device_preference or "auto").strip().lower()
        self._started = False
        self._last_err_at = 0.0
        self._lock = threading.Lock()

        # 热词: 每行一个词, 写入临时文件供 OnlineRecognizer 加载.
        # 必须配合 modified_beam_search 解码, greedy_search 不支持热词.
        # 注意: 仅流式 Transducer 模型支持热词, SenseVoice 等离线模型不支持.
        self._hotwords = [w.strip() for w in (hotwords or []) if w and w.strip()]
        self._hotwords_score = float(hotwords_score)
        self._hotwords_file: Path | None = None

        # 按语言维护 recognizer 状态
        # lang_key → recognizer (OnlineRecognizer / OfflineRecognizer 实例)
        self._recognizers: dict[str, Any] = {}
        self._lang_loading: set[str] = set()
        self._lang_ready: set[str] = set()
        self._lang_failed: set[str] = set()

    # ---- 模型选择 ----

    @property
    def _is_offline(self) -> bool:
        """当前 model_id 是否为离线模型 (SenseVoice 等)."""
        return _is_offline_model(self._model_id)

    def _model_id_for_lang_key(self, lang_key: str) -> str:
        """根据 lang_key 返回模型 id.

        - 离线模型 / 固定 model_id: 所有语言都用同一个模型
        - auto: 按语言路由 (en→英文模型, zh→双语模型)
        """
        if self._model_id and self._model_id != "auto":
            return self._model_id
        return _LANG_MODEL_MAP.get(lang_key, _LANG_MODEL_MAP["zh"])

    def _lang_key_for_language(self, language: str) -> str:
        """外部 language 参数 → 内部 lang_key.

        - 离线模型 / 固定 model_id: 所有语言都映射到 "fixed"
        - auto: en → "en", zh/其他 → "zh"
        """
        if self._is_offline or (self._model_id and self._model_id != "auto"):
            return "fixed"
        lang = (language or "")[:2].lower()
        if lang == "en":
            return "en"
        return "zh"

    def _lang_keys_to_load(self) -> list[str]:
        """需要预加载的 lang_key 列表."""
        if self._is_offline or (self._model_id and self._model_id != "auto"):
            return ["fixed"]
        return list(_PRELOAD_LANGS)

    # ---- 公开接口 ----

    @property
    def configured(self) -> bool:
        """至少一个语言模型就绪即视为可用."""
        return bool(self._lang_ready)

    @property
    def warming_up(self) -> bool:
        """有模型正在加载且还没有任何模型就绪."""
        return bool(self._lang_loading) and not self._lang_ready

    @property
    def model_id(self) -> str:
        return self._model_id

    def start(self) -> None:
        self._started = True
        self.start_loading()

    def stop(self) -> None:
        self._started = False

    def start_loading(self) -> None:
        """异步加载所有需要的语言模型."""
        with self._lock:
            pending = [
                k
                for k in self._lang_keys_to_load()
                if k not in self._lang_ready
                and k not in self._lang_failed
                and k not in self._lang_loading
            ]
            if not pending:
                return
            for k in pending:
                self._lang_loading.add(k)

        for lang_key in pending:
            model_id = self._model_id_for_lang_key(lang_key)
            logger.info(f"正在加载本地 ASR ({lang_key}={model_id})…")
            thread = threading.Thread(
                target=self._load_lang_background,
                args=(lang_key,),
                name=f"sherpa-asr-loader-{lang_key}",
                daemon=True,
            )
            thread.start()

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        """根据 language 路由到对应模型的 recognizer 进行识别."""
        if not pcm or not self._started:
            return None
        lang_key = self._lang_key_for_language(language)
        if lang_key not in self._lang_ready:
            # 触发懒加载 (如果还没开始)
            if lang_key not in self._lang_failed and lang_key not in self._lang_loading:
                self.start_loading()
            return None
        recognizer = self._recognizers.get(lang_key)
        if recognizer is None:
            return None
        try:
            return self._recognize_with(recognizer, pcm, lang_key)
        except Exception as exc:
            now = time.time()
            if now - self._last_err_at >= _LOG_INTERVAL_SEC:
                self._last_err_at = now
                logger.warning(f"sherpa-onnx ASR recognize failed ({lang_key}): {exc}")
            return None

    # ---- 内部方法 ----

    def _recognize_with(self, recognizer: Any, pcm: bytes, lang_key: str) -> str | None:
        # PCM16 s16le → float32 [-1, 1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return None
        # 离线模型 (SenseVoice): 识别前裁剪尾部静音 + BGM 预判
        # BGM 预判: 游戏实况中纯 BGM/音效段能量平稳, 识别只会产生噪点, 直接跳过
        if self._is_offline:
            samples = _trim_trailing_silence(samples, 16000)
            if _is_likely_bgm(samples, 16000):
                return None
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)

        if self._is_offline:
            # Offline (SenseVoice): 一次性解码, result 在 stream.result.text
            recognizer.decode_stream(stream)
            result = getattr(stream, "result", None)
            text = ""
            if result is not None:
                text = getattr(result, "text", None) or (
                    result.get("text", "") if isinstance(result, dict) else str(result)
                )
            text = (text or "").strip()
            # SenseVoice: 过滤事件标签 + 裁剪尾部幻听语气词
            if text:
                text = _clean_sensevoice_output(text)
            # 过滤纯标点/单字符噪点输出 (如 "." "F." "No.")
            if _is_meaningless_output(text):
                return None
            return text or None

        # 流式 (OnlineRecognizer): 增量解码
        # Each recognize() call owns a complete temporary stream. Mark its input
        # finished so the transducer emits the final token instead of truncating
        # words at PCM chunk boundaries.
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result(stream)
        text = ""
        if isinstance(result, str):
            text = result
        else:
            text = getattr(result, "text", None) or (
                result.get("text", "") if isinstance(result, dict) else str(result)
            )
        text = (text or "").strip()
        # 英文流式 Zipformer (LibriSpeech 训练) 输出全大写, 做大小写后处理
        if text and lang_key == "en":
            text = _recase_english(text)
        return text or None

    def _load_lang_background(self, lang_key: str) -> None:
        ok = False
        try:
            ok = self._load_lang(lang_key)
        except Exception as exc:
            logger.warning(f"sherpa-onnx ASR ({lang_key}) 加载失败: {exc}")
            ok = False
        with self._lock:
            if ok:
                self._lang_ready.add(lang_key)
            else:
                self._lang_failed.add(lang_key)
            self._lang_loading.discard(lang_key)
        model_id = self._model_id_for_lang_key(lang_key)
        if ok:
            logger.info(f"sherpa-onnx ASR 已就绪 ({lang_key}={model_id})")
        else:
            logger.warning(
                f"sherpa-onnx ASR ({lang_key}) 不可用，该语言将无法识别"
            )

    def _load_lang(self, lang_key: str) -> bool:
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            logger.warning("未安装 sherpa-onnx，请执行: pip install sherpa-onnx")
            return False

        model_id = self._model_id_for_lang_key(lang_key)
        info = local_asr_model_info(model_id)
        if info is None:
            logger.warning(f"未知 sherpa-onnx ASR 模型 id: {model_id}")
            return False

        cache_dir = sherpa_cache_dir(model_id)
        if not self._ensure_files(info, cache_dir):
            return False

        provider = self._resolve_provider()

        # 加载失败且像损坏模型时, 清缓存重下一次再试
        for attempt in range(2):
            ok, exc = self._init_recognizer(lang_key, info, cache_dir, provider)
            if ok:
                return True
            corrupt = exc is not None and _is_corrupt_model_error(exc)
            if attempt == 0 and corrupt and self._auto_download:
                logger.warning(
                    f"sherpa-onnx 模型疑似损坏 ({lang_key}): {exc}；将重新下载"
                )
                _purge_model_files(info, cache_dir)
                if not self._download_model(info, cache_dir):
                    return False
                continue
            logger.warning(
                f"sherpa-onnx Recognizer 初始化失败 ({lang_key}): {exc}"
            )
            return False
        return False

    def _init_recognizer(
        self,
        lang_key: str,
        info: dict[str, Any],
        cache_dir: Path,
        provider: str,
    ) -> tuple[bool, Exception | None]:
        """尝试创建 recognizer。返回 (ok, error)。"""
        try:
            import sherpa_onnx
        except ImportError as exc:
            return False, exc

        # ---- Offline 模型 (SenseVoice) ----
        if info.get("kind") == "sense_voice":
            try:
                files = info["files"]
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(cache_dir / files["model"]),
                    tokens=str(cache_dir / files["tokens"]),
                    num_threads=1,
                    use_itn=True,           # 逆文本正则化 (数字/日期)
                    language="auto",         # 自动语言识别
                    provider=provider,
                )
                logger.info(
                    f"sherpa-onnx ASR (offline) provider={provider} "
                    f"kind=sense_voice ({lang_key})"
                )
            except Exception as exc:
                return False, exc
            with self._lock:
                self._recognizers[lang_key] = recognizer
            return True, None

        # ---- 流式模型 (OnlineRecognizer Transducer) ----
        hotwords_file = self._ensure_hotwords_file()
        use_hotwords = hotwords_file is not None
        decoding_method = "modified_beam_search" if use_hotwords else "greedy_search"

        try:
            files = info["files"]
            kwargs: dict[str, Any] = {
                "tokens": str(cache_dir / files["tokens"]),
                "encoder": str(cache_dir / files["encoder"]),
                "decoder": str(cache_dir / files["decoder"]),
                "joiner": str(cache_dir / files["joiner"]),
                "num_threads": 1,
                "sample_rate": 16000,
                "feature_dim": 80,
                "decoding_method": decoding_method,
                "enable_endpoint_detection": False,
                "provider": provider,
            }
            if use_hotwords:
                kwargs["hotwords_file"] = str(hotwords_file)
                kwargs["hotwords_score"] = self._hotwords_score
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
            logger.info(
                f"sherpa-onnx ASR provider={provider} decoding={decoding_method} "
                f"hotwords={len(self._hotwords)} ({lang_key})"
            )
        except Exception as exc:
            return False, exc

        with self._lock:
            self._recognizers[lang_key] = recognizer
        return True, None

    def _ensure_hotwords_file(self) -> Path | None:
        """把热词列表写入临时文件, 供 OnlineRecognizer 加载.

        sherpa-onnx 要求 hotwords_file 每行一个热词.
        无热词时返回 None, 调用方改用 greedy_search.
        """
        if not self._hotwords:
            return None
        if self._hotwords_file is not None and self._hotwords_file.exists():
            return self._hotwords_file
        import tempfile

        fd, name = tempfile.mkstemp(suffix=".txt", prefix="sherpa_hotwords_")
        try:
            with __import__("os").fdopen(fd, "w", encoding="utf-8") as f:
                for w in self._hotwords:
                    f.write(w + "\n")
        except Exception as exc:
            logger.warning(f"写入热词文件失败: {exc}")
            return None
        self._hotwords_file = Path(name)
        return self._hotwords_file

    def _resolve_provider(self) -> str:
        """根据 preference 返回 sherpa-onnx provider ('cpu' 或 'cuda')."""
        pref = self._device_preference
        if pref == "cpu":
            return "cpu"
        # auto 或 cuda
        try:
            import onnxruntime as ort

            providers = ort.get_available_providers()
            if any("CUDA" in p for p in providers):
                return "cuda"
        except Exception:
            pass
        if pref == "cuda":
            logger.warning("用户选择了 CUDA 但 onnxruntime 无 CUDA provider，回退 CPU")
        return "cpu"

    def _ensure_files(
        self, info: dict[str, Any], cache_dir: Path
    ) -> bool:
        if self._files_ready(info, cache_dir):
            return True
        if not self._auto_download:
            logger.warning(f"本地 ASR 模型缺失且禁用下载: {cache_dir}")
            return False
        # 存在不完整文件时先清掉再下, 避免 "size>0 就跳过" 留下坏文件
        if cache_dir.is_dir() and any(cache_dir.iterdir()):
            logger.info(f"本地 ASR 模型不完整，将重新下载: {cache_dir}")
            _purge_model_files(info, cache_dir)
        return self._download_model(info, cache_dir)

    def _files_ready(self, info: dict[str, Any], cache_dir: Path) -> bool:
        if not cache_dir.is_dir():
            return False
        mins = _MIN_FILE_BYTES.get(cache_dir.name, {})
        for fname in info["files"].values():
            path = cache_dir / fname
            min_bytes = mins.get(fname, 1)
            if not _file_ok(path, min_bytes=min_bytes):
                return False
        return True

    def _download_model(
        self, info: dict[str, Any], cache_dir: Path
    ) -> bool:
        from src.utils.proxy_env import prepare_model_download_env, without_proxy

        prepare_model_download_env()
        cache_dir.mkdir(parents=True, exist_ok=True)
        repo = info["repo"]
        files = info["files"]
        mins = _MIN_FILE_BYTES.get(cache_dir.name, {})

        with without_proxy():
            for role, fname in files.items():
                target = cache_dir / fname
                min_bytes = mins.get(fname, 1)
                if _file_ok(target, min_bytes=min_bytes):
                    continue
                if target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        pass
                last_exc: Exception | None = None
                for url in _hf_urls(repo, fname):
                    try:
                        logger.info(f"正在下载本地 ASR: {url}")
                        self._download_url(url, target, min_bytes=min_bytes)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        logger.warning(f"本地 ASR 镜像失败（{url}）: {exc}")
                        for junk in (target, target.with_suffix(target.suffix + ".part")):
                            try:
                                if junk.exists():
                                    junk.unlink()
                            except OSError:
                                pass
                if last_exc is not None:
                    logger.warning(f"本地 ASR 文件下载失败: {role}={fname}: {last_exc}")
                    return False
        if not self._files_ready(info, cache_dir):
            logger.warning(f"本地 ASR 下载后校验未通过: {cache_dir}")
            return False
        logger.info(f"本地 ASR 模型已保存: {cache_dir}")
        return True

    @staticmethod
    def _download_url(url: str, target: Path, *, min_bytes: int = 1) -> None:
        """流式下载并校验 Content-Length / 最小体积 / ONNX 头."""
        tmp = target.with_suffix(target.suffix + ".part")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        req = Request(url, headers={"User-Agent": "translator-intime/1.0"})
        with urlopen(req, timeout=120) as resp:
            expected = resp.headers.get("Content-Length")
            expected_n = int(expected) if expected and expected.isdigit() else None
            written = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
        if expected_n is not None and written != expected_n:
            tmp.unlink(missing_ok=True)
            raise URLError(
                f"下载长度不匹配: got={written} expected={expected_n}"
            )
        if not _file_ok(tmp, min_bytes=min_bytes):
            size = tmp.stat().st_size if tmp.exists() else 0
            tmp.unlink(missing_ok=True)
            raise URLError(
                f"下载文件校验失败: size={size} min={min_bytes} path={tmp.name}"
            )
        tmp.replace(target)


def _is_corrupt_model_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    keys = (
        "protobuf parsing failed",
        "invalid_protobuf",
        "failed to load",
        "load model from",
        "onnxruntime",
        "invalid model",
        "corrupt",
    )
    return any(k in msg for k in keys)
