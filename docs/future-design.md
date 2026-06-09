# Translator InTime 后续功能补充设计文档

## 1. 背景

当前项目已经具备实时双向翻译的基本能力：

- Linux 端支持 PulseAudio/PipeWire `null-sink` 与 `monitor` 捕获。
- Windows 端已接入第一版 WASAPI loopback 捕获。
- 云端模型支持火山 AST 2.0。
- 本地 ASR 支持 Fun-ASR-Nano 与 faster-whisper 优先级回退。
- UI 具备基础配置页、字幕显示和音频设备选择。

但现有实现仍偏“可跑通原型”，距离跨平台稳定产品还有明显差距。后续重点应放在：

- 双端音频路由稳定性。
- 云端/本地模型策略清晰化。
- 字幕显示体验。
- 配置安全和可维护性。
- 错误恢复、诊断和测试覆盖。

## 2. 当前主要问题

### 2.1 跨平台音频层还不统一

Linux 和 Windows 现在走的是不同实现：

- Linux：`pactl` + `parec` + `translator_virtual_sink.monitor`
- Windows：`soundcard` + WASAPI loopback

这能工作，但上层 `TranslationPipeline` 仍直接使用 `AudioStream`，没有明确表达“麦克风输入”“系统输出捕获”“TTS 输出”三种不同能力。

后续需要把音频层抽象成明确角色：

- `MicInput`
- `SystemOutputCapture`
- `TTSOutput`
- `VirtualRoute`

### 2.2 云端模型和本地模型职责混杂

当前配置中有：

- `translation.backend`
- `translation.use_cloud_model`
- `asr.backend`
- `asr.local_model_priority`

语义已经初步拆开，但管道层仍存在一些历史耦合：

- 火山 AST 是端到端语音模型，不应被当成普通文本翻译后端。
- 本地 ASR + 文本翻译是另一条链路。
- 云端失败后的本地回退策略需要更明确。

### 2.3 字幕体验不够产品化

当前字幕显示仍以主窗口 `QTextEdit` 为主，存在这些问题：

- 本地 ASR 依赖静音断句，连续演讲场景会延迟明显。
- 流式 partial/final 结果没有统一 UI 模型。
- 没有游戏内 Overlay。
- 原文和译文没有作为同一个字幕单元展示。

理想字幕体验应类似：

```text
当前行 partial：实时更新，不新增行
final 行：句子结束后定稿，进入历史记录
```

### 2.4 音频输出到游戏麦克风还不完整

当前入站字幕已经能做，但出站“你说中文 -> 外语语音 -> 游戏麦克风”仍需要补齐：

- Linux：输出到 `translator_virtual_sink`
- Windows：输出到 `CABLE Input` 或其他虚拟麦克风设备
- 防止 TTS 输出又被系统音频捕获形成回环

### 2.5 依赖和模型部署复杂

Fun-ASR-Nano 当前依赖链较复杂：

- `torch`
- `torchaudio`
- FunASR 内部 Nano 注册兼容问题
- ModelScope/HuggingFace hub 差异

火山 AST 又依赖 Protobuf 生成文件，目前仍存在对 `sayhey` 生成代码的借用风险。

后续需要把模型依赖和生成代码固化到项目内，降低环境不确定性。

## 3. 目标架构

### 3.1 总体数据流

```text
                 ┌────────────────────────┐
                 │        GUI / 设置       │
                 └───────────┬────────────┘
                             │
                 ┌───────────▼────────────┐
                 │   TranslationPipeline   │
                 └───────┬────────┬───────┘
                         │        │
          ┌──────────────▼───┐  ┌─▼────────────────┐
          │  Cloud Pipeline  │  │  Local Pipeline   │
          │  Volc AST S2S/S2T│  │  ASR + Translator │
          └──────────────┬───┘  └─┬────────────────┘
                         │        │
          ┌──────────────▼────────▼─────────────┐
          │            Subtitle Model            │
          │      partial / final / history       │
          └──────────────┬──────────────────────┘
                         │
          ┌──────────────▼──────────────────────┐
          │        MainWindow / Overlay          │
          └─────────────────────────────────────┘
```

