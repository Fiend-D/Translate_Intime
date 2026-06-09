# Translator InTime - 实时游戏语音双向翻译

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/GUI-PyQt6-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

跨平台桌面软件，实现游戏语音的**实时双向翻译**。

- 🎤 **出站翻译**: 麦克风中文 → AI翻译 → 外语语音 → 输出到游戏
- 🎮 **入站翻译**: 游戏外语语音 → AI识别 → 中文字幕显示 + 可选中文播报

![架构图](docs/architecture.png)

## 🚀 核心特性

| 功能 | 技术方案 |
|------|----------|
| 语音识别(ASR) | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - 本地高速识别 |
| 机器翻译 | OpenAI / DeepL / Google Translate 多后端支持 |
| 语音合成(TTS) | [edge-tts](https://github.com/rany2/edge-tts) - 免费高质量 |
| 音频路由 | VB-Cable(Windows) / PulseAudio null-sink(Linux) |
| GUI框架 | PyQt6 暗色主题 |

## 🏗 项目结构

```
translator_intime/
├── src/
│   ├── main.py              # 应用入口
│   ├── core/                # 核心引擎
│   │   ├── asr_engine.py    # 语音识别 (Whisper)
│   │   ├── translator.py    # 机器翻译 (多后端)
│   │   ├── tts_engine.py    # 语音合成 (edge-tts)
│   │   └── pipeline.py      # 双向翻译管道调度
│   ├── audio/               # 音频处理
│   │   ├── virtual_device.py # 虚拟音频设备管理
│   │   └── stream.py        # 音频流I/O
│   ├── gui/                 # PyQt6 界面
│   │   ├── main_window.py   # 主窗口
│   │   ├── settings_dialog.py # 设置对话框
│   │   └── styles.py        # 暗色主题样式
│   └── utils/
│       ├── config.py        # YAML配置管理
│       └── logger.py        # 日志系统
├── config/
│   └── default_config.yaml  # 默认配置
├── scripts/
│   ├── setup_linux.sh       # Linux安装脚本
│   └── setup_windows.ps1    # Windows安装脚本
├── requirements.txt
└── run.py                   # 便捷启动
```

## 📦 快速开始

### 环境要求

- **Python 3.10+**
- **Windows 10/11** 或 **Ubuntu 22.04+**

### 1. 安装

```bash
# Linux
chmod +x scripts/setup_linux.sh
./scripts/setup_linux.sh

# Windows (以管理员身份运行 PowerShell)
.\scripts\setup_windows.ps1
```

### 2. 配置虚拟音频设备

**Windows**:
- 系统/游戏声音捕获优先使用 WASAPI loopback，设置中选择 `[Loopback]` 开头的扬声器/耳机即可。
- 如需把 TTS 输出送入游戏麦克风，再安装 [VB-Cable](https://vb-audio.com/Cable/)。
- `CABLE Input` → TTS输出（合成语音 → 游戏麦克风）
- `CABLE Output` → 可选的虚拟线缆捕获输入

**Linux**: 脚本自动创建 PulseAudio null-sink
- `translator_virtual_sink` → TTS输出
- `translator_virtual_sink.monitor` → 游戏声音捕获

### 3. 设置 API Key

```bash
# 方式1: 环境变量（推荐）
export OPENAI_API_KEY="sk-..."

# 方式2: 在应用设置界面中输入
```

### 4. 启动

```bash
source venv/bin/activate  # Linux
# 或 .\venv\Scripts\Activate.ps1  # Windows

python run.py
```

### 5. 配置游戏音频

在游戏设置中，将**麦克风输入**改为虚拟音频设备：
- Windows: `CABLE Input (VB-Audio Virtual Cable)`
- Linux: `translator_virtual_sink`

## ⚙️ 配置说明

所有配置可通过 GUI 设置界面修改，或直接编辑 `config/default_config.yaml`：

```yaml
translation:
  backend: openai        # 翻译后端: openai / deepl / google
  source_lang: zh         # 你的语言
  target_lang: en         # 目标外语

tts:
  voice: zh-CN-XiaoxiaoNeural     # 中文语音
  target_voice: en-US-JennyNeural  # 目标外语语音
```

### 支持的游戏翻译方向

| 源语言 | 目标语言 | 游戏场景 |
|--------|----------|----------|
| 中文 | 英文 | 国际服游戏 |
| 中文 | 日文 | 日服游戏 |
| 英文 | 中文 | 外服游戏 |
| 任意 | 任意 | 自定义 |

## 🔄 工作流程

```mermaid
graph LR
    subgraph 出站[出站: 你说中文 → 游戏听到外语]
        MIC[🎤 麦克风] --> ASR1[ASR 中文识别]
        ASR1 --> TRANS1[翻译 中→外]
        TRANS1 --> TTS1[TTS 外语合成]
        TTS1 --> GAME[🎮 游戏输入]
    end

    subgraph 入站[入站: 游戏外语 → 你看字幕]
        GAMEOUT[🎮 游戏输出] --> ASR2[ASR 外语识别]
        ASR2 --> TRANS2[翻译 外→中]
        TRANS2 --> SUB[📝 中文字幕]
        TRANS2 --> TTS2[🔊 中文播报(可选)]
    end
```

## 🛠 开发

```bash
# 安装开发依赖
pip install -r requirements.txt

# 运行
python run.py

# 代码风格
# 遵循 PEP 8，使用 type hints
```

## 📄 License

MIT License

## ⚠️ 注意事项

1. **API费用**: OpenAI/DeepL 翻译会产生 API 调用费用
2. **延迟**: 翻译延迟取决于网络和 API 响应速度（通常 1-3 秒）
3. **虚拟音频**: 需要正确配置虚拟音频设备才能实现游戏内双向通信
4. **权限**: Windows 上虚拟音频驱动通常需要管理员权限安装
