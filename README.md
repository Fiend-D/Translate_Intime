# Translator InTime

桌面实时语音翻译工具，适合游戏语音沟通、外语直播字幕、跨语言会议等场景。

产品形态参考 [SayHey](https://github.com/DoerrDev/SayHey)：双通道同传 + 悬浮字幕，当前以**火山引擎 AST 2.0** 端到端同传为主线路。

## 主要能力

* **同声传译**：采集麦克风，实时翻译并通过输出设备（建议虚拟声卡）播放，让队友在游戏或语音软件里听到译文。
* **游戏字幕**：捕获系统扬声器 / Loopback，把游戏、视频、通话里的外语译成悬浮字幕。
* **打字翻译**：直接输入文字，轻量在线翻译后用 edge-tts 朗读（独立通道，不走火山同传）。
* **音乐分享**：本地音乐播到虚拟声卡，队友当麦克风听到（CS 音乐盒平替）。

## 功能特性

### 翻译与字幕

* 麦克风同声传译与系统音频悬浮字幕（双通道可独立开关）
* 流式字幕：中间结果即时刷新，定稿后柔和确认（豆包式跟打）
* 字幕浮层可拖拽 / 缩放 / 锁定（点击穿透），支持多行历史与是否显示原文
* 多语言互译：中文 / English / 日本語 / 한국어
* 同传音色可选（复刻原音色或火山公版音色）；语速可调（-50 ~ +100）

### 热词系统

* 自定义热词表，提升专名 / 游戏术语识别
* 术语 glossary（原文=译文）注入火山同传
* 内置常见游戏热词包（`hotwords/`），可在界面中导入

### 快捷键与悬浮提示

* 可自定义全局快捷键（麦克风 / 游戏字幕 / 停止 / 浮层显隐 / 音乐播放与切歌）
* 音乐默认：`Ctrl+Alt+P` 播放暂停，`X` 停止，`PageUp/PageDown` 上一首/下一首，`B` 侧栏
* 全局快捷键不可用时自动回退为窗口内快捷键
* 触发时屏幕中央短时 toast 提示

### 设备与音频

* Windows：WASAPI Loopback；Linux：Pulse / PipeWire monitor
* 麦克风列表默认隐藏虚拟回环，降低自反馈风险
* 启动前设备防呆校验；Windows 未选输出时可优先提示 CABLE Input
* 「设备引导」说明 VB-Cable / 虚拟 sink 推荐链路
* **音乐分享**面板：选文件夹作曲库，下拉选曲；播放后右侧透明侧栏一键切歌（上一首/下一首/点击曲目）；支持音量、单曲循环、播完下一首；MP3 等格式需 `ffmpeg`

### 其它

* 深色 / 浅色主题
* 一键「检测虚拟声卡」：检查 CABLE Input / Output 是否齐全
* 主界面「高级」默认关闭：隐藏译文输出 / 游戏声音选择，使用自动推荐
* 麦克风列表默认排除虚拟回环，降低自反馈
* 启动前拦截错误设备；同传与游戏字幕共用虚拟线时自动避让
* 首次未选输出时优先 CABLE Input；游戏捕获优先系统 Loopback
* 本地 VAD（默认开）：静音/噪音段不送火山，减轻幻觉；设置里可调宽松/标准/严格
* 运行日志面板，便于排查延迟与异常
* 配置持久化（语言、设备、热词、快捷键、浮层位置等）

## 引擎说明

当前提供两条翻译路径：火山 AST 2.0 端到端同传，以及经济模式的 ASR → MT → TTS 级联：

* 火山模式支持 S2T（字幕）与 S2S（语音输出）。
* 经济模式支持 DashScope、Windows Live Captions、faster-whisper/sherpa ASR，NLLB 等 MT，
  Kokoro/Edge TTS；本地模型首次使用可能需要下载。
* 热词、术语、语速、音色在开启通道时生效；改完后需重新开通道。

备选引擎（如通义 Qwen LiveTranslate、腾讯实时语音翻译等）规划中，便于货比三家；尚未接入 UI。

火山引擎控制台：<https://console.volcengine.com/speech/new/overview?projectName=default>

打字翻译通道使用在线轻量翻译 + `edge-tts`，与火山额度无关。

### Dota 助手语音教练（可选）

若本机同时运行 [dota2-tracker](https://github.com/Miroscyer/dota2-tracker)（默认 `http://127.0.0.1:3001`）：

1. Tracker 先启动，并在设置中配置好 AI Key。
2. InTime 开启**麦克风通道**，面板勾选「Dota 助手」。
3. 默认快捷键 `Ctrl+Alt+C`：进入待命 → 说话等定稿 → 自动把**原文** POST 到 `/ai/ask`；再按一次取消待命。
4. 回复会出现在 Tracker 悬浮窗；InTime 也会 toast 摘要。

注意：待命期间麦克风同传仍会走你原来的虚拟声卡链路；若不想让队友听到提问，可暂时关掉「播放译文」或改用仅识别的用法。

## 使用前准备

### 同声传译（让队友听到译文）

1. 建议安装 [VB-Cable](https://vb-audio.com/Cable/)（Windows）或配置 Pulse/PipeWire 虚拟 sink（Linux）。
2. 本应用「译文播放」选 **CABLE Input**（或你的虚拟 sink）。
3. 游戏 / Discord 等软件的麦克风选 **CABLE Output**（或对应 monitor）。

默认音频链路：

```text
真实麦克风 -> Translator InTime -> CABLE Input -> CABLE Output -> 目标应用
```

### 游戏字幕

* 不需要 VB-Cable
* 通过系统 Loopback / monitor 捕获播放声音
* 在面板中选择具体的扬声器 / 耳机作为捕获源

### 账号

在界面填写火山 **API Key**（或兼容的 App ID + Token），保存后开启对应通道。

## 从源码运行

### 环境要求

* Python 3.12+
* Windows 10/11 或 Linux（PulseAudio / PipeWire）
* 麦克风与输出设备；同传到队友时建议虚拟声卡

### 安装依赖

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

开发依赖（可选）：

```bash
pip install -r requirements-dev.txt
```

### 启动 GUI

```bash
python run.py
# 或
python -m src.main
# 或
./run.sh          # Linux / macOS
run.bat           # Windows
```

首次运行会加载 / 创建本地配置；在界面填写火山 Key、选择设备与语言后即可使用。

### 测试

```bash
QT_QPA_PLATFORM=offscreen pytest
```

无显示器的 Linux/CI 环境必须设置 `QT_QPA_PLATFORM=offscreen`；真实音频设备和模型下载仍需
在目标机器单独验证。

## 项目结构

* `run.py` / `src/main.py`：GUI 入口
* `src/gui/`：主面板、悬浮字幕、toast、热词对话框、打字翻译、快捷键设置
* `src/core/pipeline.py`：双通道同传编排
* `src/core/volc_engine.py`：火山 AST 2.0 客户端
* `src/core/audio_capture.py` / `audio_player.py`：采集与播放
* `src/core/music_share.py`：本地音乐分享到虚拟声卡
* `src/audio/device_guard.py`：设备防呆与 VB-Cable 引导
* `src/utils/hotkeys.py`：全局 / 窗口内快捷键
* `hotwords/`：游戏热词包
* `python_protogen/`：火山 AST Protobuf 定义
* `docs/future-design.md`：后续架构与备选引擎规划
* `scripts/`：环境与音频诊断脚本

## 说明

* 本项目是桌面音频翻译与字幕工具，不注入游戏。
* 语音服务账号与 Key 需自行准备；云端同传依赖网络与额度。
* 打包安装包与自动更新：有发布需求时再做（当前以源码运行为主）。

## License

MIT（若仓库后续补充 `LICENSE` 文件，以该文件为准）
