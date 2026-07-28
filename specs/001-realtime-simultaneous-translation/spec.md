# Feature Specification: Realtime Simultaneous Translation

**Feature Branch**: `001-realtime-simultaneous-translation`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "做一个实时同传软件。用户可以使用它将麦克风的音频识别和翻译并且tts生成成指定语言的音频输入，将对端播放的音频识别和翻译成用户使用的或制定的语言音频输出，方便国际友人间交流。有本地方案和云端方案两种，tts有云端模型和本地模型，翻译有云端api和本地模型，根据用户选择来使用，并且不仅有音频输入输出，还有实时字幕，方便用户看识别的准确与否，以及看对方说了什么方便在音频没听清楚的时候校验，字幕要透明背景，可以缩放，可以锁定"

## Clarifications

### Session 2026-06-18

- **Q**: 字幕历史保留策略与窗口数量？ → **A**: 分方向显示两个字幕窗口（outbound / inbound），每个窗口仅保留当前 1–2 句；完整历史自动写入本地日志文件，按天轮替。
- **Q**: 云端 API 失败时的重试与降级策略？ → **A**: 指数退避重试 3 次（间隔 1s → 2s → 4s），全部失败后自动降级到已配置的本地备用引擎并 UI 提示；若本地备用未配置则暂停该方向处理并提示用户手动恢复。
- **Q**: 音频设备热插拔时的系统行为？ → **A**: 自动暂停对应方向的同传，显示设备断开提示，检测到设备重新插入后自动恢复；若设备未恢复，用户可手动选择其他可用设备。
- **Q**: 实时性能指标是否向用户暴露？ → **A**: 仅在调试/高级模式下显示 E2E 延迟、引擎负载等性能指标；正常模式保持 UI 简洁，但引擎降级、设备断开等关键状态仍需以非侵入式方式提示用户。
- **Q**: 字幕窗口内原文与翻译的显示布局？ → **A**: 上下排列（原文在上，翻译在下），不使用分隔线或任何图形元素，仅通过字体颜色/透明度区分；窗口保持完全透明背景。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Outbound Realtime Interpretation (Priority: P1)

用户开启同传后，对着麦克风说母语，系统实时将语音转写为文字、翻译成目标语言、并通过TTS合成为外语语音输出到对端，使对方听到的是翻译后的外语。

**Why this priority**: 这是同传软件的 outbound 核心路径，没有它用户无法让国际友人听懂自己说的话。

**Independent Test**: 启动 outbound 模式，播放一段中文测试音频到麦克风输入，验证对端是否能听到对应英文语音，且延迟可接受。

**Acceptance Scenarios**:

1. **Given** 用户已选择 outbound 源语言为中文、目标语言为英文，**When** 用户对着麦克风说"你好"，**Then** 对端在2秒内听到英文"Hello"的语音，同时用户屏幕上出现中文原文和英文翻译的实时字幕
2. **Given** 用户已切换为本地翻译引擎和本地TTS，**When** 用户离线状态下说话，**Then** 系统仍然能完成识别→翻译→TTS并输出，不依赖外部网络

---

### User Story 2 - Inbound Realtime Interpretation (Priority: P1)

用户开启同传后，对端说外语，系统通过虚拟音频设备捕获对端播放的语音，实时转写、翻译成用户母语，并通过TTS合成为母语语音输出到用户耳机，同时显示双语字幕。

**Why this priority**: 这是同传软件的 inbound 核心路径，没有它用户无法听懂国际友人说的话。

**Independent Test**: 启动 inbound 模式，向虚拟音频设备播放一段英文测试音频，验证用户耳机是否能听到对应中文语音，且字幕同步显示。

**Acceptance Scenarios**:

1. **Given** 用户已选择 inbound 源语言为英文、目标语言为中文，**When** 对端说"Hello"，**Then** 用户在2秒内听到中文"你好"的语音，同时屏幕上出现英文原文和中文翻译的实时字幕
2. **Given** 用户同时开启了 outbound 和 inbound，**When** 双方同时说话，**Then** 两条语音流互不干扰，各自独立翻译输出，字幕分别显示

---

### User Story 3 - Realtime Subtitle Overlay (Priority: P2)

系统在处理 inbound/outbound 音频时，同步在屏幕上显示两个完全透明背景、无边框的双语字幕窗口：一个用于 outbound（显示用户说的原文及翻译），一个用于 inbound（显示对端说的原文及翻译）。每个窗口内原文在上、翻译在下，仅通过字体颜色/透明度区分，不使用任何分隔线或图形元素。每个窗口仅保留当前最新的 1–2 句字幕；历史内容自动追加写入本地日志文件。用户可以分别拖动、缩放、锁定两个字幕窗口的位置，防止误触。当音频听不清时，用户可以通过阅读字幕来校验识别和翻译的准确性。