### 3.2 音频层目标结构

```text
src/audio/
  devices.py          # 跨平台设备枚举与角色识别
  capture.py          # MicInput / SystemOutputCapture
  output.py           # TTSOutput
  route.py            # Linux null-sink / Windows VB-Cable 检测
  diagnostics.py      # RMS、电平、路由检测
```

当前的 `stream.py` 可以逐步拆分，不需要一次性重写。

### 3.3 模型层目标结构

```text
src/core/
  cloud/
    volc_ast.py       # 火山 AST S2S/S2T
  asr/
    base.py           # ASR 接口
    funasr.py         # Fun-ASR-Nano
    whisper.py        # faster-whisper
  translation/
    text.py           # 文本翻译后端
  subtitle/
    model.py          # partial/final 字幕状态
```

## 4. 功能设计

### 4.1 Windows WASAPI loopback 完整化

#### 目标

Windows 用户无需安装 VB-Cable，也能捕获系统/游戏声音。

#### 当前状态

已使用 `soundcard` 实现初版：

- 枚举 `sc.all_speakers()`
- 生成 `wasapi_loopback:<speaker name>` 设备 ID
- 通过 `include_loopback=True` 录制扬声器输出

#### 后续补充

- 在设置页明确显示：
  - `系统默认输出`
  - `[Loopback] 扬声器`
  - `[Loopback] 耳机`
- 增加“测试电平”按钮。
- 如果 RMS 长期为 0，提示用户确认应用是否输出到该扬声器。
- 支持按设备名称恢复，而不是依赖不稳定 index。
- 异常时 fallback 到默认扬声器 loopback。

#### 验收标准

- Windows 11 下播放浏览器视频，选择 `[Loopback] 默认扬声器` 后 RMS 非 0。
- 切换耳机/扬声器后能重新枚举并恢复。
- 未安装 VB-Cable 时入站字幕仍可用。

### 4.2 VB-Cable / 虚拟麦克风输出

#### 目标

出站翻译语音能进入游戏麦克风。

#### 设计

Windows：

- 检测 `CABLE Input` 作为 TTS 输出设备。
- 游戏中选择 `CABLE Input` 作为麦克风。
- 如果同时选择 `CABLE Output` 做系统音频捕获，需要提示可能形成回环。

Linux：

- 继续使用 `translator_virtual_sink`。
- TTS 输出到 `translator_virtual_sink`。
- 游戏麦克风选择对应虚拟输入或 monitor 路由。

#### 验收标准

- Windows 下 TTS 播放到 `CABLE Input` 后，游戏语音测试能收到声音。
- Linux 下 TTS 播放到 `translator_virtual_sink` 后，目标应用能收到声音。
- 检测到 TTS 输出设备和系统音频捕获设备形成回环时给出警告。

### 4.3 云端模型开关与回退策略

#### 目标

明确区分云端端到端模型和本地级联模型。

#### 配置

```yaml
translation:
  use_cloud_model: true
  cloud_provider: volc

asr:
  backend: auto
  local_model_priority:
    - funasr
    - whisper
```

#### 策略

```text
if use_cloud_model and cloud_available:
    use cloud pipeline
else:
    use local pipeline

if cloud pipeline fails during startup:
    fallback to local pipeline

if cloud pipeline disconnects during runtime:
    retry N times
    if still failed:
        fallback to local pipeline and show UI warning
```

#### 后续补充

- `cloud_provider` 字段，预留后续 Qwen/其他云端同传。
- 火山断线重连。
- 云端失败原因显示到 UI。
- 云端使用量统计。

#### 验收标准

- 云端开关关闭时，不初始化火山 AST。
- 云端开关开启且凭证可用时，不加载本地 ASR。
- 云端启动失败后自动加载本地 ASR 并继续运行。

### 4.4 本地模型优先级

#### 目标

允许用户选择本地 ASR 优先级。

