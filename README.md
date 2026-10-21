# Translator InTime

桌面实时语音翻译工具，适合游戏语音沟通、外语直播字幕、跨语言会议等场景。

产品形态参考 [SayHey](https://github.com/DoerrDev/SayHey)：双通道同传 + 悬浮字幕，当前以**火山引擎 AST 2.0** 端到端同传为主线路。

## 主要能力

* **同声传译**：采集麦克风，实时翻译并通过输出设备（建议虚拟声卡）播放，让队友在游戏或语音软件里听到译文。
* **游戏字幕**：捕获系统扬声器 / Loopback，把游戏、视频、通话里的外语译成悬浮字幕。
* **打字翻译**：主窗口内嵌页面，输入文字后在线翻译并用 edge-tts 朗读（独立通道，不走火山同传）。
* **翻译记录**：主窗口内嵌页面，按天浏览、刷新与导出历史字幕。
* **音乐分享**：本地音乐播到虚拟声卡，队友当麦克风听到（CS 音乐盒平替）。

## 功能特性

### 翻译与字幕

* 麦克风同声传译与系统音频悬浮字幕（双通道可独立开关）
* 滚动字幕：识别 / 翻译中的内容显示在底部暗色"进行中"行，不会顶掉已定稿的译文
* 每句定稿译文按长度保证阅读时间（约 1.2 ~ 4 秒）再滚入历史；说得太快时自动缩短停留、
  积压过多直接进历史，字幕不会越拖越晚
* 字幕浮层可拖拽 / 缩放，点「调整字幕」解锁、双击锁定（锁定后点击穿透）；
  支持多行历史与是否显示原文
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
* 回声消除：译文 TTS 被麦克风或系统 Loopback 回采时自动扣除，只保留其他声音；
  游戏字幕与译文播放共用默认输出也能正常工作
* 朗读译文时可压低 / 静音其他应用，或不处理（混听）
* 启动前设备防呆校验；Windows 未选输出时可优先提示 CABLE Input
* 「设备引导」说明 VB-Cable / 虚拟 sink 推荐链路
* **音乐分享**面板：选文件夹作曲库，下拉选曲；播放后右侧透明侧栏一键切歌（上一首/下一首/点击曲目）；支持音量、单曲循环、播完下一首；MP3 等格式需 `ffmpeg`

### 其它

* 多套主题（跟随系统 / 浅色 / 深色 / 薄荷 / 紫罗兰 / 沙色 / 石墨）
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
* 经济模式各环节可分别选择：
  * ASR：DashScope 实时识别、Qwen3-ASR-Flash / 豆包极速版（云端），Windows Live Captions，
    本地 faster-whisper、sherpa-onnx（SenseVoice / 流式 zipformer）、Qwen3-ASR
  * MT：NLLB（本地）、Argos、MyMemory、OpenAI 兼容大模型（DeepSeek / Ollama / LM Studio 等）、
    本地 Hy-MT2
  * TTS：Kokoro、MeloTTS（中英混读）、Edge TTS、豆包 / CosyVoice / OpenAI 兼容云端 TTS
* 经济模式按停顿分句：句末标点或说话停顿后提交整句，长句最长等待约 7 秒，避免半句话被拆开翻译。
* 本地模型首次使用需要下载，支持断点续传、失败重试与代理；云端服务失败时自动熔断回退。
* 热词、术语、语速、音色在开启通道时生效；改完后需重新开通道。

火山引擎控制台：<https://console.volcengine.com/speech/new/overview?projectName=default>

打字翻译通道使用在线轻量翻译 + `edge-tts`，与火山额度无关。

### Dota 助手语音教练（可选）

若本机同时运行 [dota2-tracker](https://github.com/Miroscyer/dota2-tracker)（默认 `http://127.0.0.1:3001`）：

1. Tracker 先启动，并在设置中配置好 AI Key。
2. InTime 开启**麦克风通道**，面板勾选「Dota 助手」。
3. 默认快捷键 `Ctrl+Alt+K`：进入待命 → 说话等定稿 → 自动把**原文** POST 到 `/ai/ask`；再按一次取消待命。
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
uv sync --frozen
```

开发依赖（可选）：

```bash
uv sync --frozen --group dev
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
* `src/gui/`：主面板、悬浮字幕、toast、热词对话框、打字翻译 / 翻译记录页面、快捷键设置
* `src/core/pipeline.py`：双通道同传编排
* `src/core/volc_engine.py`：火山 AST 2.0 客户端
* `src/core/echo_canceller.py`：译文 TTS 回声消除
* `src/core/audio_capture.py` / `audio_player.py`：采集与播放
* `src/engines/pipeline/`：经济模式 ASR / MT / TTS 后端与分句
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

## Windows 一键打包

双击项目根目录的 `build_exe.bat`，脚本会创建或复用 `.venv`、安装缺失依赖并生成：

```text
dist/SoundFerry.exe
dist/SoundFerry.exe.sha256
```

命令行方式：

```powershell
python build.py --clean --install-deps
python build.py --clean --debug  # 保留控制台窗口，便于排查启动错误
python build.py --dry-run        # 只查看 PyInstaller 命令
```

ASR/NLLB 大模型不会打进单文件 EXE。打包版首次下载后会持久化到
`%LOCALAPPDATA%\SoundFerry\resource`。也可以在 EXE 同级创建 `resource` 目录，作为便携模型目录。

## License

MIT（若仓库后续补充 `LICENSE` 文件，以该文件为准）
