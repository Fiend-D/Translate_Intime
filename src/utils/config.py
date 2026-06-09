"""
配置管理模块 - 加载、保存、验证应用配置
"""
from pathlib import Path
from typing import Any, Optional, Union
from pydantic import BaseModel, Field
import yaml


class ASRConfig(BaseModel):
    """语音识别配置"""
    backend: str = "auto"  # auto / funasr / whisper
    local_model_priority: list[str] = Field(default_factory=lambda: ["funasr", "whisper"])
    model_size: str = "small"  # tiny/base/small/medium/large-v3
    funasr_model: str = "FunAudioLLM/Fun-ASR-Nano-2512"
    funasr_hub: str = "ms"  # ms / hf
    device: str = "auto"  # auto / cpu / cuda
    compute_type: str = "auto"  # auto / float16 / int8
    source_language: str = "zh"  # 你说的语言（中文ASR用）
    target_language: str = "en"  # 游戏里的外语（外语ASR用）
    vad_filter: bool = True
    noise_gate_threshold: float = 0.005  # 噪声门阈值，低于此值的音频归零
    beam_size: int = 5
    sample_rate: int = 16000    # 游戏词汇提示（可自定义，空格分隔）
    gaming_vocabulary: str = (
        # ---- 猎杀对决 Hunt: Showdown ----
        "线索 灰 红点 找线索 拿线索 放逐 驱逐 开始放逐 被放了 "
        "屠夫 蜘蛛 刺客 残喙 乌鸦 火男 沸血 钢铁 虫群 地狱犬 "
        "Boss 打Boss 蹲Boss 有人打Boss 抢Boss "
        "猎人 蹲 冲 别冲 开枪 枪声 消音 脚步 有人 没人 那边 这边 在哪 看到了 标记 "
        "撤离 撤离点 马车 船 走 快走 回来 别走 等等 别动 蹲下 趴下 过来 跟我走 "
        "大包 小包 医疗包 打药 打针 弹药 子弹 特殊弹 换弹 没子弹了 "
        "燃烧弹 炸药 炸药束 破片 铁丝网 毒气 毒气弹 闪光 闪光弹 蜂巢 诱饵 "
        "一枪 爆头 打中了 残血 一丝 死了 倒了 被烧了 烧尸 "
        "红桶 黄桶 绿桶 乌鸦 狗 鸡 马 火把 陷阱 绊雷 "
        "长枪 短枪 喷子 猎象 莫辛 勒贝尔 克拉格 双枪 弓 弩 飞刀 飞斧 "
        "灰队 复活 救人 扶我 拉人 烧人 点烧 守尸 蹲尸 "
        "禁用手枪 禁用步枪 禁用霰弹 禁用爆炸物 禁用治疗 "
        "换点 绕后 绕路 走上面 走下面 走房子 "
        # ---- MOBA通用 ----
        "中路 上路 下路 打野 辅助 射手 法师 坦克 战士 刺客 "
        "团战 开团 撤退 进攻 防守 补刀 推塔 打龙 大龙 小龙 远古龙 先锋 "
        "回城 装备 技能 大招 闪现 点燃 治疗 惩戒 传送 疾跑 屏障 净化 "
        "击杀 双杀 三杀 四杀 五杀 超神 团灭 复活 终结 一血 "
        "视野 草丛 河道 野区 高地 水晶 门牙 防御塔 兵线 "
        "集合 分散 埋伏 绕后 蹲人 抓人 反蹲 越塔 偷家 支援 "
        "我去 快走 小心 可以打 打不了 别上 等我 撤退 先撤 稳住 "
        "经济 等级 经验 金币 补刀 发育 压制 优势 劣势 翻盘 "
        "红buff 蓝buff 蓝爸爸 红爸爸 打buff 让buff 控龙 抢龙 "
        # ---- 通用英语 ----
        "Gank Push Mid Top Bot Jungle Baron Dragon "
        "Boss Clue Banish Extract Extraction Respawn Revive "
        "Hunter Shotgun Mosin Lebel Uppercut "
        "Retreat Attack Defend Go Back Help Heal Ult GG "
        "Nice Good Sorry Thanks Well played Wait Careful"
    )