#### 当前状态

已支持：

```yaml
asr:
  backend: auto
  local_model_priority:
    - funasr
    - whisper
```

#### 后续补充

- 设置页增加模型状态展示：
  - 已安装
  - 未安装依赖
  - 模型未下载
  - 加载失败原因
- 提供“一键测试本地 ASR”脚本或按钮。
- FunASR 失败时不要刷屏，应只展示摘要。
- 支持指定 FunASR hub：`ms` / `hf`。

#### 验收标准

- FunASR 缺依赖时，自动回退 Whisper。
- FunASR 成功加载时，第二个 ASR 实例复用同一模型。
- 设置优先 Whisper 时，不加载 FunASR。

### 4.5 字幕 partial/final 模型

#### 目标

解决“单词/短语碎片化”和“只有暂停才出字幕”的问题。

#### 设计

新增统一字幕事件：

```python
class SubtitleEvent:
    direction: Literal["inbound", "outbound"]
    source_text: str
    translated_text: str
    is_final: bool
    segment_id: str
```

UI 行为：

- `is_final=False`：更新当前临时行，不新增历史。
- `is_final=True`：把当前行定稿，追加到历史。

云端 AST：

- `SourceSubtitleResponse` / `TranslationSubtitleResponse` -> partial
- `SourceSubtitleEnd` / `TranslationSubtitleEnd` -> final

本地 ASR：

- 短音频窗口可作为 partial。
- 静音断句后作为 final。
- 如果本地模型不支持稳定 partial，则只输出 final。

#### 验收标准

- 连续 TED 演讲播放时，每个句子以一到两行方式更新，不刷单词。
- 暂停不是唯一触发条件，连续说话 4-6 秒也能输出稳定片段。
- 原文和译文在 UI 上作为同一个字幕单元展示。

### 4.6 Overlay 字幕窗口

#### 目标

游戏时能看到独立字幕浮层，而不是只看主窗口。

#### 设计

新增：

```text
src/gui/overlay_window.py
```

能力：

- 置顶。
- 半透明背景。
- 可拖动。
- 可锁定点击穿透。
- 字体大小、透明度、最大行数可配置。
- 主窗口和 Overlay 同步显示。

#### 验收标准

- Windows/Linux 均可显示置顶字幕。
- 用户可拖动位置并保存。
- 开启点击穿透后不影响游戏鼠标操作。

### 4.7 热词和术语

#### 目标

提升游戏词汇、专有名词、演讲术语的识别和翻译准确度。

#### 设计

统一热词来源：

```text
hotwords/
  game_zh_en.txt
  ted_en_zh.txt
```

本地 ASR：

- FunASR：按语言过滤 hotwords。
- Whisper：使用 `initial_prompt`。

火山 AST：

- `hot_words_list`：识别热词。
- `glossary_list`：翻译术语。

文本翻译：

- LLM 后端把术语表写入系统提示词。

#### 验收标准

- 切换场景后热词随配置更新。
- 英文识别时不再注入中文热词。
- 火山 StartSession 中能看到 corpus 配置。

### 4.8 LLM 翻译和润色

#### 目标

改善传统翻译后端的自然度。

#### 设计

新增翻译策略：

```yaml
translation:
  text_backend: microsoft
  refine_with_llm: false
  llm_backend: openai | qwen | ollama | volc_ark
```

模式：

- 纯机器翻译：低延迟。
- LLM 翻译：更自然。
- 机器翻译 + LLM 润色：折中。

#### 验收标准

- TED 场景译文更像自然中文。
- 游戏语音能保持短句、口语化。
- 用户可设置延迟和质量优先级。

## 5. 诊断与可观测性

### 5.1 音频诊断

已有：

- `scripts/check_pulse_audio.py`
- `scripts/check_windows_audio.py`

后续补充：

- GUI 内置“测试麦克风电平”。
- GUI 内置“测试系统音频电平”。
- GUI 内置“测试 TTS 输出”。

### 5.2 模型诊断

新增脚本：

```text
scripts/check_models.py
```

