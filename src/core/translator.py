"""
机器翻译模块 - 多后端智能选择，适配国内网络环境

后端优先级 (auto 模式):
  付费: OpenAI > DeepL
  免费: baidu(国内首选) > microsoft(国内可用) > google(需代理) > local(离线)

特性:
  - 超时自动切换下一个后端
  - 国内网络优化: 百度/微软优先，Google 兜底
  - 离线支持: Ollama 本地 LLM
  - 代理支持
"""
import asyncio
import hashlib
import os
import random
import time
from typing import Optional

import aiohttp

from src.utils.config import TranslationConfig
from src.utils.logger import logger


class TranslationEngine:
    """翻译引擎 - 多后端智能选择 + 超时降级"""

    def __init__(self, config: TranslationConfig):
        self.config = config
        self._client = None          # OpenAI async client
        self._session: Optional[aiohttp.ClientSession] = None
        self._active_backend: str = ""      # 当前后端
        self._fallback_backends: list[str] = []  # 降级列表
        self._failed_backends: set[str] = set()  # 本轮已失败的后端
        self._init_client()

    @staticmethod
    def _resolve_key(config_key: str, env_var: str) -> Optional[str]:
        """优先用配置值，否则从环境变量读取"""
        if config_key:
            return config_key
        return os.getenv(env_var)

    # ==================== 初始化 & 后端选择 ====================

    def _init_client(self) -> None:
        """初始化翻译客户端，确定后端优先级列表"""
        backend = self.config.backend.lower()
        use_cloud_model = self.config.use_cloud_model
        if backend == "volc" and not use_cloud_model:
            logger.info("云端模型已关闭，忽略 volc 后端，改用 auto 本地/文本翻译策略")
            backend = "auto"
        openai_key = self._resolve_key(self.config.openai_api_key, "OPENAI_API_KEY")
        deepl_key = self._resolve_key(self.config.deepl_api_key, "DEEPL_API_KEY")
        baidu_ready = bool(
            self.config.baidu_app_id and self.config.baidu_secret_key
        )

        # --- 构建后端列表 ---
        paid = []
        if openai_key:
            paid.append("openai")
        if deepl_key:
            paid.append("deepl")

        free = []
        for b in self.config.free_backend_priority:
            b = b.lower().strip()
            if b == "baidu" and baidu_ready:
                free.append("baidu")
            elif b == "microsoft":
                free.append("microsoft")
            elif b == "google":
                free.append("google")
            elif b == "local":
                free.append("local")
            elif b == "ollama":
                free.append("local")

        # 确保至少有一个兜底
        if not free:
            free = ["microsoft", "google"]  # 不需要任何配置的兜底

        if backend == "auto":
            # 智能模式: 付费优先，然后按 free_backend_priority
            if use_cloud_model and (
                self.config.volc_app_id
                or self.config.volc_access_token
                or os.getenv("VOLC_APP_ID")
                or os.getenv("VOLC_ACCESS_TOKEN")
                or os.getenv("VOLC_APP_KEY")
                or os.getenv("VOLC_API_KEY")
            ):
                paid.insert(0, "volc")  # 火山引擎插入付费列表最前
            all_backends = paid + free
            self._active_backend = all_backends[0]
            self._fallback_backends = all_backends[1:]
            logger.info(f"翻译策略: {self._active_backend}"
                        + (f" (降级: {', '.join(self._fallback_backends)})"
                           if self._fallback_backends else ""))
        elif backend in ("volc", "openai", "deepl", "baidu", "microsoft", "google", "local", "ollama", "hunyuan"):
            # 用户指定后端
            b = "local" if backend == "ollama" else backend
            all_backends = [b] + [f for f in free if f != b]
            self._active_backend = b
            self._fallback_backends = all_backends[1:]
            logger.info(f"翻译后端: {b}"
                        + (f" (降级: {', '.join(self._fallback_backends)})"
                           if self._fallback_backends else ""))
        else:
            logger.warning(f"未知翻译后端 '{backend}'，使用智能模式")
            all_backends = paid + free
            self._active_backend = all_backends[0]
            self._fallback_backends = all_backends[1:]

        # --- 初始化 OpenAI 客户端 (如果选中) ---
        if "openai" in [self._active_backend] + self._fallback_backends:
            if openai_key:
                from openai import AsyncOpenAI
                import httpx
                base_url = self.config.openai_base_url or None
                http_client = httpx.AsyncClient(
                    timeout=float(self.config.timeout),
                    trust_env=False,  # 不读取系统 socks 代理
                )
                self._client = AsyncOpenAI(
                    api_key=openai_key,
                    base_url=base_url,
                    http_client=http_client,
                )

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp session（忽略系统代理环境变量）"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(force_close=True)
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,  # 不读取系统 HTTP_PROXY/socks 等环境变量
            )
        return self._session

    def _get_proxy(self) -> Optional[str]:
        """获取有效的代理地址，过滤不支持的 scheme"""
        proxy = self.config.proxy.strip()
        if not proxy:
            return None
        # aiohttp 只支持 http 代理，不支持 socks
        if proxy.startswith("socks"):
            logger.warning(f"不支持 SOCKS 代理: {proxy}，已忽略。"
                           "请使用 HTTP 代理或通过环境变量设置。")
            return None
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        return proxy

    # ==================== 核心翻译入口 ====================

    async def translate(
        self,
        text: str,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> str:
        """
        翻译文本，自动重试降级后端
        """
        if not text.strip():
            return ""

        src = source_lang or self.config.source_lang
        tgt = target_lang or self.config.target_lang
        self._failed_backends.clear()

        # 尝试当前后端 + 降级列表
        backends_to_try = [self._active_backend] + [
            b for b in self._fallback_backends if b != self._active_backend
        ]

        for backend in backends_to_try:
            if backend in self._failed_backends:
                continue
            try:
                result = await asyncio.wait_for(
                    self._do_translate(backend, text, src, tgt),
                    timeout=self.config.timeout,
                )
                # 成功：如果切换了后端，记录下来
                if backend != self._active_backend:
                    logger.info(f"翻译后端已切换到: {backend}")
                    self._active_backend = backend
                return result
            except asyncio.TimeoutError:
                logger.warning(f"翻译超时 ({backend}, {self.config.timeout}s)，切换后端")
                self._failed_backends.add(backend)
            except Exception as e:
                logger.warning(f"翻译失败 ({backend}): {e}")
                self._failed_backends.add(backend)

        logger.error("所有翻译后端均失败，返回原文")
        return text

    async def _do_translate(
        self, backend: str, text: str, src: str, tgt: str
    ) -> str:
        """分发到具体后端"""
        if backend == "openai":
            return await self._translate_openai(text, src, tgt)
        elif backend == "volc":
            raise RuntimeError("火山后端是语音端到端管道，不支持文本翻译接口")
        elif backend == "deepl":
            return await self._translate_deepl(text, src, tgt)
        elif backend == "baidu":
            return await self._translate_baidu(text, src, tgt)
        elif backend == "microsoft":
            return await self._translate_microsoft(text, src, tgt)
        elif backend == "google":
            return await self._translate_google(text, src, tgt)
        elif backend == "local":
            return await self._translate_local(text, src, tgt)
        elif backend == "hunyuan":
            raise RuntimeError("腾讯混元后端由 pipeline 直接管理，不支持通过 TranslationEngine 调用")
        else:
            raise ValueError(f"不支持的后端: {backend}")

    async def translate_zh_to_en(self, text: str) -> str:
        """中文 -> 英文"""
        return await self.translate(text, "zh", "en")

    async def translate_en_to_zh(self, text: str) -> str:
        """英文 -> 中文"""
        return await self.translate(text, "en", "zh")

    # ==================== OpenAI ====================

    async def _translate_openai(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        if self._client is None:
            raise RuntimeError("OpenAI 客户端未初始化")
        system_prompt = (
            f"You are a professional game translator. "
            f"Translate from {source_lang} to {target_lang}. "
            f"Keep it natural for gaming context. Return ONLY the translation."
        )
        response = await self._client.chat.completions.create(
            model=self.config.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        translated = response.choices[0].message.content.strip()
        logger.debug(f"[OpenAI] {text[:30]}... -> {translated[:30]}...")
        return translated

    # ==================== DeepL ====================

    async def _translate_deepl(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        import deepl
        lang_map = {"zh": "ZH", "en": "EN-US", "ja": "JA", "ko": "KO"}
        src_code = lang_map.get(source_lang, source_lang.upper())
        tgt_code = lang_map.get(target_lang, target_lang.upper())
        translator = deepl.Translator(self.config.deepl_api_key)
        result = translator.translate_text(text, source_lang=src_code, target_lang=tgt_code)
        logger.debug(f"[DeepL] {text[:30]}... -> {result.text[:30]}...")
        return result.text

    # ==================== Baidu (百度翻译，国内首选) ====================

    async def _translate_baidu(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        """百度翻译 API - 免费额度 200万字符/月
        注册: https://fanyi-api.baidu.com
        """
        app_id = self.config.baidu_app_id
        secret_key = self.config.baidu_secret_key
        if not app_id or not secret_key:
            raise RuntimeError("百度翻译 app_id/secret_key 未配置")

        # 语言代码映射
        lang_map = {"zh": "zh", "en": "en", "ja": "jp", "ko": "kor",
                     "fr": "fra", "de": "de", "es": "spa", "ru": "ru"}
        src_code = lang_map.get(source_lang, source_lang)
        tgt_code = lang_map.get(target_lang, target_lang)

        salt = str(random.randint(32768, 65536))
        sign_str = app_id + text + salt + secret_key
        sign = hashlib.md5(sign_str.encode()).hexdigest()

        params = {
            "q": text,
            "from": src_code,
            "to": tgt_code,
            "appid": app_id,
            "salt": salt,
            "sign": sign,
        }
        session = await self._get_session()
        proxy = self._get_proxy()
        async with session.get(
            "https://fanyi-api.baidu.com/api/trans/vip/translate",
            params=params,
            proxy=proxy,
        ) as resp:
            data = await resp.json()
            if "error_code" in data and data["error_code"]:
                raise RuntimeError(f"百度翻译错误 {data['error_code']}: {data.get('error_msg', '')}")
            result = data["trans_result"][0]["dst"]
            logger.debug(f"[Baidu] {text[:30]}... -> {result[:30]}...")
            return result

    # ==================== Microsoft (国内可用) ====================

    async def _translate_microsoft(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        """微软翻译 - 免费，国内可直接访问"""
        # 使用微软 Translator 免费 API
        url = "https://api.cognitive.microsofttranslator.com/translate"
        params = {
            "api-version": "3.0",
            "from": source_lang,
            "to": target_lang,
        }
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self._resolve_key("", "MS_TRANSLATOR_KEY") or "",
        }
        body = [{"Text": text}]
        session = await self._get_session()
        proxy = self._get_proxy()
        if headers["Ocp-Apim-Subscription-Key"]:
            async with session.post(
                url, params=params, headers=headers, json=body, proxy=proxy
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"微软翻译 HTTP {resp.status}")
                data = await resp.json()
                result = data[0]["translations"][0]["text"]
        else:
            # 无 key 模式: 使用网页翻译接口
            async with session.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": f"{source_lang}|{target_lang}"},
                proxy=proxy,
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"MyMemory 翻译 HTTP {resp.status}")
                result = data["responseData"]["translatedText"]

        logger.debug(f"[Microsoft] {text[:30]}... -> {result[:30]}...")
        return result

    # ==================== Google ====================

    async def _translate_google(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        """Google 翻译 - 免费，国内需代理"""
        from googletrans import Translator
        translator = Translator()
        # googletrans 支持 proxy 参数
        result = await translator.translate(text, src=source_lang, dest=target_lang)
        logger.debug(f"[Google] {text[:30]}... -> {result.text[:30]}...")
        return result.text

    # ==================== Local / Ollama (离线) ====================

    async def _translate_local(
        self, text: str, source_lang: str, target_lang: str
    ) -> str:
        """本地 Ollama LLM 翻译 - 完全离线免费
        需要先安装: curl -fsSL https://ollama.com/install.sh | sh
        然后拉模型: ollama pull qwen2.5:7b
        """
        from openai import AsyncOpenAI
        import httpx
        client = AsyncOpenAI(
            base_url=self.config.ollama_base_url,
            api_key="ollama",  # Ollama 不需要真实 key
            http_client=httpx.AsyncClient(
                timeout=float(self.config.timeout),
                trust_env=False,
            ),
        )
        system_prompt = (
            f"You are a translator. Translate from {source_lang} to {target_lang}. "
            f"Return ONLY the translation, nothing else."
        )
        response = await client.chat.completions.create(
            model=self.config.ollama_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        translated = response.choices[0].message.content.strip()
        logger.debug(f"[Ollama/{self.config.ollama_model}] {text[:30]}... -> {translated[:30]}...")
        return translated

    # ==================== 同步接口 ====================

    def translate_sync(self, text: str, source_lang: str = "zh", target_lang: str = "en") -> str:
        return asyncio.run(self.translate(text, source_lang, target_lang))

    async def close(self) -> None:
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
