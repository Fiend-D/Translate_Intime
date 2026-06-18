"""
腾讯混元翻译引擎 - 支持 HY-MT1.5-1.8B 本地模型
"""
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from src.utils.config import TranslationConfig
from src.utils.logger import logger


class HunyuanEngine:
    """腾讯混元 HY-MT1.5-1.8B 本地翻译引擎"""

    CACHE_SIZE = 100

    def __init__(self, config: TranslationConfig):
        self.config = config
        self._model: Optional[Any] = None
        self._model_loaded = False
        self._tokenizer = None
        self._cache = OrderedDict()

    @property
    def is_available(self) -> bool:
        return self._model_loaded and self._model is not None

    def load_model(self, model_dir: Optional[str] = None) -> bool:
        """
        加载 HY-MT1.5-1.8B 本地模型，返回是否成功。
        仅检测本地模型，不自动下载。
        """
        if self._model_loaded and self._model is not None:
            return True

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ModuleNotFoundError as e:
            logger.error(
                "transformers 未安装，无法加载 HY-MT1.5-1.8B。请运行："
                "pip install transformers torch sentencepiece tiktoken"
            )
            return False
        except Exception as e:
            logger.error(f"transformers 导入失败：{e}")
            return False

        # 候选本地目录
        local_dirs = []
        if self.config.hunyuan_model_path:
            local_dirs.append(self.config.hunyuan_model_path)
        if model_dir:
            local_dirs.append(model_dir)
        
        # 项目目录下的 models/（仅当目录下存在 config.json 时才视为有效）
        proj_models = Path(__file__).parent.parent.parent / "models"
        for candidate in proj_models.glob("*HY-MT*"):
            if candidate.is_dir() and (candidate / "config.json").exists():
                local_dirs.append(str(candidate))
        
        # ModelScope 缓存
        ms_cache = Path.home() / ".cache" / "modelscope" / "hub"
        for org in ms_cache.iterdir() if ms_cache.exists() else []:
            if org.is_dir():
                for candidate in org.glob("*HY-MT*"):
                    if candidate.is_dir():
                        local_dirs.append(str(candidate))
        
        # Hugging Face 缓存（仅当目录下存在 config.json 时才视为有效）
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        for candidate in hf_cache.glob("*HY-MT*") if hf_cache.exists() else []:
            if candidate.is_dir() and (candidate / "config.json").exists():
                local_dirs.append(str(candidate))
            elif candidate.is_dir():
                # 检查 snapshots 子目录
                for snap in candidate.glob("snapshots/*"):
                    if snap.is_dir() and (snap / "config.json").exists():
                        local_dirs.append(str(snap))

        model_name = self.config.hunyuan_model or "HY-MT1.5-1.8B"

        for local_dir in local_dirs:
            try:
                logger.info(f"加载 HY-MT1.5-1.8B 模型：{local_dir}")
                self._tokenizer = AutoTokenizer.from_pretrained(
                    local_dir,
                    trust_remote_code=True,
                    local_files_only=True,
                )
                # 手动读取 chat_template.jinja（旧版 transformers 不会自动读取）
                jinja_path = os.path.join(local_dir, "chat_template.jinja")
                if os.path.isfile(jinja_path) and not getattr(self._tokenizer, "chat_template", None):
                    try:
                        with open(jinja_path, "r", encoding="utf-8") as f:
                            self._tokenizer.chat_template = f.read()
                        logger.info("已加载 chat_template.jinja")
                    except Exception as e:
                        logger.warning(f"读取 chat_template.jinja 失败: {e}")
                use_gpu = self._check_gpu_memory(min_gb=4.0)
                if not use_gpu and self._cuda_available():
                    logger.info("GPU 显存不足，使用 CPU 加载混元模型")
                self._model = AutoModelForCausalLM.from_pretrained(
                    local_dir,
                    trust_remote_code=True,
                    local_files_only=True,
                    device_map="auto" if use_gpu else None,
                )
                self._model.eval()
                # 首次预热：空跑一次生成，减少后续延迟
                if self._tokenizer.chat_template:
                    warm_ids = self._tokenizer.apply_chat_template(
                        [{"role": "user", "content": "hi"}],
                        return_tensors="pt",
                        add_generation_prompt=True,
                    )
                else:
                    warm_ids = self._tokenizer("hi", return_tensors="pt").input_ids
                device = next(self._model.parameters()).device
                warm_ids = warm_ids.to(device)
                with torch.inference_mode():
                    self._model.generate(warm_ids, max_new_tokens=1, pad_token_id=self._tokenizer.eos_token_id)
                logger.info("HY-MT1.5-1.8B 模型加载成功 ✓")
                self._model_loaded = True
                return True
            except Exception as e:
                logger.warning(f"模型加载失败 ({local_dir}): {e}")
                self._model = None
                self._model_loaded = False

        logger.error(
            "HY-MT1.5-1.8B 本地模型加载失败，请检查:\n"
            "  1. 模型是否已下载:\n"
            "    git clone https://huggingface.co/tencent/HY-MT1.5-1.8B ./models/HY-MT1.5-1.8B\n"
            "  2. 依赖是否已安装:\n"
            "    pip install transformers torch sentencepiece tiktoken\n"
            "  然后重新启动应用"
        )
        return False

    def _cuda_available(self) -> bool:
        """检查 CUDA 是否可用"""
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _check_gpu_memory(self, min_gb: float = 4.0) -> bool:
        """检查 GPU 可用显存是否 >= min_gb（GB），失败时返回 False"""
        try:
            import torch
            if not torch.cuda.is_available():
                return False
            device = torch.cuda.current_device()
            total = torch.cuda.get_device_properties(device).total_memory
            reserved = torch.cuda.memory_reserved(device)
            available = (total - reserved) / (1024 ** 3)
            if available < min_gb:
                logger.warning(f"GPU 显存不足: 可用 {available:.1f}GB < 要求 {min_gb}GB，将回退到 CPU")
                return False
            return True
        except Exception:
            return False

    def translate(self, text: str, source_lang: str = "en", target_lang: str = "zh") -> Optional[str]:
        """
        使用本地 HY-MT1.5-1.8B 模型进行翻译
        """
        if not text.strip():
            return ""

        cache_key = f"{source_lang}:{target_lang}:{text.strip()}"
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        if not self._model_loaded or self._model is None:
            if not self.load_model():
                return None

        try:
            import torch

            # 语言代码 -> 完整名称映射
            lang_name_map = {
                "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
                "fr": "French", "de": "German", "es": "Spanish", "ru": "Russian",
                "ar": "Arabic", "pt": "Portuguese", "it": "Italian", "vi": "Vietnamese",
                "th": "Thai", "id": "Indonesian", "ms": "Malay", "tr": "Turkish",
                "pl": "Polish", "nl": "Dutch", "sv": "Swedish", "da": "Danish",
                "fi": "Finnish", "cs": "Czech", "el": "Greek", "ro": "Romanian",
                "hu": "Hungarian", "hi": "Hindi", "fa": "Persian", "uk": "Ukrainian",
            }
            src_lang_name = lang_name_map.get((source_lang or "").lower().strip(), source_lang)
            tgt_lang_name = lang_name_map.get((target_lang or "").lower().strip(), target_lang)

            # 使用 chat template 构造输入（推荐方式）
            messages = [{
                "role": "user",
                "content": f"Translate the following segment into {tgt_lang_name}, without additional explanation.\n\n{text}"
            }]
            try:
                input_ids = self._tokenizer.apply_chat_template(
                    messages,
                    return_tensors="pt",
                    add_generation_prompt=True,
                )
                inputs = {"input_ids": input_ids}
            except Exception:
                # 回退：直接编码文本
                prompt = f"Translate the following segment into {tgt_lang_name}, without additional explanation.\n\n{text}"
                inputs = self._tokenizer(prompt, return_tensors="pt")

            # 移除 CausalLM 不支持的 token_type_ids
            inputs.pop("token_type_ids", None)

            # 移动到设备
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            # 生成翻译（CPU 上贪婪解码比 beam search 快数倍）
            generate_kwargs = {
                "max_new_tokens": 128,
                "num_beams": 1,
                "do_sample": False,
                "pad_token_id": self._tokenizer.eos_token_id,
            }
            # 仅当 tokenizer 支持 lang_code_to_id 时设置（如 MarianMT）
            if hasattr(self._tokenizer, "lang_code_to_id"):
                tgt_code = (target_lang or "")[:2].lower()
                generate_kwargs["forced_bos_token_id"] = self._tokenizer.lang_code_to_id.get(tgt_code, 0)

            import time
            start = time.time()
            with torch.inference_mode():
                outputs = self._model.generate(**inputs, **generate_kwargs)
            elapsed = time.time() - start
            logger.info(f"混元翻译耗时: {elapsed:.2f}s")

            # 解码结果（跳过输入部分，仅解码新生成的 token）
            input_length = inputs["input_ids"].shape[1]
            translation = self._tokenizer.decode(
                outputs[0][input_length:],
                skip_special_tokens=True,
                clean_up_tokenization_spaces=True
            )
            
            logger.debug(f"腾讯混元翻译结果：{translation}")
            result = translation
            self._cache[cache_key] = result
            if len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
            return result

        except Exception as e:
            logger.error(f"腾讯混元翻译处理失败：{e}")
            return None