**Why this priority**: 字幕是同传体验的重要补充，尤其在嘈杂环境或模型识别错误时，用户需要视觉校验手段；分方向显示避免双方内容混在一起，提升可读性。

**Independent Test**: 启动双向翻译模式，验证 outbound 和 inbound 字幕窗口是否分别跟随对应语音流实时滚动；测试各窗口的拖动、缩放、锁定功能是否正常；检查本地日志文件是否按时间顺序记录了完整历史字幕。

**Acceptance Scenarios**:

1. **Given** 同传已启动且双向均开启，**When** 用户说"你好"、对端说"Hello"，**Then** outbound 字幕窗口在 200 ms 内显示"你好 / Hello"，inbound 字幕窗口在 200 ms 内显示"Hello / 你好"，两窗口内容互不干扰
2. **Given** 同传持续进行中，**When** 任意方向产生第 3 句新字幕，**Then** 该方向字幕窗口仅保留最新的 2 句，旧内容从 UI 移除但已写入本地日志
3. **Given** 字幕窗口已显示，**When** 用户拖动 outbound 窗口到屏幕左下角并点击锁定按钮，**Then** 该窗口固定在该位置，inbound 窗口仍可独立拖动，后续语音不再触发 outbound 窗口移动
4. **Given** 字幕窗口已锁定，**When** 用户双击窗口边缘，**Then** 窗口进入缩放模式，用户可调整大小，字幕字号自适应窗口尺寸

---

### User Story 4 - Engine Selection and Fallback (Priority: P2)

用户可以在设置中选择 ASR、翻译、TTS 分别使用本地模型或云端服务。系统根据用户选择加载对应引擎；如果本地模型未下载或加载失败，系统提示用户并提供一键下载/修复引导。

**Why this priority**: 让用户在不同网络环境和硬件条件下都能使用同传功能（本地适合隐私/离线，云端适合低配置机器）。

**Independent Test**: 在设置中切换翻译引擎为本地混元模型和云端OpenAI，验证两种模式下 outbound/inbound 都能正常工作。

**Acceptance Scenarios**:

1. **Given** 用户在设置页选择"本地翻译+本地TTS"，**When** 点击保存并启动同传，**Then** 系统加载本地模型进行翻译和语音合成，不调用任何外部API
2. **Given** 用户选择本地翻译但模型文件缺失，**When** 点击保存或启动同传，**Then** 系统弹出提示"模型未找到"，并在设置页显示"一键下载"按钮
3. **Given** 用户正在使用云端翻译，**When** 网络断开或API返回错误，**Then** 系统以指数退避策略重试 3 次（间隔 1s → 2s → 4s），若仍失败则自动切换到已配置的备用引擎（本地优先），并 UI 提示用户当前已降级

### Edge Cases