class TranslationConfig(BaseModel):
    """翻译配置"""
    backend: str = "auto"  # auto(智能) / openai / deepl / baidu / microsoft / google / local
    use_cloud_model: bool = True  # 是否优先使用云端端到端模型（当前为火山 AST）
    source_lang: str = "zh"
    target_lang: str = "en"
    # auto 模式下的免费后端优先级（国内推荐: baidu > microsoft > google）
    free_backend_priority: list[str] = ["baidu", "microsoft", "google"]
    # 网络设置
    proxy: str = ""          # 代理地址，如 http://127.0.0.1:7890
    timeout: int = 5         # 单次翻译超时(秒)，超时自动切换下一个后端
    # OpenAI (付费，质量最高)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""  # 兼容 Ollama/vLLM 等本地 LLM
    # DeepL (付费)
    deepl_api_key: str = ""
    # 百度翻译 (免费额度: 200万字符/月，国内首选)
    baidu_app_id: str = ""
    baidu_secret_key: str = ""
    # 本地 Ollama (完全离线免费)
    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434/v1"
    # 火山引擎 AST 2.0 实时语音翻译。volc_app_id 字段兼容旧配置名，实际填写 API Key。
    volc_app_id: str = ""
    volc_access_token: str = ""  # 兼容旧配置；新版控制台通常留空
    volc_resource_id: str = "volc.service_type.10053"


class TTSConfig(BaseModel):
    """语音合成配置"""
    backend: str = "edge-tts"  # edge-tts / openai / azure
    voice: str = "zh-CN-XiaoxiaoNeural"  # 中文声音
    target_voice: str = "en-US-JennyNeural"  # 外语声音
    rate: str = "+0%"
    volume: str = "+0%"


class AudioConfig(BaseModel):
    """音频设备配置"""
    input_device: Optional[Union[int, str]] = None  # 麦克风设备ID或PulseAudio/PipeWire设备名
    output_device: Optional[Union[int, str]] = None  # TTS输出设备ID或PulseAudio/PipeWire设备名
    game_output_device: Optional[Union[int, str]] = None  # 游戏声音捕获设备ID或monitor源名
    chunk_size: int = 1024
    channels: int = 1
    sample_rate: int = 16000


class UIConfig(BaseModel):
    """界面配置"""
    language: str = "zh"  # 界面语言
    font_size: int = 12
    always_on_top: bool = True
    show_subtitle_overlay: bool = True
    subtitle_opacity: float = 0.85
    max_subtitle_lines: int = 20
    play_chinese_voice: bool = False  # 是否播报翻译后的中文语音


class AppConfig(BaseModel):
    """应用总配置"""
    asr: ASRConfig = Field(default_factory=ASRConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


class ConfigManager:
    """配置管理器"""

    DEFAULT_CONFIG_PATH = Path("config/default_config.yaml")

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: AppConfig = AppConfig()
        self.load()

    @property
    def config(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        """从YAML文件加载配置"""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._config = AppConfig(**data)
            except Exception as e:
                print(f"加载配置失败，使用默认配置: {e}")
                self._config = AppConfig()
        else:
            self._config = AppConfig()
            self.save()
        return self._config

    def save(self, path: Optional[Path] = None) -> None:
        """保存配置到YAML文件"""
        save_path = path or self._config_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        config_dict = self._config.model_dump()
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, allow_unicode=True, default_flow_style=False)

    def update(self, section: str, **kwargs) -> None:
        """更新配置中某个section的值"""
        if hasattr(self._config, section):
            section_obj = getattr(self._config, section)
            for key, value in kwargs.items():
                if hasattr(section_obj, key):
                    setattr(section_obj, key, value)
            self.save()

    def get(self, section: str) -> Any:
        """获取配置section"""
        return getattr(self._config, section)