检查：

- FunASR 是否可导入。
- Torch / torchaudio 是否可导入。
- Fun-ASR-Nano 是否可加载。
- Whisper 模型是否可加载。
- 火山凭证是否能握手。

### 5.3 日志指标

建议记录：

- ASR 耗时。
- 翻译耗时。
- TTS 耗时。
- 音频 RMS。
- 云端重连次数。
- 字幕 final 数量。
- 失败后端。

## 6. 测试计划

### 6.1 Linux

- PulseAudio / PipeWire 下 monitor 捕获。
- `translator_virtual_sink` 创建和复用。
- `parec` 进程退出恢复。
- TTS 输出到虚拟 sink。

### 6.2 Windows

- WASAPI loopback 捕获默认扬声器。
- 捕获指定耳机。
- 插拔设备后重新枚举。
- VB-Cable 输出到游戏麦克风。
- 未安装 VB-Cable 时仍可入站字幕。

### 6.3 模型

- 云端火山 AST。
- FunASR CPU。
- Whisper CPU。
- FunASR 失败回退 Whisper。
- 云端失败回退本地。

### 6.4 字幕

- TED 演讲连续语音。
- 游戏短句语音。
- 背景噪声。
- 中英混合。
- 长句断句。

## 7. 实施路线

### 阶段 1：跨平台音频可用

目标：Linux/Windows 都能捕获系统输出。

任务：

- 完善 Windows WASAPI loopback。
- 增加 GUI 电平测试。
- 增加设备恢复和 fallback。

预计：1-2 天。

### 阶段 2：模型策略稳定

目标：云端/本地选择清晰，失败能回退。

任务：

- 固化 FunASR 依赖和加载逻辑。
- 火山 Protobuf 代码纳入项目。
- 云端断线重连。
- 本地优先级 UI 完善。

预计：2-3 天。

### 阶段 3：字幕体验产品化

目标：字幕像实时同传，而不是日志输出。

任务：

- partial/final 字幕模型。
- Overlay 窗口。
- 原文/译文合并显示。
- 去重和稳定输出。

预计：3-5 天。

### 阶段 4：质量优化

目标：翻译更自然，术语更准。

任务：

- 热词/术语注入。
- LLM 翻译/润色后端。
- 场景模板。

预计：3-5 天。

### 阶段 5：测试和打包

目标：能稳定给别人安装使用。

任务：

- 单元测试。
- 音频集成测试。
- Windows/Linux 安装脚本完善。
- PyInstaller/Nuitka 打包验证。

预计：1 周。

## 8. 优先级建议

优先做：

1. Windows loopback 电平测试和设备恢复。
2. partial/final 字幕模型。
3. 火山 Protobuf 内置。
4. FunASR 加载稳定化。
5. Overlay。

暂缓做：

1. 多云厂商同时接入。
2. 复杂账号/计费系统。
3. 大规模模型管理界面。
4. 自动安装虚拟声卡驱动。

## 9. 风险

### 9.1 Fun-ASR-Nano 生态仍不稳定

FunASR pip 包和 Nano 模型存在远程代码、注册、依赖问题。需要保留 Whisper 作为稳定兜底。

### 9.2 Windows 音频设备差异大

不同机器、耳机、声卡、驱动会导致 WASAPI 行为不同。必须提供诊断和 fallback。

### 9.3 云端同传成本和稳定性

火山 AST 效果好，但依赖网络、账号权限和额度。必须提供本地回退。

### 9.4 CPU 本地模型实时性

Fun-ASR-Nano CPU 可能无法满足低延迟实时字幕。需要允许用户在质量和延迟之间选择。

## 10. 结论

项目要走 Linux + Windows 双端，最关键不是继续堆模型，而是把三条基础能力做稳：

1. 跨平台音频捕获和输出。
2. 清晰的云端/本地模型调度。
3. 面向实时观看的字幕 UI 模型。

完成这三项后，再优化翻译自然度、热词、打包和测试，整体体验会比现在稳定很多。
