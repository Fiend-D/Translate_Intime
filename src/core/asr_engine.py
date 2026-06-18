"""
语音识别模块 (ASR) - 支持 FunASR / faster-whisper
将用户麦克风输入的中文语音实时转写为文本
"""
import os
import sys
import tempfile
import site
from pathlib import Path
import numpy as np
from typing import Any, Optional
from src.utils.config import ASRConfig
from src.utils.logger import logger


class ASREngine:
    """语音识别引擎"""

    def __init__(self, config: ASRConfig, language: Optional[str] = None):
        self.config = config
        self.language = language or config.source_language
        self._model: Optional[Any] = None
        self._backend = ""
        self._audio_buffer: list[np.ndarray] = []
        self._buffer_samples = 0
        self._min_samples = int(config.sample_rate * 0.25)
        self._chunk_samples = int(config.sample_rate * 3.0)
        self._max_samples = config.sample_rate * 10
        self._last_text: str = ""

    def load_model(self, model_dir: Optional[str] = None) -> bool:
        """加载配置指定的 ASR 模型。"""
        for backend in self._backend_candidates():
            if backend == "funasr" and self._load_funasr_model():
                self._backend = "funasr"
                return True
            if backend == "whisper" and self._load_whisper_model(model_dir):
                self._backend = "whisper"
                return True
            if backend == "qwen3" and self._load_qwen3_model():
                self._backend = "qwen3"
                return True

        self._model = None
        logger.error("所有本地 ASR 模型均加载失败")
        return False

    def share_model_from(self, other: "ASREngine") -> bool:
        """复用另一个 ASR 引擎已加载的模型，避免同一模型加载两次。"""
        if other._model is None or not other._backend:
            return False
        self._model = other._model
        self._backend = other._backend
        logger.info(f"ASR 模型已复用（backend={self._backend}, language={self.language}）")
        return True

    def _backend_candidates(self) -> list[str]:
        requested = (self.config.backend or "auto").lower()
        priority = [
            item.lower()
            for item in (self.config.local_model_priority or ["qwen3", "funasr", "whisper"])
            if item.lower() in ("funasr", "whisper", "qwen3")
        ]
        if not priority:
            priority = ["qwen3", "funasr", "whisper"]

        if requested == "auto":
            return list(dict.fromkeys(priority))
        if requested in ("funasr", "whisper", "qwen3"):
            return list(dict.fromkeys([requested, *priority]))
        logger.warning(f"未知 ASR 后端 '{requested}'，按本地优先级尝试")
        return list(dict.fromkeys(priority))

    def _load_funasr_model(self) -> bool:
        """加载 Fun-ASR-Nano。"""
        self._prepare_funasr_nano_paths()
        try:
            from funasr import AutoModel
            self._register_funasr_nano()
        except ModuleNotFoundError as e:
            if e.name != "funasr":
                logger.error(f"FunASR 依赖缺失: {e.name}")
                if e.name in ("torch", "torchaudio"):
                    logger.error(
                        "Fun-ASR-Nano 需要 PyTorch/torchaudio。CPU 版安装: "
                        "python -m pip install -r requirements-funasr-cpu.txt"
                    )
                logger.debug(f"Python解释器: {sys.executable}")
                self._model = None
                return False
            logger.error(
                "FunASR 未安装，无法加载 Fun-ASR-Nano。请运行: "
                "python -m pip install -r requirements.txt"
            )
            logger.debug(f"Python解释器: {sys.executable}")
            logger.debug(f"FunASR 导入失败: {e}")
            self._model = None
            return False
        except Exception as e:
            logger.error(f"FunASR 导入失败: {e}")
            logger.debug(f"Python解释器: {sys.executable}")
            self._model = None
            return False

        device = self.config.device
        if device == "auto":
            device = "cuda:0" if self._cuda_available() else "cpu"
        elif device == "cuda":
            device = "cuda:0"

        preferred_hub = os.getenv("FUNASR_HUB", self.config.funasr_hub or "ms")
        model_name = self.config.funasr_model or "FunAudioLLM/Fun-ASR-Nano-2512"

        hubs = [preferred_hub]
        hubs.append("hf" if preferred_hub == "ms" else "ms")

        for hub in list(dict.fromkeys(hubs)):
            try:
                vad_model = "funasr/fsmn-vad" if hub == "hf" else "fsmn-vad"
                logger.info(f"加载 FunASR 模型: {model_name}, device={device}, hub={hub}")
                self._model = AutoModel(
                    model=model_name,
                    trust_remote_code=True,
                    vad_model=vad_model,
                    vad_kwargs={"max_single_segment_time": 30000},
                    device=device,
                    hub=hub,
                    disable_update=True,
                )
                logger.info("Fun-ASR-Nano 模型加载成功 ✓")
                return True
            except Exception as e:
                logger.warning(f"Fun-ASR-Nano 模型加载失败 (hub={hub}): {e}")
                self._model = None

        logger.error(
            "Fun-ASR-Nano 加载失败。若仍提示 No module named 'model'，"
            "请安装 FunASR 最新源码版: "
            "python -m pip install -U git+https://github.com/modelscope/FunASR.git"
        )
        return False

    @staticmethod
    def _prepare_funasr_nano_paths() -> None:
        """
        FunASR 1.3.x 的 Fun-ASR-Nano 内部有裸导入（如 ctc、cn_tn）。
        运行在 pip 包外部时需要把对应目录加入 sys.path 才能完成模型注册。
        """
        candidates: list[Path] = []
        for root in [*site.getsitepackages(), *sys.path]:
            if not root:
                continue
            base = Path(root) / "funasr" / "models" / "fun_asr_nano"
            if base.exists():
                candidates.append(base)

        for base in candidates:
            for path in (base, base / "tools"):
                path_str = str(path)
                if path.exists() and path_str not in sys.path:
                    sys.path.insert(0, path_str)

    @staticmethod
    def _register_funasr_nano() -> None:
        """手动注册 FunASRNano，绕过部分 funasr 版本自动注册失败问题。"""
        try:
            from funasr.models.fun_asr_nano.model import FunASRNano
            from funasr.register import tables
            tables.model_classes["FunASRNano"] = FunASRNano
        except Exception as e:
            logger.debug(f"FunASRNano 手动注册跳过: {e}")

    def _load_whisper_model(self, model_dir: Optional[str] = None) -> bool:
        """
        加载 Whisper 模型，返回是否成功。
        仅检测本地模型，不自动下载。
        """
        from faster_whisper import WhisperModel

        device = self.config.device
        if device == "auto":
            device = "cuda" if self._cuda_available() else "cpu"

        compute_type = self.config.compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"

        model_name = f"faster-whisper-{self.config.model_size}"
        logger.info(f"加载 Whisper 模型: {model_name}, device={device}")

        # 候选本地目录
        local_dirs = []
        if model_dir:
            local_dirs.append(model_dir)
        # 项目目录下的 models/ (匹配 Systran/systran 等大小写)
        proj_models = Path(__file__).parent.parent.parent / "models"
        for org in proj_models.iterdir():
            if org.is_dir() and org.name.lower() in ("systran",):
                for candidate in org.glob(f"*{self.config.model_size}*"):
                    if candidate.is_dir():
                        local_dirs.append(str(candidate))
        # ModelScope 缓存
        ms_cache = Path.home() / ".cache" / "modelscope" / "hub"
        for org in ms_cache.iterdir() if ms_cache.exists() else []:
            if org.is_dir() and org.name.lower() == "systran":
                for candidate in org.glob(f"*{self.config.model_size}*"):
                    if candidate.is_dir():
                        local_dirs.append(str(candidate))

        for local_dir in local_dirs:
            for ct in (compute_type, "int8"):  # 尝试用户设置 → int8 兜底
                try:
                    logger.info(f"  尝试本地: {local_dir} (compute={ct})")
                    self._model = WhisperModel(
                        local_dir, device=device, compute_type=ct,
                        local_files_only=True,
                    )
                    logger.info("Whisper 模型加载成功 ✓")
                    return True
                except Exception as e:
                    err_msg = str(e)
                    if "float16" in err_msg and ct != "int8":
                        continue  # 换 int8 重试
                    logger.warning(f"  本地加载失败 ({ct}): {e}")
                    break  # 不是 compute_type 问题，跳过这个目录

        logger.error(
            "Whisper 本地模型未找到，请手动下载模型:\n"
            "  1. 安装 modelscope: pip install modelscope\n"
            "  2. 下载模型: python -c \"from modelscope import snapshot_download; "
            "snapshot_download('systran/faster-whisper-small', cache_dir='./models')\"\n"
            "  3. 重新启动应用"
        )
        self._model = None
        return False

    def _load_qwen3_model(self) -> bool:
        """
        加载 Qwen3-ASR-0.6B 模型，返回是否成功。
        使用官方 qwen-asr 包加载本地模型。
        """
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ModuleNotFoundError:
            logger.error(
                "qwen-asr 未安装，无法加载 Qwen3-ASR。请运行: "
                "pip install qwen-asr"
            )
            self._model = None
            return False
        except Exception as e:
            logger.error(f"qwen-asr 导入失败: {e}")
            self._model = None
            return False

        device = self.config.device
        if device == "auto":
            device = "cuda:0" if self._cuda_available() else "cpu"
        elif device == "cuda":
            device = "cuda:0"

        # 候选本地目录
        local_dirs = []
        proj_models = Path(__file__).parent.parent.parent / "models"
        for org in proj_models.iterdir():
            if org.is_dir() and org.name.lower() in ("qwen",):
                for candidate in org.glob("*Qwen3-ASR*"):
                    if candidate.is_dir():
                        local_dirs.append(str(candidate))
        # ModelScope / HuggingFace 缓存
        for cache_root in (Path.home() / ".cache" / "modelscope" / "hub",
                           Path.home() / ".cache" / "huggingface" / "hub"):
            if not cache_root.exists():
                continue
            for org in cache_root.iterdir():
                if org.is_dir() and org.name.lower() in ("qwen", "models--qwen"):
                    for candidate in org.rglob("*Qwen3-ASR*"):
                        if candidate.is_dir() and (candidate / "config.json").exists():
                            local_dirs.append(str(candidate))

        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        for local_dir in local_dirs:
            try:
                logger.info(f"加载 Qwen3-ASR 模型: {local_dir}, device={device}")
                self._model = Qwen3ASRModel.from_pretrained(
                    local_dir,
                    dtype=dtype,
                    device_map=device,
                )
                logger.info("Qwen3-ASR 模型加载成功 ✓")
                return True
            except Exception as e:
                logger.warning(f"Qwen3-ASR 加载失败: {e}")
                self._model = None

        logger.error(
            "Qwen3-ASR 本地模型未找到，请手动下载模型:\n"
            "  1. 安装 modelscope: pip install modelscope\n"
            "  2. 下载模型: python -c \"from modelscope import snapshot_download; "
            "snapshot_download('Qwen/Qwen3-ASR-0.6B', cache_dir='./models')\"\n"
            "  3. 重新启动应用"
        )
        self._model = None
        return False

    def feed_audio(self, audio_chunk: np.ndarray) -> None:
        """喂入音频数据"""
        self._audio_buffer.append(audio_chunk.copy())
        self._buffer_samples += len(audio_chunk)
        # 如果缓冲区过大，丢弃旧数据
        while self._buffer_samples > self._max_samples and len(self._audio_buffer) > 1:
            dropped = self._audio_buffer.pop(0)
            self._buffer_samples -= len(dropped)

    def is_ready(self) -> bool:
        """检查是否有足够音频进行识别"""
        return self._buffer_samples >= self._min_samples

    def should_force_flush(self, max_seconds: float = 6.0) -> bool:
        """长时间连续说话时兜底切分，避免缓冲无限增长。"""
        return self._buffer_samples >= int(self.config.sample_rate * max_seconds)

    @staticmethod
    def _preprocess(audio: np.ndarray) -> np.ndarray:
        """音频预处理：归一化 + 自适应降噪"""
        audio = audio.astype(np.float32)
        audio = audio - np.mean(audio)
        peak = np.max(np.abs(audio))
        if peak > 0.001:
            audio = audio / peak * 0.9
        # 自适应噪声门
        mask = audio != 0
        noise_floor = np.mean(np.abs(audio[mask])) * 2 if mask.any() else 0.005
        audio[np.abs(audio) < max(noise_floor, 0.003)] = 0
        return audio

    @staticmethod
    def _funasr_language(language: str) -> str:
        lang_map = {
            "zh": "中文",
            "en": "英文",
            "ja": "日文",
            "auto": "auto",
        }
        return lang_map.get(language, language)

    def _funasr_hotwords(self) -> list[str]:
        """按识别语言过滤热词，避免中文热词污染英文识别。"""
        words = [
            word.strip()
            for word in (self.config.gaming_vocabulary or "").split()
            if word.strip()
        ]
        lang = (self.language or "").lower()
        if lang == "en":
            words = [word for word in words if word.isascii()]
        elif lang == "zh":
            words = [word for word in words if not word.isascii()]
        elif lang == "auto":
            words = []
        return words[:80]

    def _transcribe_audio(self, audio: np.ndarray) -> str:
        """核心识别：根据 ASR 后端分发。"""
        if self._backend == "funasr":
            return self._transcribe_funasr(audio)
        if self._backend == "qwen3":
            return self._transcribe_qwen3(audio)
        return self._transcribe_whisper(audio)

    def _transcribe_whisper(self, audio: np.ndarray) -> str:
        """Whisper 识别：游戏词汇提示 + 优化参数。"""
        prompt = self.config.gaming_vocabulary if self.config.gaming_vocabulary else None
        segments, _ = self._model.transcribe(
            audio,
            beam_size=self.config.beam_size,
            language=None if self.language == "auto" else self.language,
            vad_filter=self.config.vad_filter,
            condition_on_previous_text=False,
            initial_prompt=prompt,
            temperature=0.0,
            no_speech_threshold=0.4,
            compression_ratio_threshold=1.8,
            log_prob_threshold=-1.5,
        )
        return " ".join(seg.text for seg in segments).strip()

    def _transcribe_funasr(self, audio: np.ndarray) -> str:
        """FunASR 识别。Fun-ASR-Nano 接收 wav 文件路径最稳定。"""
        import soundfile as sf

        hotwords = self._funasr_hotwords()

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            sf.write(tmp_path, audio.astype(np.float32), self.config.sample_rate)
            result = self._model.generate(
                input=[tmp_path],
                cache={},
                batch_size=1,
                disable_pbar=True,
                hotwords=hotwords,
                language=self._funasr_language(self.language),
                itn=True,
            )
            if isinstance(result, list) and result:
                return str(result[0].get("text", "")).strip()
            if isinstance(result, dict):
                return str(result.get("text", "")).strip()
            return ""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _qwen3_language(language: str) -> Optional[str]:
        """将语言代码映射为 Qwen3-ASR 支持的语言名称。"""
        lang_map = {
            "zh": "Chinese",
            "en": "English",
            "ja": "Japanese",
            "ko": "Korean",
            "ar": "Arabic",
            "de": "German",
            "fr": "French",
            "es": "Spanish",
            "pt": "Portuguese",
            "id": "Indonesian",
            "it": "Italian",
            "ru": "Russian",
            "th": "Thai",
            "vi": "Vietnamese",
            "tr": "Turkish",
            "hi": "Hindi",
            "ms": "Malay",
            "nl": "Dutch",
            "sv": "Swedish",
            "da": "Danish",
            "fi": "Finnish",
            "pl": "Polish",
            "cs": "Czech",
            "fil": "Filipino",
            "fa": "Persian",
            "el": "Greek",
            "ro": "Romanian",
            "hu": "Hungarian",
            "mk": "Macedonian",
            "yue": "Cantonese",
        }
        return lang_map.get((language or "").lower().strip())

    def _transcribe_qwen3(self, audio: np.ndarray) -> str:
        """Qwen3-ASR 识别。直接传入 numpy 数组，避免磁盘 I/O。"""
        try:
            lang = None if self.language == "auto" else self._qwen3_language(self.language)
            results = self._model.transcribe(
                audio=(audio.astype(np.float32), self.config.sample_rate),
                language=lang,
            )
            if results and hasattr(results[0], "text"):
                return str(results[0].text).strip()
            return ""
        except Exception as e:
            logger.error(f"Qwen3-ASR 识别失败: {e}")
            return ""

    def transcribe(self) -> str:
        """执行语音识别，清空缓冲区并返回文本"""
        if self._model is None:
            logger.warning("模型未加载，无法识别")
            return ""
        if not self._audio_buffer:
            return ""

        audio = np.concatenate(self._audio_buffer)
        self._audio_buffer.clear()
        self._buffer_samples = 0
        audio = self._preprocess(audio)
        if not np.any(audio) or np.sqrt(np.mean(audio ** 2)) < self.config.noise_gate_threshold:
            return ""

        try:
            text = self._transcribe_audio(audio)
            if text:
                self._last_text = ""
                logger.debug(f"ASR: {text}")
            return text
        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            return ""

    def transcribe_non_blocking(self) -> str:
        """
        非阻塞识别：只有就绪时才识别，否则返回空。
        长句时会自动分块（取出最旧的 ~2秒），不等待整句结束。
        """
        if not self.is_ready():
            return ""

        # 如果缓冲超过分块阈值，只取最旧的一段做流式输出
        if self._buffer_samples >= self._chunk_samples:
            return self._transcribe_streaming_chunk()
        return self.transcribe()

    def _transcribe_streaming_chunk(self) -> str:
        """流式分块识别 + 去重"""
        chunk_parts = []
        while self._audio_buffer and sum(len(p) for p in chunk_parts) < self._chunk_samples:
            chunk_parts.append(self._audio_buffer.pop(0))
        self._buffer_samples -= sum(len(p) for p in chunk_parts)

        if not chunk_parts:
            return ""

        audio = self._preprocess(np.concatenate(chunk_parts))

        if self._model is None:
            return ""

        try:
            text = self._transcribe_audio(audio)
            if text and text != self._last_text and text not in self._last_text:
                self._last_text = text
                logger.debug(f"ASR(stream): {text}")
                return text
            elif text and text in self._last_text:
                logger.debug(f"ASR(stream): 重复跳过 '{text}'")
            return ""
        except Exception as e:
            logger.error(f"流式识别失败: {e}")
            return ""

    @staticmethod
    def _cuda_available() -> bool:
        """检测是否有可用的 NVIDIA GPU"""
        try:
            import torch
            return torch.cuda.is_available() and torch.cuda.device_count() > 0
        except ImportError:
            return False