- What happens when both sides speak simultaneously? → 系统必须分离两条音频流，独立处理，不串音
- What happens when a local model runs out of memory or takes too long? → 自动降级到云端引擎（如果已配置），或提示用户暂停并释放资源
- What happens when the subtitle window is moved off-screen? → 窗口边缘检测，防止完全移出可视区域
- How does the system handle very long continuous speech (>30 seconds)? → ASR应分段输出结果，避免一次性处理过长音频导致延迟累积
- What happens when the user switches languages mid-session? → 允许热切换，当前会话已缓存的结果保持，新语音按新语言处理
- What happens if the subtitle log file grows too large or the disk is full? → 日志按天轮替，单文件上限 10 MB；磁盘不足时停止写入日志并UI提示，不阻塞同传主流程
- What happens if the user only enables outbound or inbound (not both)? → 仅显示对应方向的单个字幕窗口，另一个窗口隐藏；窗口位置仍分别记忆
- What happens when a cloud API returns an error or times out? → 指数退避重试 3 次（1s → 2s → 4s），全部失败后自动降级到已配置的本地备用引擎，UI 提示降级状态；若本地备用也未配置，则暂停该方向处理并提示用户手动恢复
- What happens when the microphone or headphones are unplugged mid-session? → 自动暂停对应方向的同传，显示设备断开提示，检测到设备重新插入后自动恢复；若设备未恢复，用户可手动选择其他可用设备

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST capture microphone audio in real time, transcribe it to text (ASR), translate it to a user-selected target language, synthesize the translated text into speech (TTS), and route the synthesized audio to the designated output device for the counterparty to hear.
- **FR-002**: System MUST capture counterparty audio via a virtual audio device (or equivalent loopback), transcribe it, translate it to the user's native language, synthesize it, and play it through the user's headphones/speakers.
- **FR-003**: Users MUST be able to independently select ASR, translation, and TTS engines as either local models or cloud APIs in the settings panel.
- **FR-004**: System MUST display real-time bilingual subtitles in two separate fully transparent, borderless overlay windows: one for outbound (user's original + translated text) and one for inbound (counterparty's original + translated text). Within each window, the original text MUST appear above the translated text, differentiated only by font color or opacity; no separators, borders, or graphical elements MUST be used. Each window MUST retain only the latest 1–2 utterances; older content MUST be removed from the UI but persisted to a local log file.
- **FR-005**: Users MUST be able to independently drag, resize, and lock each subtitle window position via mouse interaction; when locked, a window MUST ignore further drag events until unlocked. Window positions and locked states MUST persist across application restarts.
- **FR-010**: System MUST append every generated `SubtitleEntry` to a local timestamped log file (e.g., `subtitles_YYYY-MM-DD_HH-MM-SS.txt`) in a human-readable format; log files MUST be rotated daily or when size exceeds 10 MB to prevent unbounded disk growth.
- **FR-006**: System MUST support hot-swapping source/target languages without restarting the translation pipeline; in-flight audio segments MAY finish under the old language pair.
- **FR-007**: When a local model is selected but missing or unloadable, System MUST display a clear UI message and offer a one-click download action; if a cloud fallback is configured, System MAY automatically switch to it.
- **FR-008**: System MUST mask API keys in the settings UI and persist them securely; raw credentials MUST NOT appear in logs or UI state labels.
- **FR-009**: All heavy processing (ASR, translation, TTS inference) MUST run on background threads or processes; the UI thread MUST remain responsive at all times.
- **FR-011**: When a cloud API returns an error or times out, System MUST retry up to 3 times with exponential backoff (1s → 2s → 4s); if all retries fail, System MUST automatically fall back to the configured local backup engine and display a UI degradation notice. If no local backup is configured, System MUST pause that direction's processing and prompt the user to resume manually.
- **FR-012**: System MUST monitor active audio input/output devices during a session; if the configured device becomes unavailable, System MUST automatically pause the affected direction, display a device-disconnected notice, and resume automatically when the device is re-detected. Users MUST be able to manually select an alternative device if the original does not return.

### Key Entities

- **TranslationSession**: Represents an active simultaneous-interpretation session. Attributes: session_id, inbound_enabled, outbound_enabled, source_language, target_language, asr_engine, trans_engine, tts_engine, start_time, status.
- **SubtitleEntry**: A single line of bilingual subtitle. Attributes: timestamp, original_text, translated_text, speaker_direction (inbound/outbound), displayed (bool).
- **EngineConfig**: User-selected engine preferences. Attributes: asr_backend (local/cloud), asr_model, trans_backend (local/cloud), trans_model/credentials, tts_backend (local/cloud), tts_model/voice.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can start a two-way interpretation session in under 5 clicks from application launch.
- **SC-002**: End-to-end voice-to-voice latency (microphone in → translated voice out) averages under 2 seconds for cloud engines and under 3 seconds for local engines on recommended hardware.
- **SC-003**: Subtitle text appears on screen within 500 ms of the corresponding audio being spoken.
- **SC-004**: Users can reposition and lock the subtitle window in under 3 seconds; the locked state persists across application restarts.
- **SC-005**: The application remains fully functional for at least 60 minutes of continuous two-way translation without memory growth exceeding 500 MB or requiring a restart.
- **SC-006**: When switching from cloud to local engines (or vice versa), the transition completes in under 10 seconds with no more than 2 seconds of audio interruption.

## Assumptions

- Users have a microphone and headphones/speakers available; for game or meeting software integration, a virtual audio cable (VB-Cable on Windows, PulseAudio null-sink on Linux) is assumed to be installed or installable.
- The application targets desktop environments (Windows/Linux primary); mobile support is out of scope for this feature.
- Local model files (ASR, translation, TTS) are large; users are expected to have sufficient disk space and are willing to wait for initial downloads.
- Internet connectivity is assumed for cloud-engine usage; local-engine usage is the fallback for offline scenarios.
- Language support for the first release focuses on Chinese, English, Japanese, and Korean; additional languages can be added incrementally without architectural changes.
