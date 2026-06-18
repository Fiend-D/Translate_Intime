# 翻译配置界面输入框排版
_Exported on 2026/6/9 at GMT+8 10:54:31 from Cursor (3.5.33)_

---

**User**

我做了一个工具，但是UI排版有点问题，翻译配置界面输入框挤在一起，帮我调整一下

---

**Cursor**

我先看一下项目结构和翻译配置界面相关文件，确认它用的 UI 框架和布局方式，然后再做针对性的间距调整。

我找到了配置窗口主要在 `src/gui/settings_dialog.py`，接下来会读它的布局代码和样式，重点看翻译配置区域的表单组织。

翻译页的问题看起来主要是：`auto` 后端会同时显示多组配置，但页面没有滚动区域，多个 `QGroupBox` 会在固定高度里被压缩。我会把翻译页改成可滚动的分组表单，并统一加大表单行距和边距。

布局改动已经完成：翻译页现在有滚动区域，基础配置也放进独立分组，后端配置表单使用统一的行距、边距和字段伸展策略。接下来我会做一次快速语法检查和诊断检查。

当前环境里 `python` 命令不存在，但编辑器诊断没有报错。我再用 `python3` 跑同样的语法检查确认一下。

语法检查通过了。最后我看一下工作区变更，避免把检查生成的缓存文件也算进本次改动。

这个目录不是 Git 仓库，所以无法用 `git status` 查看变更。我会直接检查是否生成了 `settings_dialog` 的字节码缓存。

已调整翻译配置界面布局，改动在 `src/gui/settings_dialog.py`：

- 设置窗口最小尺寸从 `550x500` 调整为 `660x560`。
- 翻译页加入可滚动区域，避免 `auto` 后端同时显示多组配置时输入框被压扁。
- 把基础翻译配置放入独立分组。
- 统一翻译配置表单的边距、行距、标签对齐和输入框伸展策略。

验证已完成：`python3 -m py_compile src/gui/settings_dialog.py` 通过，编辑器诊断无 lint 错误。

---

**User**

@/home/fiend/.cursor/projects/home-fiend-WorkSpace-python-translator-intime/terminals/3.txt:22-38 

---

**Cursor**

我看到报错是 `from dotenv import load_dotenv` 找不到模块，但终端里安装的是当前目录的 `./.venv`，运行时的 `python` 可能不是同一个解释器。我先检查项目依赖和虚拟环境结构，确认是依赖声明问题还是解释器路径不一致。

`requirements.txt` 里已经有正确依赖 `python-dotenv`，代码导入也没写错。现在重点是核对 `pip` 和 `python` 是否来自同一个虚拟环境。

在我这边的非激活 shell 里，`pip` 指向系统 `/usr/bin/pip`，并且没有 `python` 命令；这和你终端里的 `(.venv)` 提示不完全一致。下一步我会定位项目里的虚拟环境目录，看看实际可执行文件名字和包安装位置。

项目里同时有 `venv` 和 `.venv`，而 `dotenv` 包两个环境里都有；终端报错更像是运行时没用到这两个环境里的解释器。最直接的修复是用虚拟环境里的 Python 启动，或重新激活正确环境。
这个报错不是代码导入写错，`requirements.txt` 里已经有 `python-dotenv`，而且项目的 `.venv`/`venv` 里也能看到 `dotenv` 包。问题基本是：`pip install` 和 `python run.py` 用的不是同一个 Python 环境。

在项目目录执行下面其中一种：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

如果 `.venv` 这个环境有问题，就试 `venv`：

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

以后安装包建议都用：

```bash
python -m pip install 包名
```

不要直接用 `pip install`，这样可以确保包安装到当前正在运行的那个 Python 里。另外这里不需要单独装 `dotenv`，正确依赖名是 `python-dotenv`。

---

**User**

我该怎么申请火山模型api

---

**Cursor**

我先看一下你这个项目里火山引擎后端实际需要哪些参数，这样能按当前工具的配置项告诉你该申请哪个服务、拿什么凭证。

你这个项目里用的不是“豆包大模型 API”，而是火山引擎的“实时语音翻译 AST”服务，需要拿到：

- `APP ID`
- `Access Token`
- 可选 `Resource ID`，项目默认是 `volc.service_type.10053`

申请步骤大概是：

1. 打开火山引擎控制台  
   [https://console.volcengine.com](https://console.volcengine.com)

2. 注册/登录并完成实名认证  
   国内云服务一般都需要实名认证，否则很多语音能力不能开通。

3. 进入语音技术服务  
   可以直接试这个项目代码里写的入口：  
   [https://console.volcengine.com/speech/service/8](https://console.volcengine.com/speech/service/8)

4. 找到“实时语音翻译”或 AST 相关服务  
   开通服务后，通常会有“应用管理 / App 管理 / 访问凭证”之类页面。

5. 创建应用  
   创建后拿到 `APP ID` 和 `Access Token`。

6. 回到你的工具设置界面  
   在“设置 -> 翻译 -> 翻译后端”选择 `volc`，然后填：

   - `APP ID`: 火山控制台里的 App ID
   - `Token`: 火山控制台里的 Access Token

如果你说的是“豆包/火山方舟大模型 API”，那申请路径不一样：要进入“火山方舟 Ark”，创建 API Key 和模型接入点。但你当前项目的火山配置界面用的是语音翻译服务，不是方舟大模型 API。

---

**User**

4 | WARNING  | 游戏声音捕获设备打开失败: Error querying device 28
10:44:34 | ERROR    | 管道错误: 'TranslationPipeline' object has no attribute '_outbound_loop'


---

**Cursor**

这两个报错分别指向音频设备配置和管道代码缺失方法；我先检查 `TranslationPipeline` 的启动逻辑，确认 `_outbound_loop` 应该怎么恢复或替换。

我确认到核心 bug：`pipeline.py` 里“本地出站管道”的代码块还在，但 `async def _outbound_loop(...)` 函数头丢了，所以启动时找不到这个方法。设备 28 的警告则是配置里保存了一个当前系统不存在的音频设备 ID。先修复缺失方法，再处理设备回退体验。

我会做两个小改动：恢复 `_outbound_loop` 方法头，解决启动崩溃；同时把默认配置里的游戏声音捕获设备改成空，避免新环境默认使用过期的 `device 28`。

默认配置里的 `game_output_device` 现在已经是 `null`，所以设备 28 多半来自你之前保存过的配置或运行中的旧配置；这不会再导致主流程崩溃。我现在先修复真正的管道代码错误。

缺失的 `_outbound_loop` 已恢复。接下来我跑语法检查和 lint，确保这个修复没有引入新的问题。

已修复导致崩溃的代码问题：`src/core/pipeline.py` 里缺失的 `_outbound_loop` 方法头已经补回来了。语法检查通过，lint 无错误。

你现在可以重新运行：

```bash
python run.py
```

关于这条警告：

```text
游戏声音捕获设备打开失败: Error querying device 28
```

这是保存的“游戏声音捕获设备”ID 已经失效了。它不会再导致主流程崩溃，但入站游戏声音翻译会被禁用。你可以打开“设置 -> 音频设备”，把“游戏声音捕获”重新选一个当前存在的设备，或者选系统默认/先留空，只用“你说中文 -> 翻译成外语”的出站模式。

---

**User**

INFO     | 火山引擎管道启动
10:47:57 | ERROR    | 火山引擎连接失败: 404, message='Invalid response status', url='wss://openspeech.bytedance.com/api/v1/ast/translate?appid=3210380753&nonce=b1d6cdc4999c422e84a6cfa377923e70&resource_id=volc.service_type.10053&timestamp=1780541276&signature=D2yqnzVLGZIfEpPY2A6av2yOSyC7Y7cD/UFl0WTIMXk='
10:47:57 | ERROR    | 火山引擎连接失败，回退本地模式


---

**Cursor**

这个 404 说明已经发起 WebSocket 连接了，但火山服务端不认当前接口路径；不像是网络断开，更像是项目里写的 AST URL 或鉴权方式和你开通的服务不匹配。我先核对当前火山语音翻译文档/接口地址，再决定是改 endpoint 还是提示你换用正确服务凭证。

查到原因了：代码里还在连旧地址 `api/v1/ast/translate`，而火山现在的同声传译 2.0 文档使用 `wss://openspeech.bytedance.com/api/v4/ast/v2/translate`，鉴权也不是 URL 签名参数，而是 WebSocket 请求头。除此之外，当前“回退本地模式”只是打日志，没有真正启动本地管道，我会一起修掉。

火山连接的地址和鉴权方式已更新。现在我补上真正的本地回退：火山握手失败时继续走本地 ASR/翻译/TTS，而不是让任务直接结束。

我还需要验证语法和诊断。需要注意：这次修的是握手 404 和回退逻辑；火山 AST 2.0 后续还要求 Protobuf 二进制协议，当前项目里发送音频部分仍可能需要继续适配。

已修复这次 404 的主要原因：

- `src/core/volc_engine.py`：火山接口从旧的 `api/v1/ast/translate` 改为新版 `api/v4/ast/v2/translate`。
- 鉴权从 URL 签名参数改为 WebSocket Header：`X-Api-App-Key`、`X-Api-Access-Key`、`X-Api-Resource-Id`、`X-Api-Connect-Id`。
- `src/core/pipeline.py`：火山连接失败时现在会真正回退到本地出站翻译管道，不会只打日志后停止。

已通过语法检查和 lint。

你可以重新运行试一下：

```bash
python run.py
```

提醒一下：火山 AST 2.0 后续通信要求 Protobuf 二进制协议，当前项目里的火山音频发送部分还比较简化。如果这次不再 404，但后面出现协议/消息格式错误，那下一步需要继续适配火山的 StartSession 和音频帧协议。

---

**User**

 | 火山引擎连接失败: 403, message='Invalid response status', url='wss://openspeech.bytedance.com/api/v4/ast/v2/translate'
10:49:16 | ERROR    | 火山引擎连接失败，回退本地模式


---

**Cursor**

403 说明新版地址已经命中了，但鉴权被拒绝了；我先核对文档里“新版控制台”和“旧版控制台”的请求头字段，看看你的配置项是不是把 App ID/App Key 混用了。

文档里旧版凭证对应的是 `X-Api-App-Id` + `X-Api-Access-Key`，我刚才按部分示例用了 `X-Api-App-Key`，这很可能就是 403 的直接原因。我会改成官方鉴权表里的字段名；如果之后仍 403，就说明凭证或服务开通本身不对。

鉴权头字段已修正。现在我做一次语法和诊断检查，然后给你说明如果仍然 403 该在火山控制台检查哪些项。

已修正 403 可能的代码原因：火山旧版凭证鉴权头应使用 `X-Api-App-Id`，不是 `X-Api-App-Key`。`src/core/volc_engine.py` 已更新，语法检查和 lint 都通过。

你重新运行试一下：

```bash
python run.py
```

如果还是 403，基本就是控制台配置问题，重点检查这几项：

- 你填的是“同声传译/AST 服务”的 `App ID` 和 `Access Token`，不是火山方舟/豆包大模型的 API Key。
- 火山控制台里已经开通“同声传译 2.0 / AST”服务。
- `Access Token` 没有复制错、没有多余空格。
- `resource_id` 使用默认 `volc.service_type.10053`。

另外，新版控制台可能只给 `API Key`，这种和现在界面的 `App ID + Access Token` 不是同一种鉴权方式。若你拿到的是单个 API Key，我可以继续把界面和代码改成兼容新版 `X-Api-Key` 鉴权。

---

**User**

你可以参考上一级目录下的sayhey项目

---

**Cursor**

我去上一级目录找 `sayhey` 里的火山接入实现，对比它的鉴权、URL 和协议处理方式，再把这个项目按可复用的部分修正。

上一级没有直接叫 `sayhey` 的目录，我会先列一下 `/home/fiend/WorkSpace/python` 下的项目名，找实际路径或大小写差异。

工具的 glob 返回了递归文件，没有显示目录本身。我用目录列表确认一下父目录里是否有隐藏路径或不同命名。

`/home/fiend/WorkSpace/python` 下没有 `sayhey`，可能是在 `/home/fiend/WorkSpace` 或命名不同。我继续在工作区上层按目录名和火山关键词找一下。

找到了，实际路径是 `/home/fiend/WorkSpace/sayhey/SayHey`。我会重点看它的 `volc_engine.py`、`huoshan_s2t_engine.py` 和 demo，抽取可用实现。

`sayhey` 使用的是新版 `API Key` 鉴权加 Protobuf 消息，和当前项目的 `App ID + Access Token + JSON/裸 PCM` 完全不同。我先确认这个项目是否已有 `protobuf/websockets` 依赖和生成的 `python_protogen` 文件，决定是复用还是复制最小必要文件。

要完整复用 `sayhey` 的实现，需要把它生成好的 Protobuf Python 文件带过来，并新增 `protobuf`、`websockets` 依赖。我会先看这些生成文件大小，避免手工改坏。

我会先按 `sayhey` 的方式改火山引擎：使用 `VOLC_APP_KEY/API Key` 鉴权、发送 `TranslateRequest` Protobuf，而不是 JSON 和裸 PCM。为了尽快验证，我会临时复用 sibling 项目的 `python_protogen` 生成代码；之后需要的话再把它复制进当前项目。

火山引擎代码已经切到 `sayhey` 的 API Key + Protobuf 方式。接下来我会把依赖补进 `requirements.txt`，并把设置界面里的字段文案改成“API Key”，避免继续填 App ID。

为了和配置文件也对齐，我再把火山配置字段注释改清楚：当前仍复用原字段名保存，但含义改为 API Key，避免破坏已有配置读写。

我注意到 sibling 路径需要从 `WorkSpace` 层找 `sayhey`，我先修正这个 fallback 路径，再跑检查。

现在我做语法检查和诊断检查；如果缺少 `protobuf`，会提示安装新依赖。

语法和 lint 通过了。我再实际 import 一下火山模块，确认 Protobuf 生成代码路径和依赖能被找到。

系统 `python3` 没装项目运行依赖，导入被 `faster_whisper` 卡住了。我改用项目虚拟环境里的解释器再验证。

虚拟环境存在，我用 `.venv/bin/python` 再做真实导入检查。

导入 `src.core` 会先加载 ASR 模块，而这个环境缺 `faster_whisper`，所以我绕过包初始化直接加载 `volc_engine.py` 本身来验证。

`.venv` 里连 `aiohttp` 也没有，说明你实际运行的可能是另一个 `venv`。我用 `venv/bin/python` 再检查一次。

我发现一个兼容细节：如果你之前 Token 框里还留着旧 Access Token，当前优先级会误用它。我把优先级改成先用“API Key”输入框，再兼容旧 Token。

优先级已修正。最后再跑一次语法和 lint 检查。

已按上一级 `sayhey/SayHey` 的实现调整火山接入方式：

- 火山鉴权改为 `sayhey` 使用的新版 `API Key` 方式：`X-Api-Key` / `X-Api-App-Key`。
- WebSocket 消息改为发送火山 AST 2.0 的 `TranslateRequest` Protobuf，不再发送 JSON/裸 PCM。
- 设置界面里火山字段改成了 `API Key`，并提示旧 `APP ID` 不能用于这个接口。
- `requirements.txt` 补了 `protobuf>=6.31.1,<7`。
- 语法检查和 lint 已通过。

你现在需要在“设置 -> 翻译 -> 火山引擎”里这样填：

- `API Key`：填写 `sayhey` 里 `.env` 的 `VOLC_APP_KEY`
- `兼容 Token`：先清空
- 后端选择：`volc`

然后执行一次依赖更新：

```bash
python -m pip install -r requirements.txt
python run.py
```

注意：当前先复用了 sibling `sayhey/SayHey/python_protogen` 的 Protobuf 生成代码，所以这个项目暂时依赖上一级的 `sayhey` 目录存在。后面如果要独立打包，我再把这些生成文件正式复制进当前项目。

---

**User**

同声传译2.0-API接入文档

简介 #
本文档介绍如何通过WebSocket协议实时访问同传大模型 (AST)服务，主要包含鉴权相关、协议详情、常见问题和使用Demo四部分。支持s2s（Speech-to-Speech），s2t（Speech-to-Text），目前支持支持克隆本身说话人的音色，支持的语种如下：

输入/输出模式

源语种/目标语种设置模式

支持语种

语种数量

备注

语音到语音（S2S）模式

需指定源语种、目标语种
源语种或目标语种必须是zh中文/en英语
zh中文、en英语、pt葡萄牙语、es西班牙语、ja日语、id印尼语、de德语、fr法语

8

如果目标语种为zh中文/en英语，可支持使用公版音色播报，可选2个音色：

zh_female_vv_uranus_bigtts
zh_male_jingqiangkanye_emo_mars_bigtts
自动识别免切换

zh中文、en英语

2

语音到文本（S2T）模式

需指定源语种、目标语种
源语种或目标语种必须是zh中文/en英语
方言仅支持作为源语种
外语：​zh中文、en英语、pt葡萄牙语、es西班牙语、ja日语、id印尼语、de德语、fr法语、ru俄语、it意大利语、ko韩语、ar阿拉伯语、tr土耳其语、ms马来语、vi越南语、th泰语、nl荷兰语、ro罗马尼亚语、pl波兰语、cs捷克语
方言：​粤语、上海话

20外语
2方言

AST 服务使用的接口地址是：wss://openspeech.bytedance.com/api/v4/ast/v2/translate

非业务直接相关协议 #

鉴权 #
在 websocket 建连的 HTTP 请求头（Request Header 中）添加以下信息
使用新版控制台时，推荐采用以下更简化的鉴权方式。

Key

说明

参数类型

是否必须

Value示例

X-Api-Key

使用火山引擎控制台获取的API Key，可参考 控制台API Key管理

string

必须

"your-api-key"

X-Api-Resource-Id

表示调用服务的资源信息 ID，是固定值

string

必须

volc.service_type.10053

headers = {
    "X-Api-Key": "your-api-key",
    "X-Api-Resource-Id": "volc.service_type.10053"
}
若使用旧版控制台，鉴权方式如下。建议尽快切换至新版，以体验更便捷的鉴权流程。

Key

说明

参数类型

是否必须

Value示例

X-Api-App-Id

使用火山引擎控制台获取的App-Id，可参考 控制台API Id管理

string

必须

“12345678”

X-Api-Access-Key

使用火山引擎控制台获取的Access Token，可参考 控制台使用FAQ-Q1（旧版控制台使用，新版控制台只需要X-Api-Key即可）

string

必须

“your-access-key”

X-Api-Resource-Id

表示调用服务的资源信息 ID，是固定值

string

必须

volc.service_type.10053

headers = {
    "X-Api-App-Id": "123456789",
    "X-Api-Access-Key": "your-access-key",
    "X-Api-Resource-Id": "volc.service_type.10053"
}
websocket 握手成功后，会返回 Response header

Key

说明

Value 示例

X-Tt-Logid

服务端返回的 logid，建议用户获取和打印方便定位问题

202407261553070FACFE6D19421815D605


建连 HTTP 请求头示例 #
GET /api/v4/ast/v2/translate 
Host: openspeech.bytedance.com
X-Api-App-Key: 123456789
X-Api-Resource-Id: volc.service_type.10053

# 返回 Header
X-Tt-Logid: 202407261553070FACFE6D19421815D605

业务协议详情 #

Protobuf #

protos.tar.gz
未知大小

构建方法：下载并解压上面的gzip压缩包后，参考其中的HOWTO.md教程

目前有Go，Python, Java语言的构建教程，此压缩包为Go的示例教程， Python, Java语言的构建教程直接打包在下方Client Demo中，请直接下载获取。


Client Demo #
Go：


ast_go_client.zip
未知大小


Python：

ast_python_client.zip
未知大小


Java:

ast_java_client.zip
未知大小


交互流程 #
Image

WebSocket 二进制协议 #
WebSocket protobuf传输数据。

Event 字段描述 #
发送端 Event Type:

Event

取值

描述

StartSession

100

建联请求

UpdateConfig

201

更新参数

TaskRequest

200

发送音频数据

FinishSession

102

结束session

接收端 Event Type:

Type

取值

描述

SessionStarted

150

建联成功

SourceSubtitleStart

650

原文开始

SourceSubtitleResponse

651

原文数据

SourceSubtitleEnd

652

原文结束

TranslationSubtitleStart

653

译文开始

TranslationSubtitleResponse

654

译文数据

TranslationSubtitleEnd

655

译文结束

TTSSentenceStart

350

TTS开始

TTSResponse

352

TTS数据

TTSSentenceEnd

351

TTS结束

UsageResponse

154

计量计费

SessionFinished

152

会话正常结束

SessionFailed

153

会话失败

AudioMuted

250

静音事件


请求流程 #

发送端 #

建立连接-StartSession
根据 WebSocket 协议本身的机制，client 会发送 HTTP GET 请求和 server 建立连接做协议升级。
需要在其中根据身份认证协议加入鉴权签名头。设置方法请参考鉴权。
WebSocket 建立连接后，发送的第一个请求是 建联 request。请求体字段说明：

字段名

说明

层级

格式

是否必填

备注

request_meta

请求元信息

1

dict

✓

请求元信息

session_id

会话ID

2

string

✓

建议采用UUID

event

请求事件说明

1

enum(int32)

✓

建联请求的event 为100，见上文Event 字段描述

user

用户相关配置

1

dict

提供后可供服务端过滤日志

uid

用户标识

2

string

建议采用 IMEI 或 MAC。

did

设备名称

2

string

platform

操作系统及API版本号

2

string

iOS/Android/Linux

sdk_version

sdk版本

2

string

request

请求相关配置

1

dict

✓

请求配置说明

mode

模式

2

string

s2t/s2s 选一个, 控制是否需要语音

speaker_id

说话人音色

2

string

选择传入以下精品音色作为输出音频的说话人，不传或者传错则使用默认行为（复刻输入音频音色）
zh_female_vv_uranus_bigtts
zh_male_jingqiangkanye_emo_mars_bigtts

speech_rate

语速

2

number

取值范围[-50,100],100代表2.0倍速,-50代表0.5倍数

source_language

源语言

2

string

见下方：语种说明

target_language

目标语言

2

string

见下方：语种说明

corpus

语料/干预词等

2

dict

自定义词典，该object的所有配置字段（热词和术语）加和不超过1000个。超过则会报错。

hot_words_list

热词列表

3

[string]

原文字幕识别时使用的热词词库,用来指导模型，不一定干预生效（优先级高于传热词表）
示例：

["视频直播","赛事直播","智能家居"]
boosting_table_id

热词表ID

3

string

自学习平台上设置的热词词表ID
热词表功能和设置方法可以参考文档

boosting_table_name

热词表名

3

string

自学习平台上设置的热词词表名称
热词表功能和设置方法可以参考文档

correct_words

替换词

3

json string

原文和译文字幕识别时使用的替换词词库，（优先级高于传替换词表）
示例：

"{\"接受\":\"接收\",\"Accept\":\"Receive\"}"
regex_correct_table_id

替换词表ID

3

string

自学习平台上设置的替换词词表名称
替换词功能和设置方法可以参考文档

regex_correct_table_name

替换词表名

3

string

自学习平台上设置的替换词词表ID
替换词功能和设置方法可以参考文档

glossary_list

术语列表

3

dict{string: string}

原文翻译成译文时使用的术语词词库，用来指导模型，不一定干预生效（优先级高于传术语词表）
示例:

{"人工智能":"Machine Learning"}
glossary_table_id

术语词表ID

3

string

自学习平台上设置的术语词词表ID

glossary_table_name

术语词表名

3

string

自学习平台上设置的术语词词表名称

source_audio

源音频相关配置

1

dict

✓

源音频信息

format

音频容器格式

2

string

✓

wav，仅支持wav

codec

音频编码格式

2

string

raw， raw(表示pcm编码) 。 仅支持raw

rate

音频采样率

2

int

必须是16000

bits

音频采样点位数

2

int

必须是16

channel

音频声道数

2

int

1(mono) / 2(stereo)，当前仅支持单声道，必须传1

target_audio

目标音频相关配置

1

dict

s2s时必填，s2t时非必填

目标音频信息

format

音频容器格式

2

string

s2s时必填，s2t时非必填

pcm/ogg_opus

rate

音频采样率

2

int

s2s时必填，s2t时非必填

默认为 24000。支持16000/24000
注：
pcm 格式：16000Hz 采样率下默认 16 位整型（16bit），24000Hz 采样率下默认 32 位浮点型（32float）。
ogg_opus 格式：默认32 位浮点型（32float）且输出的采样率固定为48000，rate配置无法更改该格式的采样率；

参数示例：

Request中的request_meta.session_id为必填字段，不可缺省

{
  "request_meta": {
      "session_id": "xxxxxxxx-xxxxxxxxxx-xxxxxxx-xxxxxxxxxx"
  }
  "event": event.Type_StartSession,
  "user": {
    "uid": "388808088185088",
    "did": "xxxxxx"
  },
  "source_audio": {
    "format": "wav",
    "rate": 16000,
    "bits": 16,
    "channel": 1,
  },
  "target_audio": {
    "format": "pcm",
    "rate": 48000
  },
  "request": {
    "mode": "s2s",
    "speaker_id": "zh_female_vv_uranus_bigtts", //可选，不传或者传错则使用默认行为（复刻输入音频音色）
    "speech_rate": 0,
    "source_language": "zh",
    "target_language": "en",
    "corpus": {
      "hot_words_list": ["xxxxx","xxxxx"],//(优先级最高)
      "boosting_table_id":"", //热词表id(优先级其次)
      "boosting_table_name":"", //热词表名(优先级最后)
      "correct_words":"{\"xxx\":\"xxx\",\"xxx\":\"xxx\"}", //正则替换词json格式的map字符串(优先级最高)
      "regex_correct_table_id":"", //正则替换词表id(优先级其次)
      "regex_correct_table_name":"", //正则替换词表名(优先级最后)
      "glossary_list": {
          "xxxxx": "yyy",
          "zzzzz": "www",
      },//(优先级最高)
      "glossary_table_id":"",//术语词表id(优先级其次)
      "glossary_table_name":"",//术语词表名(优先级最后)
    }
  }
}
语种说明

语种集

语种集

语种数

语种清单

lang_8

8

中文、英文、德语、法语、西班牙语、印尼语、日语、葡萄牙语

lang_20

20

中文、英文、德语、法语、西班牙语、印尼语、日语、葡萄牙语、韩语、土耳其语、马来语、荷兰语、罗马尼亚语、波兰语、捷克语、阿拉伯语、泰语、越南语、俄语、意大利语

方言

2

粤语（yue-CN）、上海话（sh-CN）

模式与语种匹配/约束关系

输入/输出模式

语种设置特性及约束

支持语种

语音到文本（S2T）

源语种和目标语种必须指定
源语种 或 目标语种必须是中英
支持中英反转互译（zhen）
源语种：lang_20、方言
目标语种：lang_20
语音到语音（S2S）- 指定音色模式

传入 speaker_id，支持 2 个公版音色

zh_female_vv_uranus_bigtts
zh_male_jingqiangkanye_emo_mars_bigtts
源语种和目标语种必须指定
目标语种必须为中英
支持中英反转互译（zhen）
源语种：lang_20、方言
目标语种：中英
语音到语音（S2S）- 声音复刻模式

不传 speaker_id，自动复刻说话人声音

源语种和目标语种必须指定
源语种 或 目标语种必须是中英
支持中英反转互译（zhen）
源语种：lang_8
目标语种：lang_8
语种代号及说明

语言

参数值

说明

中文

zh

中英语种之一

英文

en

中英语种之一

德语

de

法语

fr

西班牙语

es

印尼语

id

日语

ja

葡萄牙语

pt

韩语

ko

土耳其语

tr

马来语

ms

荷兰语

nl

罗马尼亚语

ro

波兰语

pl

捷克语

cs

阿拉伯语

ar

泰语

th

越南语

vi

俄语

ru

意大利语

it

粤语

yue-CN

方言，仅支持作为源语种

上海话

sh-CN

方言，仅支持作为源语种

中英反转互译

zhen

source_language 和 target_language 需同时传 zhen

示例：你好，everyone 翻译为 Hello，大家

使用方式

source_language 和 target_language 均传上表中的参数值，例如中文传 zh，英文传 en。
mode=s2t 时返回文本结果，按“语音到文本（S2T）”的语种约束传参。
mode=s2s 且传入支持的 speaker_id 时，按“语音到语音（S2S）- 指定音色模式”的语种约束传参。
mode=s2s 且不传或传入不支持的 speaker_id 时，按“语音到语音（S2S）- 声音复刻模式”的语种约束传参。

发送音频数据-TaskRequest
Client 发送 建连请求后，再发送包含音频数据的 TaskRequest。音频应采用建立连接request 中指定的格式（音频格式、编解码器、采样率、声道）。二进制数据放在protobuf 的request体内部
例如在流式语音识别中如果每次发送 100ms 的音频数据，那么data中的 内容 就是 100ms 的音频数据。
注意：需要等到收到服务端响应的SessionStarted后再发参数包及音频包

具体的参数字段见下表：
字段

说明

层级

格式

是否必填

备注

event

请求事件说明

1

enum (int32)

✓

发送音频数据的的event 为200，见上文Event 字段描述

source_audio

源音频相关配置

1

dict

✓

源音频信息

data

音频数据

2

bytes

✓

音频流的二进制数据, 要求16khz,16bit,单通道wav/pcm, 建议80ms 一包

参数示例：

{
  "event": event.Type_TaskRequest,
  "source_audio": {
    "data": "ff\xa2\xfe*\xfeB\xfe\xa3\xfe\x9c\xff\xe2\x0"
  }
}

更新参数-ConfigUpdate
用于在session中更新语料/干预词等
参数示例：

{
  "event": event.Type_UpdateConfig,
  "request": {
    "mode": "s2s",     // 注意：当前不支持在会话中切换语言及mode，如需切换，请重新建立连接
    "corpus": {    // 用于在中间包修改热词和术语列表
      "hot_words_list": ["xxxxx","xxxxx"],//(优先级最高)
      "boosting_table_id":"", //热词表id(优先级其次)
      "boosting_table_name":"", //热词表名(优先级最后)
      "correct_words":"{\"xxx\":\"xxx\",\"xxx\":\"xxx\"}", //正则替换词json格式的map字符串(优先级最高)
      "regex_correct_table_id":"", //正则替换词表id(优先级其次)
      "regex_correct_table_name":"", //正则替换词表名(优先级最后)
      "glossary_list": {
          "xxxxx": "yyy",
          "zzzzz": "www",
      },//(优先级最高)
      "glossary_table_id":"",//术语词表id(优先级其次)
      "glossary_table_name":"",//术语词表名(优先级最后)
    }
  }
}

结束session-FinishSession
单独的结束事件，不带音频，在要发送的音频全部发送完毕后发送
参数示例：

{
  "event": event.FinishSession
}

服务端 #
Client 发送请求，服务端都会返回response。格式具体见protobuf定义，具体关键字段说明如下：

字段

说明

层级

格式

是否必填

备注

response_meta

响应元信息

1

dict

status_code

错误码

2

int

message

错误信息

2

string

billing

计量计费信息

2

dict

仅计量计费-UsageResponse event返回此字段

duration_msec

音频的持续时长

3

int

单位：毫秒

items

计量计费详情

3

array

unit

token分类

4

string

取值为：
output_text_tokens
output_audio_tokens
input_audio_tokens

quantity

消耗token量

4

float

event

响应事件

1

int

响应事件标志，例如建联成功（SessionStarted 取值为150）

text

整个音频的识别结果文本

1

string

原文或者译文

data

响应数据

1

raw

响应的二进制数据

start_time

起始时间（毫秒）

1

int

仅当识别成功时填写

end_time

结束时间（毫秒）

1

int

仅当识别成功时填写

spk_chg

说话人是否发生了切换的标志

1

bool

默认为false，在检测到说话人发生切换的那个句子的SourceSubtitleStart和TranslationSubtitleStart响应的响应体里会把此参数设置为true

muted_duration_ms

静音时间

int

单位ms, 表示静音了多久，存在误差，不是精确值


接收到建联成功-SessionStarted
响应示例：

{
  "event": event.Type_SessionStarted
}

原文开始-SourceSubtitleStart
标记原文开始发送，包含开始时间戳(startTime), 说话人切换信号(如开启相关功能)

{
  "event": event.Type_SourceSubtitleStart,
  "start_time": xxx,
  "spk_chg": false    //默认为false，如果检测到此句说话人发生切换，那么为true
}

原文数据-SourceSubtitleResponse
发送音频，要求16khz,16bit,单通道wav/pcm, 建议80ms一包

{
  "event": event.Type_SourceSubtitleResponse,
  "text": "xxx"   //原文文本
}

原文结束-SourceSubtitleEnd
{
  "event": event.Type_SourceSubtitleEnd,
  "start_time": xxx,
  "end_time": xxx,
  "text": "xxx"
}

译文开始-TranslationSubtitleStart
{
  "event": event.Type_TranslationSubtitleStart,
  "start_time": xxx,
  "spk_chg": false    //默认为false，如果检测到此句说话人发生切换，那么为true
}



---

**Cursor**

文档里有两个关键点需要再对齐：新版鉴权只需要 `X-Api-Key`，并且必须等收到 `SessionStarted` 后再发音频包。我先把当前实现按这两点收紧，避免握手通过后又在协议阶段失败。

我按你贴的文档继续修：当前代码还没等待 `SessionStarted` 就开始发音频，这违反了文档要求；同时新版鉴权头可以精简为 `X-Api-Key` + `X-Api-Resource-Id`。我会把这两个点改掉。

会话启动顺序已经按文档调整：发 `StartSession` 后会等待 `SessionStarted`，只有成功后才允许发音频。现在我跑检查。

已按你贴的“同声传译 2.0”文档继续修正：

- 新版鉴权头精简为 `X-Api-Key` + `X-Api-Resource-Id`。
- `StartSession` 后现在会等待服务端返回 `SessionStarted`。
- 只有收到 `SessionStarted` 后才开始发送 `TaskRequest` 音频包。
- 保持 Protobuf `TranslateRequest/TranslateResponse` 通信方式。

检查已通过：`src/core/volc_engine.py` 语法检查和 lint 都没问题。

你现在需要确保设置里填的是火山新版控制台的 `API Key`，不是旧的 `App ID`。然后运行：

```bash
python -m pip install -r requirements.txt
python run.py
```

如果下一次还有报错，把 `SessionFailed` 后面的 message 或最新日志贴出来，我再按服务端返回继续对齐。

---

**User**

| ✅ 火山引擎管道已启动（端到端语音翻译）
11:01:01 | INFO     | 火山引擎管道启动
11:01:01 | ERROR    | 火山引擎连接失败: 401, message='Invalid response status', url='wss://openspeech.bytedance.com/api/v4/ast/v2/translate'
11:01:01 | ERROR    | 火山引擎连接失败，回退本地模式


---

**Cursor**

401 是鉴权失败，不是接口地址问题。结合你之前填过 `App ID + Access Token`，当前代码把数字 `App ID` 当成新版 `API Key` 发出去了，所以会被拒；我把鉴权逻辑改成同时兼容新版 API Key 和旧版 App ID/Access Token。

鉴权兼容逻辑已改：如果有 `App ID + Access Token` 就走旧版请求头；如果只有 API Key 就走新版请求头。现在我做一次快速检查。

已修复 401 的主要兼容问题：现在火山鉴权会自动判断两种模式。

- 如果填了 `API Key`，走新版：`X-Api-Key`
- 如果填了 `App ID + Access Token`，走旧版：`X-Api-App-Id` + `X-Api-Access-Key`

检查已通过。

你重新运行后看日志里会出现：

```text
火山引擎鉴权模式: 新版 API Key
```

或：

```text
火山引擎鉴权模式: 旧版 App ID + Access Token
```

如果仍然 401，请确认当前设置界面里填的是以下二选一：

新版：`API Key` 填火山控制台 API Key，`兼容 Token` 留空。  
旧版：`API Key` 那格填数字 `App ID`，`兼容 Token` 填 `Access Token`。

---

**User**

火山引擎鉴权模式: 旧版 App ID + Access Token
11:05:06 | ERROR    | 火山引擎连接失败: 403, message='Invalid response status', url='wss://openspeech.bytedance.com/api/v4/ast/v2/translate'
11:05:06 | ERROR    | 火山引擎连接失败，回退本地模式
还是不行

---

**Cursor**

从日志看它确实走的是旧版 `App ID + Access Token`，但这个服务现在很可能没有给你的旧凭证授权 AST 2.0。为了避免 UI 里残留的 Token 让程序误判，我先把鉴权判断改得更明确：只有第一格是纯数字 App ID 且第二格有 Token 时才走旧版；否则第一格一律按新版 API Key。

这个改动能避免把新版 API Key 加上残留 Token 误判成旧版。接下来我再检查代码没问题，然后给你明确下一步该怎么填。

这次日志已经说明问题点：程序走的是“旧版 App ID + Access Token”，但火山 AST 2.0 对你的这组旧凭证返回了 `403`，一般表示凭证本身有效但没有这个资源/服务权限，或旧控制台凭证不能调用该 AST 2.0 接口。

我刚刚加了一个判断修正：只有第一格是纯数字 App ID 且第二格有 Token 时才走旧版；否则第一格会按新版 `API Key` 走。

你现在建议这样处理：

1. 去火山控制台拿“API Key”，不是 App ID。
2. 设置里火山第一格填这个 `API Key`。
3. 第二格“兼容 Token”清空。
4. 保存后重启运行。

如果日志变成：

```text
火山引擎鉴权模式: 新版 API Key
```

才说明走对了新版文档里的鉴权方式。若新版 API Key 仍 401/403，那就是控制台里 AST 2.0 服务或 `volc.service_type.10053` 权限没开通。

---

**User**

我就是旧版的id+token

---

**Cursor**

明白，那就不要切新版 API Key。旧版鉴权文档里字段名有个坑：表格写 `X-Api-App-Id`，建连示例又写 `X-Api-App-Key`。我把连接逻辑改成旧版凭证自动尝试两种 header 变体，这样不用你反复改配置。

我会把 `_build_headers` 改成生成多个候选，并让 `connect()` 在旧版模式下先试 `X-Api-App-Id`，失败 401/403 后再试 `X-Api-App-Key`，同时保留最终错误日志。

我开始改连接候选逻辑。

旧版两种 header 变体已经加上。现在检查语法和 lint。

明白，你就是旧版 `App ID + Access Token`。我已经把代码改成旧版自动尝试两种鉴权头：

- 先试文档表格里的 `X-Api-App-Id`
- 如果失败，再试建连示例里类似的 `X-Api-App-Key`

语法和 lint 都通过了。

你重新运行后看日志，应该会出现类似：

```text
尝试火山引擎鉴权头: 旧版 App-Id
...
尝试火山引擎鉴权头: 旧版 App-Key
```

如果两种都还是 403，那就基本不是代码字段名问题了，而是这组旧版凭证没有 `volc.service_type.10053` 的 AST 2.0 调用权限，需要在火山控制台确认“同声传译 2.0/AST”服务是否对这个 App ID 开通。

---

**User**

ERROR    | 火山引擎连接失败: 'ClientWebSocketResponse' object has no attribute 'headers'
11:13:17 | ERROR    | 火山引擎连接失败，回退本地模式


---

**Cursor**

这个报错其实是好消息：WebSocket 已经握手成功了，只是在读取 `X-Tt-Logid` 时用了 `aiohttp` 不存在的 `headers` 属性，导致被误判为连接失败。我马上把 logid 读取改成兼容写法。

已把 `X-Tt-Logid` 读取改成从 `aiohttp` 的底层响应对象取，不会再因为日志字段导致连接失败。现在我做一次检查。

修好了。这个错误说明火山握手其实已经成功了，只是代码读取 `X-Tt-Logid` 的方式不兼容 `aiohttp`：

```text
'ClientWebSocketResponse' object has no attribute 'headers'
```

我已改成从底层响应对象读取，不会再把成功连接误判为失败。语法检查和 lint 都通过。

你再运行一次，下一步应该会进入 `StartSession` / `SessionStarted` 阶段。如果还有报错，把后面的 `SessionFailed` message 或最新日志贴出来。

---

**User**

怎么捕获系统输出的音频？

---

**Cursor**

在 Linux/PipeWire/PulseAudio 下，捕获“系统输出声音”一般用 **monitor 源**。

你的项目已经会创建一个虚拟输出：

```text
translator_virtual_sink
translator_virtual_sink.monitor
```

用法是：

1. 打开系统声音设置。
2. 把游戏/浏览器/Discord 的输出设备切到 `translator_virtual_sink`。
3. 在工具里打开“设置 -> 音频设备”。
4. “游戏声音捕获”选择：

```text
translator_virtual_sink.monitor
```

这样音频流是：

```text
游戏/系统声音 -> translator_virtual_sink -> translator_virtual_sink.monitor -> 翻译工具捕获
```

如果你想捕获当前真实扬声器输出，也可以在终端看 monitor 设备：

```bash
pactl list short sources
```

通常会看到类似：

```text
alsa_output.xxx.monitor
```

然后在“游戏声音捕获”里选对应的 `monitor` 源。

如果下拉列表没有 monitor 设备，先确认安装了：

```bash
sudo apt install pulseaudio-utils
```

然后重启程序，进入“设置 -> 音频设备”，点“刷新设备列表”。

---

**User**

加载配置失败，使用默认配置: 1 validation error for AppConfig
audio.game_output_device
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='translator_virtual_sink.monitor', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing
/home/fiend/.local/lib/python3.12/site-packages/pydantic/main.py:475: UserWarning: Pydantic serializer warnings:
  PydanticSerializationUnexpectedValue(Expected `int` - serialized value may not be as expected [field_name='game_output_device', input_value='translator_virtual_sink.monitor', input_type=str])
  return self.__pydantic_serializer__.to_python(


---

**Cursor**

这是配置类型太窄导致的：音频设备以前只允许整数 ID，但 monitor 源是字符串设备名。我会把音频配置里的设备字段改成同时支持 `int` 和 `str`，这样 `translator_virtual_sink.monitor` 能正常保存和读取。

我会把配置类型和 TTS 输出设备类型一起放宽为 `int | str | None`，因为 PipeWire/PulseAudio 的设备名本来就是字符串。

配置读取已经支持字符串设备名了。我再把 TTS 引擎的类型标注同步调整，避免编辑器继续警告。

现在我跑语法和 lint 检查，并顺手验证配置文件能加载字符串 monitor 源。

已修复。现在音频设备配置支持字符串设备名了，例如：

```yaml
game_output_device: translator_virtual_sink.monitor
```

改动包括：

- `src/utils/config.py`：`input_device`、`output_device`、`game_output_device` 支持 `int | str | None`
- `src/core/tts_engine.py`：TTS 输出设备也支持字符串设备名

已验证配置能正常加载 `translator_virtual_sink.monitor`，语法和 lint 都通过。重新运行程序即可。

---

**User**

| WARNING  | 游戏声音捕获设备打开失败: No input device matching 'translator_virtual_sink.monitor'


---

**Cursor**

这个警告是因为 `AudioStream` 目前只把 `alsa_...` 字符串当作 PulseAudio/PipeWire 设备名处理，`translator_virtual_sink.monitor` 被直接交给 PortAudio 匹配，所以找不到。我会把字符串设备名统一走 `PULSE_SOURCE/PULSE_SINK` 路由。

我这边 sandbox 里连不上你的桌面音频服务，但代码问题已经明确：字符串设备名应该通过 PulseAudio 环境变量路由。我现在改 `open_input/open_output`。

字符串设备名现在会走 `PULSE_SOURCE`/`PULSE_SINK`，不会再让 PortAudio 按设备名硬匹配。接下来做语法和 lint 检查。

已修复。现在 `translator_virtual_sink.monitor` 这类字符串设备名会通过：

```text
PULSE_SOURCE=translator_virtual_sink.monitor
```

交给 PulseAudio/PipeWire 路由，不会再被 PortAudio 当作普通设备名匹配，所以应能解决：

```text
No input device matching 'translator_virtual_sink.monitor'
```

改动在 `src/audio/stream.py`，语法和 lint 都通过。重新运行程序再试一下。

---

**User**

没反应？我后台一直在播放视频

---

**Cursor**

“后台视频没反应”这里有两个常见原因：一是视频声音没有路由到 `translator_virtual_sink`，二是当前火山模式代码只启动了麦克风出站管道，没有启动游戏/系统音频入站管道。我先检查并补上第二个代码问题。

我确认到代码问题了：当前只要火山可用，`start()` 只创建 `_volc_outbound_loop()`，不会启动 `_inbound_loop()`，所以你播放系统/游戏声音也不会进入入站字幕管道。我会加一个独立的火山入站引擎，专门处理 `game_output_device` 捕获到的系统音频。

火山引擎现在支持 `s2s` 和 `s2t` 模式了；入站系统音频会用 `s2t`，只拿字幕文本，约束更少。接下来改管道启动逻辑。

启动逻辑已经会在配置了游戏声音捕获设备时，同时启动火山入站任务。现在补入站循环本身。

入站火山字幕管道已经补上。接下来跑语法和 lint；之后你还需要确认视频声音确实输出到了 `translator_virtual_sink`。

确实会“没反应”：之前火山模式只启动了麦克风出站管道，没有启动系统/游戏音频入站管道。我已经补上了：

- 火山出站：麦克风 -> 外语
- 火山入站：系统/游戏声音 -> 中文字幕
- 入站使用 `s2t` 模式，更适合字幕，不要求 TTS 音频输出

检查已通过。

你还需要确认视频声音路由正确：后台视频必须输出到 `translator_virtual_sink`，工具捕获 `translator_virtual_sink.monitor` 才能收到声音。简单说：

```text
视频/浏览器输出设备 = translator_virtual_sink
工具游戏声音捕获 = translator_virtual_sink.monitor
```

重新运行后日志里应看到类似：

```text
火山引擎双向管道已启动（出站 + 系统音频入站）
火山引擎入站管道启动（系统音频→字幕）
```

如果还是没字幕，把这两行附近的日志贴出来。

---

**User**

translator_intime  python run.py
11:20:01 | INFO     | 日志系统初始化完成
11:20:01 | INFO     | ============================================================
11:20:01 | INFO     | Translator InTime v0.1.0 启动
11:20:01 | INFO     | 实时游戏语音双向翻译系统
11:20:01 | INFO     | ============================================================
11:20:02 | INFO     | GUI 已启动
11:20:03 | INFO     | 翻译策略: microsoft (降级: google)
11:20:03 | INFO     | 初始化翻译管道...
11:20:03 | INFO     | 虚拟音频设备 'translator_virtual' 已存在
11:20:03 | INFO     | Monitor 源自动可用: translator_virtual_sink.monitor
11:20:03 | INFO     | 下载/加载 Whisper 模型（首次需下载 ~140MB）...
11:20:03 | INFO     | 加载 Whisper 模型: faster-whisper-small, device=cpu
11:20:03 | INFO     |   尝试本地: /home/fiend/WorkSpace/python/translator_intime/models/systran/faster-whisper-small (compute=int8)
11:20:04 | INFO     | Whisper 模型加载成功 ✓
11:20:04 | INFO     | 加载 Whisper 模型: faster-whisper-small, device=cpu
11:20:04 | INFO     |   尝试本地: /home/fiend/WorkSpace/python/translator_intime/models/systran/faster-whisper-small (compute=int8)
11:20:06 | INFO     | Whisper 模型加载成功 ✓
11:20:06 | INFO     | TTS输出: 系统默认扬声器
11:20:06 | INFO     | 翻译管道初始化完成
11:20:06 | INFO     | 音频输入流已打开 (device=None, sr=16000)
11:20:06 | INFO     | 音频输入: PulseAudio 设备 translator_virtual_sink.monitor
11:20:06 | INFO     | 音频输入流已打开 (device=translator_virtual_sink.monitor, sr=16000)
11:20:06 | INFO     | 游戏声音捕获已开启
11:20:06 | INFO     | ✅ 双向翻译管道已启动（出站 + 入站）
11:20:06 | INFO     | 出站管道(中->外) 启动
11:20:06 | INFO     | 入站管道(外->中) 启动
这就是所有日志了，并没有捕获到音频？

---

**Cursor**

从这段日志看，当前其实没有走火山，而是走本地 `microsoft + Whisper` 双向管道；入站管道确实启动了，但我们还不知道它读到的是静音还是视频没路由进 monitor。我先加一个轻量级音量日志，确认 `translator_virtual_sink.monitor` 实际有没有音频能量。

这里还看到你的翻译策略是 `microsoft`，说明后端不是 `volc`，所以系统音频走的是本地 Whisper 入站识别，不是火山入站。我先给本地入站加音量日志，判断是“没声音进来”还是“有声音但 ASR 没出文字”。

我会在入站循环里每秒打印一次 RMS 音量，比如 `系统音频输入电平 rms=...`。如果一直是 `0.0000`，就是视频声音没有进 `translator_virtual_sink.monitor`；如果有数值但没字幕，再看 ASR/语言配置。

音量日志已加。接下来检查一下语法和 lint。

从日志看，入站管道确实启动了，但当前后端是 `microsoft`，不是火山：

```text
翻译策略: microsoft (降级: google)
✅ 双向翻译管道已启动（出站 + 入站）
入站管道(外->中) 启动
```

我加了一个每秒音量日志，重启后你会看到：

```text
系统音频输入电平 rms=0.00000
```

判断方法：

- 如果一直接近 `0.00000`：视频声音没有进入 `translator_virtual_sink.monitor`。需要把浏览器/播放器的输出设备切到 `translator_virtual_sink`。
- 如果有明显数值，比如 `0.01`、`0.03`：已经捕获到声音，问题在 ASR 语言/静音阈值/识别触发。

你先重启跑一次，看 `系统音频输入电平 rms=...` 是多少。  
另外，如果你要走火山实时字幕，需要在翻译设置里把后端选成 `volc`，现在日志显示还在用 `microsoft`。

---

**User**

我切换了列表中的所有设备，可是电平都是0，但我确定我一直在播放视频，视频音量也是开到了最大

---

**Cursor**

如果所有设备电平都是 0，代码层面还有一个更可能的问题：`sounddevice` 并不一定会按 `PULSE_SOURCE` 打开指定 monitor 源，它可能还是打开了默认输入。更稳的方式是：字符串形式的 Pulse/PipeWire source 直接用 `parec` 按设备名读取。我会把字符串输入设备改成 `parec` 捕获，并把 monitor 源显示进设备列表。

我会把字符串输入设备从 `sounddevice` 改成直接调用 `parec --device=...` 读取原始 PCM。这样不会依赖 PortAudio 是否尊重 `PULSE_SOURCE`，能真正从 `translator_virtual_sink.monitor` 拉音频。

`parec` 直连捕获已经加上，monitor 源也会出现在输入设备列表里。接下来做检查。

我修了一个关键点：之前字符串设备名还是通过 `sounddevice` 间接打开，很多 Linux 环境下它不会真的切到 `translator_virtual_sink.monitor`，所以读到的可能一直是默认输入的静音。

现在改成了：

- 字符串输入设备直接用 `parec --device=translator_virtual_sink.monitor` 读取 PCM。
- monitor 源会显示在设备列表里。
- 电平日志可以真正反映 monitor 源有没有声音。

你需要安装/确认有 `parec`：

```bash
sudo apt install pulseaudio-utils
```

然后重启程序，再播放视频。看日志里的：

```text
系统音频输入电平 rms=...
```

如果还是 `0.00000`，那就不是代码没捕获，而是视频声音没有输出到 `translator_virtual_sink`。如果出现非零电平，就说明系统音频已经进来了。

---

**User**

10 | INFO     | 系统音频输入电平 rms=0.00000
11:30:11 | INFO     | 系统音频输入电平 rms=0.00000
11:30:12 | INFO     | 系统音频输入电平 rms=0.00000
11:30:13 | INFO     | 系统音频输入电平 rms=0.00000
11:30:14 | INFO     | 系统音频输入电平 rms=0.00000
11:30:15 | INFO     | 系统音频输入电平 rms=0.00000
11:30:16 | INFO     | 系统音频输入电平 rms=0.00000
11:30:17 | INFO     | 系统音频输入电平 rms=0.00000
11:30:18 | INFO     | 系统音频输入电平 rms=0.00000
11:30:19 | INFO     | 系统音频输入电平 rms=0.00000
11:30:20 | INFO     | 系统音频输入电平 rms=0.00000
11:30:21 | INFO     | 系统音频输入电平 rms=0.00000
11:30:22 | INFO     | 系统音频输入电平 rms=0.00000
11:30:23 | INFO     | 系统音频输入电平 rms=0.00000
依旧没有，需要我帮你在系统中测试什么来确认问题吗？

---

**Cursor**

需要，我们现在要确认两件事：视频音频实际在哪个 sink 上播放，以及 `translator_virtual_sink.monitor` 用 `parec` 直接录出来是不是静音。我会给你几条命令；同时我也会加一个小工具命令方便在项目里做音频电平测试。

我会加一个 `scripts/check_pulse_audio.py`，它会列出 sink/source/sink-input，并用 `parec` 直接测指定 source 的 RMS。你运行它的输出能直接告诉我们视频到底有没有路由到目标 sink。

检查脚本已添加。现在我做语法检查。

可以，需要你在本机终端里测一下音频路由。我加了一个诊断脚本：

```bash
python scripts/check_pulse_audio.py
```

请保持视频播放，然后把输出贴给我。重点看三段：

- `Sinks 输出设备`：有没有 `translator_virtual_sink`
- `Sources 输入/Monitor`：有没有 `translator_virtual_sink.monitor`
- `Sink Inputs 正在播放的应用`：视频/浏览器当前到底输出到哪个 sink

最后脚本会直接测：

```text
translator_virtual_sink.monitor
rms=...
peak=...
```

判断：

- 如果 `sink-inputs` 里视频不在 `translator_virtual_sink`，说明视频没有路由到虚拟输出。
- 如果视频在 `translator_virtual_sink`，但 `rms/peak` 还是 0，说明 monitor/source 创建或 capture 有问题。
- 如果 `rms/peak` 非 0，说明捕获正常，问题就在翻译管道后续处理。

你也可以顺手运行这个看默认输出设备：

```bash
pactl get-default-sink
```

把这些输出发我，我就能定位下一步。

---

**User**

python scripts/check_pulse_audio.py

== Sinks 输出设备 ==
83	translator_virtual_sink	PipeWire	float32le 2ch 48000Hz	RUNNING
901	alsa_output.pci-0000_00_1f.3.hdmi-stereo	PipeWire	s32le 2ch 48000Hz	SUSPENDED

== Sources 输入/Monitor ==
83	translator_virtual_sink.monitor	PipeWire	float32le 2ch 48000Hz	RUNNING
901	alsa_output.pci-0000_00_1f.3.hdmi-stereo.monitor	PipeWire	s32le 2ch 48000Hz	SUSPENDED
902	alsa_input.pci-0000_00_1f.3.analog-stereo.3	PipeWire	s32le 2ch 48000Hz	SUSPENDED

== Sink Inputs 正在播放的应用 ==
1410	83	1409	PipeWire	float32le 2ch 48000Hz

== 测量 translator_virtual_sink.monitor ==
请保持视频播放，下面每 0.5 秒输出一次 RMS。
rms=0.11006 peak=0.75174
rms=0.10943 peak=0.48712
rms=0.05893 peak=0.33575
rms=0.06888 peak=0.53647
rms=0.00576 peak=0.07126
rms=0.11528 peak=0.52301
rms=0.06370 peak=0.37796
rms=0.03812 peak=0.32455
rms=0.03238 peak=0.27170
(venv) [10.6.3.58]☁  translator_intime  pactl get-default-sink
translator_virtual_sink


---

**Cursor**

诊断结果很好：系统路由没问题，`translator_virtual_sink.monitor` 用 `parec` 能测到明显音量。现在问题大概率是程序里保存的是 PortAudio 数字设备 `83`，导致走了 `sounddevice` 路径而不是 `parec` 路径。我会让代码识别到数字设备对应的是 `.monitor` 时，自动转成 source 名称用 `parec` 捕获。

我已加自动识别逻辑。现在做语法和 lint 检查，确认没有缩进或类型问题。

你的诊断输出说明系统音频路由是正常的：

```text
Sink Inputs 正在播放的应用 -> sink 83
83 translator_virtual_sink RUNNING
translator_virtual_sink.monitor rms=0.11006
```

所以不是系统问题，是程序捕获路径问题。很可能设置里保存的是数字设备 `83`，程序用 `sounddevice` 打开它时读到静音；而脚本用 `parec --device=translator_virtual_sink.monitor` 能正常读到声音。

我已修复：如果输入设备 ID 对应的名称是 `.monitor`，程序会自动改用 `parec` 按 source 名称捕获。

重启程序后日志应该出现类似：

```text
输入设备 ID 83 是 monitor 源，改用 parec 捕获: translator_virtual_sink.monitor
PulseAudio输入流已打开 (source=translator_virtual_sink.monitor, sr=16000)
系统音频输入电平 rms=0.0xxxx
```

如果你之前手动填的是字符串 `translator_virtual_sink.monitor`，也会直接走 `parec`。现在再试一次，电平应该不再是 0。

---

**User**

-> 感谢您的观看


[EN] Thank you for watching
-> 谢谢观看


[EN] Thanks for watching
-> 谢谢


-> 感谢您的观看
？？？？？？不是接入了火山吗？怎么识别还是一团糟

---

**Cursor**

你这个输出不是火山在识别，还是本地 Whisper 在识别，然后用 Microsoft 翻译；之前日志里已经显示 `翻译策略: microsoft` 和 `入站管道(外->中) 启动`。我先检查当前配置和策略代码，确认为什么没有进入火山管道。

现在日志已经证明捕获修好了：`rms=0.10489`，但识别仍是本地 Whisper，因为日志明确是 `翻译策略: microsoft`、`ASR: Thank you for watching`。我会把 `backend=volc` 的配置路径修正成真正启用火山管道，并避免在火山模式下还走本地 Whisper。

原因很明确：你的最新日志里没有任何“火山引擎管道启动/鉴权模式”，只有 `翻译策略: microsoft`，说明程序启动时没有读到火山凭证，所以走了本地 Whisper。现在我把火山启用条件改成也读取环境变量，并且 `backend=volc` 时强制创建火山管道。

火山管道现在会读取 `VOLC_APP_ID/VOLC_ACCESS_TOKEN/VOLC_APP_KEY/VOLC_API_KEY` 环境变量，也会在后端选择 `volc` 时创建。接着我修翻译策略日志，避免 `volc` 被当成未知后端。

现在策略日志会正确显示 `volc`，不会再默默落到 `microsoft`。我再加一条初始化日志，启动时能看出火山是否被请求和是否读到凭证。

我跑一下语法和 lint，确认这些策略改动没问题。

你说得对，刚才那些结果不是火山，还是本地 Whisper 幻觉。日志里已经暴露了：

```text
翻译策略: microsoft
ASR: Thank you for watching
[Microsoft] ...
```

所以“识别一团糟”的源头是本地 `faster-whisper-small`，不是火山 AST。

我已修正两处：

- `backend=volc` 现在会被识别为火山后端，不会当未知后端降级到 `microsoft`。
- 火山管道现在也会读取环境变量里的旧版/新版凭证：`VOLC_APP_ID`、`VOLC_ACCESS_TOKEN`、`VOLC_APP_KEY`、`VOLC_API_KEY`。

重启后请看日志，正确进入火山应该出现这些：

```text
火山引擎配置: requested=True, configured=True
翻译后端: volc
✅ 火山引擎双向管道已启动（出站 + 系统音频入站）
火山引擎入站管道启动（系统音频→字幕）
```

如果还看到：

```text
翻译策略: microsoft
入站管道(外->中) 启动
ASR: ...
```

那就是设置里后端还不是 `volc`，或火山凭证没有保存/没有加载。

---

**User**

translator_intime  python run.py
11:35:23 | INFO     | 日志系统初始化完成
11:35:23 | INFO     | ============================================================
11:35:23 | INFO     | Translator InTime v0.1.0 启动
11:35:23 | INFO     | 实时游戏语音双向翻译系统
11:35:23 | INFO     | ============================================================
11:35:23 | INFO     | GUI 已启动
11:35:26 | INFO     | 翻译策略: microsoft (降级: google)
11:35:26 | INFO     | 初始化翻译管道...
11:35:26 | INFO     | 虚拟音频设备 'translator_virtual' 已存在
11:35:26 | INFO     | Monitor 源自动可用: translator_virtual_sink.monitor
11:35:26 | INFO     | 下载/加载 Whisper 模型（首次需下载 ~140MB）...
11:35:26 | INFO     | 加载 Whisper 模型: faster-whisper-small, device=cpu
11:35:26 | INFO     |   尝试本地: /home/fiend/WorkSpace/python/translator_intime/models/systran/faster-whisper-small (compute=int8)
11:35:27 | INFO     | Whisper 模型加载成功 ✓
11:35:27 | INFO     | 加载 Whisper 模型: faster-whisper-small, device=cpu
11:35:27 | INFO     |   尝试本地: /home/fiend/WorkSpace/python/translator_intime/models/systran/faster-whisper-small (compute=int8)
11:35:28 | INFO     | Whisper 模型加载成功 ✓
11:35:28 | INFO     | TTS输出: 系统默认扬声器
11:35:28 | INFO     | 翻译管道初始化完成
11:35:28 | INFO     | 音频输入流已打开 (device=None, sr=16000)
11:35:28 | INFO     | PulseAudio输入流已打开 (source=translator_virtual_sink.monitor, sr=16000)
11:35:28 | INFO     | 游戏声音捕获已开启
11:35:28 | INFO     | ✅ 双向翻译管道已启动（出站 + 入站）
11:35:28 | INFO     | 出站管道(中->外) 启动
11:35:28 | INFO     | 入站管道(外->中) 启动
为什么还是本地模型？

---

**Cursor**

这段日志里没有我刚加的 `火山引擎配置: ...`，说明启动时没有进入新的火山初始化分支。我要先直接看当前配置文件里 `backend` 和火山凭证到底保存成什么了。

当前配置其实有火山 Key，但 `backend` 还是 `auto`。按现在代码，`auto + volc_app_id` 应该会优先 `volc`，所以你那段 11:35 日志应该是改动前的代码在运行，或者运行的进程没重启到最新代码。我先本地验证当前代码的策略输出。

系统解释器被项目包初始化里的 `faster_whisper` 卡住了，我绕开包初始化直接加载 `translator.py` 验证策略。

我直接把当前配置里的 `translation.backend` 从 `auto` 改成 `volc`，这样不会再被自动策略带去 `microsoft`。同时我会让火山模式跳过本地 Whisper 模型加载，避免日志看起来还在用本地模型。

配置已强制切到 `volc`。接下来我改初始化逻辑：火山可用时不加载本地 Whisper，只有本地模式才加载。

我还需要补一下火山连接失败时的本地回退：如果火山失败再走本地，先加载 Whisper。

现在火山模式不会先加载本地模型，只有火山失败回退本地时才会加载。接下来做语法和 lint 检查。

原因是配置仍然是：

```yaml
translation:
  backend: auto
```

所以它按自动策略走到了 `microsoft + 本地 Whisper`，不是火山。我已经把配置改成：

```yaml
translation:
  backend: volc
```

并且改了初始化逻辑：火山可用时不再加载本地 Whisper，只有火山连接失败回退时才会加载。

你现在重启后，正确日志应该是：

```text
翻译后端: volc
火山引擎配置: requested=True, configured=True
火山引擎可用，跳过本地 Whisper 模型加载
✅ 火山引擎双向管道已启动...
```

另外我看到当前配置里：

```yaml
volc_app_id: 28b3c04b-01c4-43e3-874b-251cf788b4b1
volc_access_token: ''
```

这更像新版 `API Key`，不是旧版“数字 App ID + Token”。如果你确实要用旧版，需要把 `volc_app_id` 填数字 App ID，并把 `volc_access_token` 填 Access Token。

---

**User**


-> 模式


-> ：


[EN] of
[EN] parental
[EN] angu
[EN] ish
[EN] .
-> 为人


-> 父母


-> 的


-> 焦虑


-> 。这个输出是不是观看起来有点费劲呢？

---

**Cursor**

是的，这样按碎片逐条刷屏会很难看。问题不是翻译内容本身，而是流式字幕事件被直接 append 到界面了；我先看一下当前字幕缓冲和显示逻辑，然后把入站/出站结果合并成更自然的句子级显示。

对，这种输出确实费劲，而且根因是我们把火山流式 `Response` 片段直接显示了。按文档应该用 `SourceSubtitleEnd` / `TranslationSubtitleEnd` 的完整句子来上屏；我会改成内部累积片段，只在句子结束事件时显示。

我会把 `VolcASREngine` 改成：`Start` 重置缓冲，`Response` 只累积，`End` 才回调 UI。这样不会再出现 `of / parental / angu / ish` 这种碎片逐行显示。

片段合并逻辑已加。现在我检查语法和 lint。

是的，之前这样显示很费劲。火山返回的是流式片段，我们之前把 `Response` 片段直接上屏了，所以才会出现：

```text
[EN] of
[EN] parental
[EN] angu
[EN] ish
```

我已改成按火山文档的事件语义处理：

- `SourceSubtitleResponse` / `TranslationSubtitleResponse`：只在内部累积
- `SourceSubtitleEnd` / `TranslationSubtitleEnd`：句子结束后再显示完整文本

这样字幕会更接近一句一句显示，不会碎片刷屏。语法和 lint 都通过了。重启后再试。

---

**User**

对比一下我目前的项目实现和sayhey的实现相比有哪些不足！

---

**Cursor**

我会从实现层面对比当前项目和 `sayhey`，重点看火山接入、音频路由、字幕聚合、配置和稳定性差异，而不是只看表面功能。先读取一下用于展示分析结果的指南，再让只读探索并行梳理两个项目。

我已经拿到两个项目的只读梳理结果。这个对比属于架构/实现评审，我会整理成一个可打开的 Canvas 视图，同时在聊天里给你最重要的结论和优先级。

我会用 Canvas 把差异按模块和优先级可视化，避免在聊天里堆很长的表格。

我会把对比沉淀成一个 Canvas 文件，你可以在旁边打开看完整分项；聊天里我会给压缩版结论。

我整理了一个可打开的对比 Canvas：[sayhey-comparison](/home/fiend/.cursor/projects/home-fiend-WorkSpace-python-translator-intime/canvases/sayhey-comparison.canvas.tsx)。这是一个独立的可视化分析视图，适合这种模块级对比。

核心结论：你当前项目已经把 Linux/PipeWire 音频捕获和火山 AST 主链路跑起来了，但整体还偏“调通原型”；`sayhey` 更像一个完整产品。

当前项目主要不足：

- 火山 Protobuf 代码没有纳入本项目，目前还依赖上一级 `sayhey/SayHey/python_protogen`，独立运行和打包不稳。
- 火山 `s2s` 的 `TTSResponse` 还没接到播放输出，所以端到端语音输出链路不完整。
- 字幕 UI 还只是主窗口文本流，缺少 `sayhey` 那种 Overlay、定时 flush、断句优化和位置/样式配置。
- 音频设备鲁棒性不如 `sayhey`，没有完整的设备 fallback、啸叫检测、输出队列、路由校验。
- 配置和密钥管理比较混乱，`volc_app_id` 现在同时承担 API Key/App ID 语义，而且密钥写进 YAML 不安全。
- 稳定性缺口明显：火山断线不重连、`SessionFailed` 用户提示弱、usage 没记录、测试覆盖基本没有。

当前项目也有优势：Linux 音频这块更贴你的环境，`parec` 捕获、`pactl` 枚举、RMS 诊断脚本已经比 `sayhey` 更适合 PipeWire/PulseAudio。下一步最值得做的是：先把 Protobuf 依赖内置，再拆分火山 S2S/S2T 引擎，随后移植 `sayhey` 的 Overlay 和字幕缓冲。

---

**User**

如果要和sayhey对齐工作量大不大

---

**Cursor**

不算小。按当前状态，如果目标是“核心体验接近 SayHey”，大概是 **中等偏大工作量**；如果目标是“完整产品化对齐”，就是 **大工作量**。

粗略估算：

- **最小可用对齐：1-2 天**  
  把 Protobuf 生成代码纳入本项目、修火山 S2T/S2S 分离、完善字幕句级显示、接上火山 TTS 音频播放。

- **体验接近 SayHey：3-5 天**  
  加 Overlay 字幕窗口、QTimer 字幕缓冲、热词/术语注入、音频路由自检、断线重连、错误提示。

- **产品化接近 SayHey：1-2 周**  
  设备 fallback、啸叫检测、配置迁移、密钥安全、用量统计、测试覆盖、跨平台音频适配、完整设置界面。

我建议别一次性“全量对齐”。最划算路线是：

1. 先把火山链路做稳：Protobuf 内置、S2T/S2S 拆类、TTSResponse 播放。
2. 再把字幕体验做好：句级聚合、去重、Overlay。
3. 最后补产品化：配置、热词、设备检测、重连、测试。

这样第一阶段完成后，实际可用性就会明显提升。

---

**User**

我最近发现阿里新发布了一个模型，叫Fun-ASR-Nano，试试用这个模型替代whisper

---

**Cursor**

我先确认当前 ASR 封装方式和依赖，再查一下 `Fun-ASR-Nano` 的实际包名/调用 API，避免按名字猜错模型接入方式。

我会把它做成 ASR 后端选项，而不是硬删 Whisper：默认切到 Fun-ASR-Nano，但保留 faster-whisper 作为回退。这样如果 FunASR 依赖或模型下载有问题，项目还能跑。

我会做三处改动：配置里新增 `asr.backend`，默认设为 `funasr`；`ASREngine` 同时支持 `funasr` 和 `whisper`；设置界面增加 ASR 后端选择。FunASR 会先用 `FunAudioLLM/Fun-ASR-Nano-2512`，失败时可切回 Whisper。

配置字段已加并默认切到 FunASR。接下来改 ASR 引擎实现。

ASR 引擎已支持 FunASR/Whisper 双后端。现在补依赖和设置界面。

接下来在设置界面加 “ASR 后端” 下拉，并把模型下拉加入 `Fun-ASR-Nano`。

我发现保存时如果当前是 FunASR，会把 `model_size` 也写成模型路径，之后切回 Whisper 会不方便。我顺手把保存逻辑改成 FunASR/Whisper 分开保存。

我还会把管道日志从“加载 Whisper”改成“加载本地 ASR”，这样切到 FunASR 后日志不误导。

实现改完了，我现在做语法检查和编辑器诊断；如果环境没有安装 FunASR，这一步也不会导入模型。

已接入 `Fun-ASR-Nano` 作为本地 ASR 后端，并默认替代 Whisper。

改动点：

- `src/core/asr_engine.py`
  - 新增 `funasr` 后端。
  - 默认模型：`FunAudioLLM/Fun-ASR-Nano-2512`。
  - 使用 `funasr.AutoModel` 加载，支持 `ms/hf` hub。
  - 识别时会把音频临时写成 wav，再调用 `model.generate()`，输出带标点文本。
  - 保留 `whisper` 后端作为回退选项。

- `src/utils/config.py` / `config/default_config.yaml`
  - 新增：
    ```yaml
    asr:
      backend: funasr
      funasr_model: FunAudioLLM/Fun-ASR-Nano-2512
      funasr_hub: ms
    ```

- `src/gui/settings_dialog.py`
  - 设置界面新增 `ASR后端`：`funasr / whisper`
  - ASR 模型下拉加入 `FunAudioLLM/Fun-ASR-Nano-2512`

- `requirements.txt`
  - 新增：
    ```txt
    funasr
    ```

检查已通过：语法检查和 lint 都没问题。

你需要先安装依赖：

```bash
python -m pip install -r requirements.txt
```

注意：如果当前翻译后端是 `volc`，本地 ASR 不会参与，FunASR 只会在非火山模式或火山失败回退本地时替代 Whisper。要专门测试 FunASR，就把翻译后端临时切到 `microsoft`/`auto`，然后看日志里是否出现：

```text
加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512
Fun-ASR-Nano 模型加载成功
```

---

**User**

把云端模型加一个设置项，配置是否使用云端模型，以及本地模型加设置项，优先使用哪个本地模型

---

**Cursor**

我理解你的意思是把“云端端到端模型（目前火山 AST）”和“本地 ASR 模型（FunASR/Whisper）”分开配置：先决定是否使用云端；不使用或云端失败时，再按本地模型优先级选择 FunASR 或 Whisper。我先改配置和设置页，再让管道按这个优先级走。

我会把配置语义拆清楚：翻译页加“使用云端模型”开关；ASR 页加“本地模型优先”选项。内部逻辑会变成：云端开关打开且火山可用时走云端；否则按本地优先级尝试 FunASR/Whisper。

配置模型已经加了云端开关和本地优先级。接下来同步默认配置。

现在改 ASR 引擎，让 `auto` 按本地优先级依次尝试，并在 FunASR 失败时自动退回 Whisper。

ASR 加载逻辑已改成优先级尝试。接下来改云端开关对火山管道和文本翻译策略的影响。

火山管道已经受“使用云端模型”开关控制。继续改文本翻译策略，避免云端关闭时 `backend=volc` 仍然激活火山。

现在改设置界面：ASR 页显示“本地模型优先”，翻译页显示“使用云端模型”。

设置项已加到界面。接下来补 `_load_config` 和保存逻辑。

我微调一下保存模型的逻辑：即使 ASR 后端是 auto，只要模型下拉选的是 FunASR 模型路径，就保存到 `funasr_model`。

最后我把本地优先级字段改成安全的默认工厂，然后跑检查。

我现在做语法和 lint 检查，确认新增配置项不会破坏启动。

检查通过了。再确认一下翻译配置里的云端开关已写入默认配置。

已加好两个设置项，并把运行逻辑接上了。

现在配置逻辑是：

- `translation.use_cloud_model`
  - `true`：允许使用云端模型，目前就是火山 AST。
  - `false`：即使后端是 `volc`，也会忽略云端，改走本地 ASR + 文本翻译。

- `asr.local_model_priority`
  - 默认：`["funasr", "whisper"]`
  - 本地 ASR 会按优先级尝试：Fun-ASR-Nano 加载失败时自动退回 Whisper。

设置界面也加了：

- 翻译页：`使用云端模型（火山 AST，关闭后使用本地ASR+文本翻译）`
- 语音识别页：
  - `ASR后端`: `auto / funasr / whisper`
  - `本地模型优先`: `Fun-ASR-Nano 优先，Whisper 兜底` 或反过来

默认配置现在是：

```yaml
asr:
  backend: auto
  local_model_priority:
    - funasr
    - whisper

translation:
  backend: volc
  use_cloud_model: true
```

已通过语法检查和 lint。

---

**User**

 ERROR    | FunASR 未安装，无法加载 Fun-ASR-Nano。请运行: python -m pip install -r requirements.txt
11:32:36 | INFO     | 加载 Whisper 模型: faster-whisper-small, device=cpu
11:32:36 | INFO     |   尝试本地: /home/fiend/WorkSpace/python/translator_intime/models/systran/faster-whisper-small (compute=int8)
11:32:36 | INFO     | Whisper 模型加载成功 ✓
11:32:36 | INFO     | TTS输出: 系统默认扬声器
11:32:36 | INFO     | 翻译管道初始化完成
11:32:36 | INFO     | 音频输入流已打开 (device=None, sr=16000)
11:32:36 | INFO     | PulseAudio输入流已打开 (source=translator_virtual_sink.monitor, sr=16000)
11:32:36 | INFO     | 游戏声音捕获已开启
11:32:36 | INFO     | ✅ 双向翻译管道已启动（出站 + 入站）
11:32:36 | INFO     | 出站管道(中->外) 启动
11:32:37 | INFO     | 入站管道(外->中) 启动
11:32:38 | INFO     | 系统音频输入电平 rms=0.00000
(venv) [10.6.3.58]☁  translator_intime  FunASR 未安装，无法加载 Fun-ASR-Nano。请运行: python -m pip install -r requirements.txt

zsh: command not found: FunASR
(venv) [10.6.3.58]☁  translator_intime  python -m pip install -r requirements.txt
Requirement already satisfied: PyQt6>=6.5.0 in /usr/lib/python3/dist-packages (from -r requirements.txt (line 2)) (6.6.1)
Requirement already satisfied: PyYAML>=6.0 in /usr/lib/python3/dist-packages (from -r requirements.txt (line 3)) (6.0.1)
Requirement already satisfied: sounddevice>=0.4.6 in /home/fiend/.local/lib/python3.12/site-packages (from -r requirements.txt (line 6)) (0.5.5)
Requirement already satisfied: PyAudio>=0.2.11 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 7)) (0.2.14)
Requirement already satisfied: numpy>=1.24.0 in /usr/lib/python3/dist-packages (from -r requirements.txt (line 8)) (1.26.4)
Requirement already satisfied: scipy>=1.10.0 in /usr/lib/python3/dist-packages (from -r requirements.txt (line 9)) (1.11.4)
Requirement already satisfied: funasr in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 12)) (1.3.9)
Requirement already satisfied: faster-whisper>=1.0.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 13)) (1.2.1)
Requirement already satisfied: edge-tts>=6.1.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 17)) (7.2.8)
Requirement already satisfied: soundfile>=0.12.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 18)) (0.13.1)
Requirement already satisfied: openai>=1.0.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 21)) (2.40.0)
Requirement already satisfied: deepl>=1.16.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 22)) (1.30.0)
Requirement already satisfied: googletrans>=4.0.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 23)) (4.0.2)
Requirement already satisfied: aiohttp>=3.9.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 26)) (3.14.0)
Requirement already satisfied: aiofiles>=23.0.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 27)) (25.1.0)
Requirement already satisfied: protobuf<7,>=6.31.1 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 28)) (6.33.6)
Requirement already satisfied: loguru>=0.7.0 in /home/fiend/.local/lib/python3.12/site-packages (from -r requirements.txt (line 31)) (0.7.3)
Requirement already satisfied: pydantic>=2.0.0 in /home/fiend/.local/lib/python3.12/site-packages (from -r requirements.txt (line 32)) (2.13.4)
Requirement already satisfied: python-dotenv>=1.0.0 in ./venv/lib/python3.12/site-packages (from -r requirements.txt (line 33)) (1.2.2)
Requirement already satisfied: PyQt6-sip<14,>=13.6 in /usr/lib/python3/dist-packages (from PyQt6>=6.5.0->-r requirements.txt (line 2)) (13.6.0)
Requirement already satisfied: cffi in /home/fiend/.local/lib/python3.12/site-packages (from sounddevice>=0.4.6->-r requirements.txt (line 6)) (2.0.0)
Requirement already satisfied: librosa in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.11.0)
Requirement already satisfied: tqdm in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (4.67.3)
Requirement already satisfied: requests in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (2.34.2)
Requirement already satisfied: omegaconf>=2.0 in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (2.3.0)
Requirement already satisfied: hydra-core>=1.3.2 in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (1.3.2)
Requirement already satisfied: modelscope in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (1.37.1)
Requirement already satisfied: huggingface_hub in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (1.17.0)
Requirement already satisfied: safetensors in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.7.0)
Requirement already satisfied: transformers in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (5.10.2)
Requirement already satisfied: tiktoken in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.13.0)
Requirement already satisfied: sentencepiece in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.2.1)
Requirement already satisfied: kaldiio>=2.17.0 in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (2.18.1)
Requirement already satisfied: jieba in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.42.1)
Requirement already satisfied: jamo in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.4.1)
Requirement already satisfied: jaconv in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.5.0)
Requirement already satisfied: umap_learn in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.5.12)
Requirement already satisfied: editdistance>=0.5.2 in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.8.1)
Requirement already satisfied: torch_complex in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (0.4.4)
Requirement already satisfied: tensorboardX in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (2.6.5)
Requirement already satisfied: oss2 in ./venv/lib/python3.12/site-packages (from funasr->-r requirements.txt (line 12)) (2.19.1)
Requirement already satisfied: ctranslate2<5,>=4.0 in ./venv/lib/python3.12/site-packages (from faster-whisper>=1.0.0->-r requirements.txt (line 13)) (4.7.2)
Requirement already satisfied: tokenizers<1,>=0.13 in ./venv/lib/python3.12/site-packages (from faster-whisper>=1.0.0->-r requirements.txt (line 13)) (0.22.2)
Requirement already satisfied: onnxruntime<2,>=1.14 in ./venv/lib/python3.12/site-packages (from faster-whisper>=1.0.0->-r requirements.txt (line 13)) (1.26.0)
Requirement already satisfied: av>=11 in ./venv/lib/python3.12/site-packages (from faster-whisper>=1.0.0->-r requirements.txt (line 13)) (17.0.1)
Requirement already satisfied: certifi>=2023.11.17 in /usr/lib/python3/dist-packages (from edge-tts>=6.1.0->-r requirements.txt (line 17)) (2023.11.17)
Requirement already satisfied: tabulate<1.0.0,>=0.4.4 in ./venv/lib/python3.12/site-packages (from edge-tts>=6.1.0->-r requirements.txt (line 17)) (0.10.0)
Requirement already satisfied: typing-extensions<5.0.0,>=4.1.0 in /home/fiend/.local/lib/python3.12/site-packages (from edge-tts>=6.1.0->-r requirements.txt (line 17)) (4.15.0)
Requirement already satisfied: anyio<5,>=3.5.0 in ./venv/lib/python3.12/site-packages (from openai>=1.0.0->-r requirements.txt (line 21)) (4.13.0)
Requirement already satisfied: distro<2,>=1.7.0 in /usr/lib/python3/dist-packages (from openai>=1.0.0->-r requirements.txt (line 21)) (1.9.0)
Requirement already satisfied: httpx<1,>=0.23.0 in ./venv/lib/python3.12/site-packages (from openai>=1.0.0->-r requirements.txt (line 21)) (0.28.1)
Requirement already satisfied: jiter<1,>=0.10.0 in ./venv/lib/python3.12/site-packages (from openai>=1.0.0->-r requirements.txt (line 21)) (0.15.0)
Requirement already satisfied: sniffio in ./venv/lib/python3.12/site-packages (from openai>=1.0.0->-r requirements.txt (line 21)) (1.3.1)
Requirement already satisfied: aiohappyeyeballs>=2.5.0 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (2.6.2)
Requirement already satisfied: aiosignal>=1.4.0 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (1.4.0)
Requirement already satisfied: attrs>=17.3.0 in /usr/lib/python3/dist-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (23.2.0)
Requirement already satisfied: frozenlist>=1.1.1 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (1.8.0)
Requirement already satisfied: multidict<7.0,>=4.5 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (6.7.1)
Requirement already satisfied: propcache>=0.2.0 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (0.5.2)
Requirement already satisfied: yarl<2.0,>=1.17.0 in ./venv/lib/python3.12/site-packages (from aiohttp>=3.9.0->-r requirements.txt (line 26)) (1.24.2)
Requirement already satisfied: annotated-types>=0.6.0 in /home/fiend/.local/lib/python3.12/site-packages (from pydantic>=2.0.0->-r requirements.txt (line 32)) (0.7.0)
Requirement already satisfied: pydantic-core==2.46.4 in /home/fiend/.local/lib/python3.12/site-packages (from pydantic>=2.0.0->-r requirements.txt (line 32)) (2.46.4)
Requirement already satisfied: typing-inspection>=0.4.2 in /home/fiend/.local/lib/python3.12/site-packages (from pydantic>=2.0.0->-r requirements.txt (line 32)) (0.4.2)
Requirement already satisfied: idna>=2.8 in /usr/lib/python3/dist-packages (from anyio<5,>=3.5.0->openai>=1.0.0->-r requirements.txt (line 21)) (3.6)
Requirement already satisfied: pycparser in /home/fiend/.local/lib/python3.12/site-packages (from cffi->sounddevice>=0.4.6->-r requirements.txt (line 6)) (3.0)
Requirement already satisfied: setuptools in /usr/lib/python3/dist-packages (from ctranslate2<5,>=4.0->faster-whisper>=1.0.0->-r requirements.txt (line 13)) (68.1.2)
Requirement already satisfied: httpcore==1.* in ./venv/lib/python3.12/site-packages (from httpx<1,>=0.23.0->openai>=1.0.0->-r requirements.txt (line 21)) (1.0.9)
Requirement already satisfied: h11>=0.16 in ./venv/lib/python3.12/site-packages (from httpcore==1.*->httpx<1,>=0.23.0->openai>=1.0.0->-r requirements.txt (line 21)) (0.16.0)
Requirement already satisfied: h2<5,>=3 in ./venv/lib/python3.12/site-packages (from httpx[http2]>=0.27.2->googletrans>=4.0.0->-r requirements.txt (line 23)) (4.3.0)
Requirement already satisfied: click>=8.4.0 in ./venv/lib/python3.12/site-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (8.4.1)
Requirement already satisfied: filelock>=3.10.0 in ./venv/lib/python3.12/site-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (3.29.0)
Requirement already satisfied: fsspec>=2023.5.0 in ./venv/lib/python3.12/site-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (2026.4.0)
Requirement already satisfied: hf-xet<2.0.0,>=1.4.3 in ./venv/lib/python3.12/site-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (1.5.0)
Requirement already satisfied: packaging>=20.9 in /usr/lib/python3/dist-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (24.0)
Requirement already satisfied: typer<0.26.0,>=0.20.0 in ./venv/lib/python3.12/site-packages (from huggingface_hub->funasr->-r requirements.txt (line 12)) (0.25.1)
Requirement already satisfied: antlr4-python3-runtime==4.9.* in ./venv/lib/python3.12/site-packages (from hydra-core>=1.3.2->funasr->-r requirements.txt (line 12)) (4.9.3)
Requirement already satisfied: flatbuffers in ./venv/lib/python3.12/site-packages (from onnxruntime<2,>=1.14->faster-whisper>=1.0.0->-r requirements.txt (line 13)) (25.12.19)
Requirement already satisfied: charset_normalizer<4,>=2 in ./venv/lib/python3.12/site-packages (from requests->funasr->-r requirements.txt (line 12)) (3.4.7)
Requirement already satisfied: urllib3<3,>=1.26 in /usr/lib/python3/dist-packages (from requests->funasr->-r requirements.txt (line 12)) (2.0.7)
Requirement already satisfied: audioread>=2.1.9 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (3.1.0)
Requirement already satisfied: numba>=0.51.0 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (0.65.1)
Requirement already satisfied: scikit-learn>=1.1.0 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (1.9.0)
Requirement already satisfied: joblib>=1.0 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (1.5.3)
Requirement already satisfied: decorator>=4.3.0 in /usr/lib/python3/dist-packages (from librosa->funasr->-r requirements.txt (line 12)) (5.1.1)
Requirement already satisfied: pooch>=1.1 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (1.9.0)
Requirement already satisfied: soxr>=0.3.2 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (1.1.0)
Requirement already satisfied: lazy_loader>=0.1 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (0.5)
Requirement already satisfied: msgpack>=1.0 in ./venv/lib/python3.12/site-packages (from librosa->funasr->-r requirements.txt (line 12)) (1.1.2)
Requirement already satisfied: aliyun-python-sdk-core>=2.13.12 in ./venv/lib/python3.12/site-packages (from oss2->funasr->-r requirements.txt (line 12)) (2.16.0)
Requirement already satisfied: aliyun-python-sdk-kms>=2.4.1 in ./venv/lib/python3.12/site-packages (from oss2->funasr->-r requirements.txt (line 12)) (2.16.5)
Requirement already satisfied: crcmod>=1.7 in ./venv/lib/python3.12/site-packages (from oss2->funasr->-r requirements.txt (line 12)) (1.7)
Requirement already satisfied: pycryptodome>=3.4.7 in ./venv/lib/python3.12/site-packages (from oss2->funasr->-r requirements.txt (line 12)) (3.23.0)
Requirement already satisfied: six in /usr/lib/python3/dist-packages (from oss2->funasr->-r requirements.txt (line 12)) (1.16.0)
Requirement already satisfied: regex in ./venv/lib/python3.12/site-packages (from tiktoken->funasr->-r requirements.txt (line 12)) (2026.5.9)
Requirement already satisfied: pynndescent>=0.5 in ./venv/lib/python3.12/site-packages (from umap_learn->funasr->-r requirements.txt (line 12)) (0.6.0)
Requirement already satisfied: cryptography>=3.0.0 in /usr/lib/python3/dist-packages (from aliyun-python-sdk-core>=2.13.12->oss2->funasr->-r requirements.txt (line 12)) (41.0.7)
Requirement already satisfied: jmespath<1.0.0,>=0.9.3 in ./venv/lib/python3.12/site-packages (from aliyun-python-sdk-core>=2.13.12->oss2->funasr->-r requirements.txt (line 12)) (0.10.0)
Requirement already satisfied: hyperframe<7,>=6.1 in ./venv/lib/python3.12/site-packages (from h2<5,>=3->httpx[http2]>=0.27.2->googletrans>=4.0.0->-r requirements.txt (line 23)) (6.1.0)
Requirement already satisfied: hpack<5,>=4.1 in ./venv/lib/python3.12/site-packages (from h2<5,>=3->httpx[http2]>=0.27.2->googletrans>=4.0.0->-r requirements.txt (line 23)) (4.1.0)
Requirement already satisfied: llvmlite<0.48,>=0.47.0dev0 in ./venv/lib/python3.12/site-packages (from numba>=0.51.0->librosa->funasr->-r requirements.txt (line 12)) (0.47.0)
Requirement already satisfied: platformdirs>=2.5.0 in ./venv/lib/python3.12/site-packages (from pooch>=1.1->librosa->funasr->-r requirements.txt (line 12)) (4.10.0)
Requirement already satisfied: narwhals>=2.0.1 in ./venv/lib/python3.12/site-packages (from scikit-learn>=1.1.0->librosa->funasr->-r requirements.txt (line 12)) (2.22.1)
Requirement already satisfied: threadpoolctl>=3.5.0 in ./venv/lib/python3.12/site-packages (from scikit-learn>=1.1.0->librosa->funasr->-r requirements.txt (line 12)) (3.6.0)
Requirement already satisfied: shellingham>=1.3.0 in ./venv/lib/python3.12/site-packages (from typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (1.5.4)
Requirement already satisfied: rich>=13.8.0 in ./venv/lib/python3.12/site-packages (from typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (15.0.0)
Requirement already satisfied: annotated-doc>=0.0.2 in ./venv/lib/python3.12/site-packages (from typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (0.0.4)
Requirement already satisfied: markdown-it-py>=2.2.0 in /usr/lib/python3/dist-packages (from rich>=13.8.0->typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (3.0.0)
Requirement already satisfied: pygments<3.0.0,>=2.13.0 in /usr/lib/python3/dist-packages (from rich>=13.8.0->typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (2.17.2)
Requirement already satisfied: mdurl~=0.1 in /usr/lib/python3/dist-packages (from markdown-it-py>=2.2.0->rich>=13.8.0->typer<0.26.0,>=0.20.0->huggingface_hub->funasr->-r requirements.txt (line 12)) (0.1.2)


---

**Cursor**

这个现象说明“运行程序的 Python”和“安装 funasr 的 Python”很可能不是同一个，或者 `funasr` 导入时内部依赖报错被我们笼统显示成“未安装”。我先直接用当前目录的 `venv/bin/python` 测一下 `import funasr` 的真实错误。

我这边测到 `venv/bin/python` 也导入不到 `funasr`，而且 `sys.executable` 显示异常，说明这个 venv 的 Python 链接/激活状态有问题。继续检查 `venv` 和 `.venv` 两个环境的实际路径。

我看到 `pip show funasr` 确实在 `./venv` 里，但我们代码把所有导入异常都报成“未安装”，这会掩盖真实原因。我先把错误日志改准确：输出当前 Python 路径和完整导入异常，方便判断到底是没装、路径不对，还是 FunASR 内部依赖炸了。

日志已改成能区分“funasr 包没找到”和“funasr 内部依赖缺失”。我再加一个诊断脚本，直接打印当前运行环境的解释器、pip 和 funasr 导入状态。

诊断脚本已添加。现在做语法检查。

你这里不要把日志那句当命令输入：

```bash
FunASR 未安装，无法加载 Fun-ASR-Nano...
```

所以才会有：

```text
zsh: command not found: FunASR
```

不过你后面 `pip install -r requirements.txt` 显示 `funasr` 已经在 `./venv` 里了。现在问题大概率是两种之一：

1. 程序启动那次是在安装前跑的，重启后就好了。
2. `python run.py` 用的解释器和 `pip install` 的解释器不一致。
3. `funasr` 包能找到，但它内部某个依赖导入失败，我们之前误报成“未安装”。

我已经把日志改准确了：之后会打印真实导入失败原因和 Python 解释器路径。

请你现在运行这两条，把输出贴我：

```bash
python scripts/check_python_env.py
```

以及：

```bash
python -c "import sys; print(sys.executable); import funasr; print(funasr.__file__)"
```

如果你想确保用的是项目里的 `venv`，直接这样启动：

```bash
./venv/bin/python run.py
```

如果这条能加载 FunASR，而 `python run.py` 不行，就说明 shell 里的 `python` 指向错了。

---

**User**

 python scripts/check_python_env.py
python: /home/fiend/WorkSpace/python/translator_intime/venv/bin/python
version: 3.12.3 (main, Mar 23 2026, 19:04:32) [GCC 13.3.0]
prefix: /home/fiend/WorkSpace/python/translator_intime/venv
base_prefix: /usr
site-packages:
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/local/lib/python3.12/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/dist-packages
   /usr/lib/python3.12/site-packages
   /usr/local/lib/python3.12/dist-packages
   /usr/lib/python3/dist-packages
   /usr/lib/python3.12/dist-packages

pip:
pip 24.0 from /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/pip (python 3.12)

import funasr:
[transformers] PyTorch was not found. Models won't be available and only tokenizers, configuration and file/data utilities can be used.
  failed: ModuleNotFoundError("No module named 'torch'")

import faster_whisper:
  ok 1.2.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/faster_whisper/__init__.py

import aiohttp:
  ok 3.14.0 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/aiohttp/__init__.py

import soundfile:
  ok 0.13.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/soundfile.py
(venv) [10.6.3.58]☁  translator_intime  python -c "import sys; print(sys.executable); import funasr; print(funasr.__file__)"
/home/fiend/WorkSpace/python/translator_intime/venv/bin/python
[transformers] PyTorch was not found. Models won't be available and only tokenizers, configuration and file/data utilities can be used.
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py", line 76, in <module>
    from funasr.auto.auto_model import AutoModel
  File "/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/auto/auto_model.py", line 9, in <module>
    import torch
ModuleNotFoundError: No module named 'torch'

---

**Cursor**

诊断很明确：`funasr` 已安装，真正缺的是它的运行依赖 `torch`。我会把提示改准确，并加一个专门安装 CPU 版 PyTorch/FunASR 的脚本，避免你直接 `pip install torch` 下载一堆 CUDA 大包。

我会新增 `requirements-funasr-cpu.txt`，把 CPU 版 PyTorch 的安装源单独隔离出来；主 `requirements.txt` 保持通用依赖，避免影响其它包。

接着我把 FunASR 缺依赖的日志改得更直观，明确告诉你缺的是 PyTorch。

现在我做语法检查。

问题找到了：`funasr` 已经装了，缺的是 `torch`。

你的输出里关键是：

```text
failed: ModuleNotFoundError("No module named 'torch'")
```

我已做两件事：

1. 新增 `requirements-funasr-cpu.txt`，专门安装 CPU 版 PyTorch，避免误装 CUDA 大包。
2. 改了日志提示，以后会明确说缺 `torch`，而不是误报 FunASR 未安装。

现在执行：

```bash
python -m pip install -r requirements-funasr-cpu.txt
```

然后验证：

```bash
python scripts/check_python_env.py
```

看到下面这样就 OK：

```text
import funasr:
  ok ...
```

再启动：

```bash
python run.py
```

如果你有 NVIDIA GPU 并且想用 CUDA 版 PyTorch，不要用这个 CPU requirements，我再给你对应 CUDA 版本安装命令。

---

**User**

我要用cpu运行模式

---

**Cursor**

那就用 CPU 版 PyTorch，执行：

```bash
python -m pip install -r requirements-funasr-cpu.txt
```

确认安装成功：

```bash
python scripts/check_python_env.py
```

看到 `import funasr: ok` 就可以启动：

```bash
python run.py
```

设置里保持：

- `ASR后端`: `auto` 或 `funasr`
- `运行设备`: `cpu`
- `本地模型优先`: `Fun-ASR-Nano 优先，Whisper 兜底`

首次加载 `Fun-ASR-Nano` 可能会下载模型，CPU 推理速度也会比 Whisper 慢一些。

---

**User**

 python -m pip install -r requirements-funasr-cpu.txt
Looking in indexes: https://download.pytorch.org/whl/cpu
Collecting torch (from -r requirements-funasr-cpu.txt (line 5))
  Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.12.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl.metadata (31 kB)
Requirement already satisfied: filelock in ./venv/lib/python3.12/site-packages (from torch->-r requirements-funasr-cpu.txt (line 5)) (3.29.0)
Requirement already satisfied: typing-extensions>=4.10.0 in /home/fiend/.local/lib/python3.12/site-packages (from torch->-r requirements-funasr-cpu.txt (line 5)) (4.15.0)
Requirement already satisfied: setuptools<82 in /usr/lib/python3/dist-packages (from torch->-r requirements-funasr-cpu.txt (line 5)) (68.1.2)
Collecting sympy>=1.13.3 (from torch->-r requirements-funasr-cpu.txt (line 5))
  Downloading sympy-1.14.0-py3-none-any.whl.metadata (12 kB)
Collecting networkx>=2.5.1 (from torch->-r requirements-funasr-cpu.txt (line 5))
  Downloading networkx-3.6.1-py3-none-any.whl.metadata (6.8 kB)
Requirement already satisfied: jinja2 in /usr/lib/python3/dist-packages (from torch->-r requirements-funasr-cpu.txt (line 5)) (3.1.2)
Requirement already satisfied: fsspec>=0.8.5 in ./venv/lib/python3.12/site-packages (from torch->-r requirements-funasr-cpu.txt (line 5)) (2026.4.0)
Collecting mpmath<1.4,>=1.1.0 (from sympy>=1.13.3->torch->-r requirements-funasr-cpu.txt (line 5))
  Downloading mpmath-1.3.0-py3-none-any.whl.metadata (8.6 kB)
Downloading https://download-r2.pytorch.org/whl/cpu/torch-2.12.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl (192.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 192.3/192.3 MB 629.6 kB/s eta 0:00:00
Downloading networkx-3.6.1-py3-none-any.whl (2.1 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2.1/2.1 MB 683.4 kB/s eta 0:00:00
Downloading sympy-1.14.0-py3-none-any.whl (6.3 MB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 6.3/6.3 MB 666.6 kB/s eta 0:00:00
Downloading mpmath-1.3.0-py3-none-any.whl (536 kB)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 536.2/536.2 kB 631.3 kB/s eta 0:00:00
Installing collected packages: mpmath, sympy, networkx, torch
  Attempting uninstall: mpmath
    Found existing installation: mpmath 0.0.0
    Not uninstalling mpmath at /usr/lib/python3/dist-packages, outside environment /home/fiend/WorkSpace/python/translator_intime/venv
    Can't uninstall 'mpmath'. No files were found to uninstall.
  Attempting uninstall: sympy
    Found existing installation: sympy 1.12
    Not uninstalling sympy at /usr/lib/python3/dist-packages, outside environment /home/fiend/WorkSpace/python/translator_intime/venv
    Can't uninstall 'sympy'. No files were found to uninstall.
Successfully installed mpmath-1.3.0 networkx-3.6.1 sympy-1.14.0 torch-2.12.0+cpu
(venv) [10.6.3.58]☁  translator_intime  python -c "import sys; print(sys.executable); import funasr; print(funasr.__file__)"
(venv) [10.6.3.58]☁  translator_intime  python scripts/check_python_env.py
python: /home/fiend/WorkSpace/python/translator_intime/venv/bin/python
version: 3.12.3 (main, Mar 23 2026, 19:04:32) [GCC 13.3.0]
prefix: /home/fiend/WorkSpace/python/translator_intime/venv
base_prefix: /usr
site-packages:
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/local/lib/python3.12/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/dist-packages
   /usr/lib/python3.12/site-packages
   /usr/local/lib/python3.12/dist-packages
   /usr/lib/python3/dist-packages
   /usr/lib/python3.12/dist-packages

pip:
pip 24.0 from /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/pip (python 3.12)

import funasr:
<unknown>:87: SyntaxWarning: invalid escape sequence '\['
<unknown>:163: SyntaxWarning: invalid escape sequence '\['
<unknown>:174: SyntaxWarning: invalid escape sequence '\['
<unknown>:334: SyntaxWarning: invalid escape sequence '\['
<unknown>:355: SyntaxWarning: invalid escape sequence '\['
<unknown>:378: SyntaxWarning: invalid escape sequence '\['
  failed: ModuleNotFoundError("No module named 'torchaudio'")

import faster_whisper:
  ok 1.2.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/faster_whisper/__init__.py

import aiohttp:
  ok 3.14.0 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/aiohttp/__init__.py

import soundfile:
  ok 0.13.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/soundfile.py


---

**Cursor**

现在 `torch` 已经装好了，新的真实缺失依赖是 `torchaudio`。FunASR 会依赖它处理音频，所以我把 CPU 版安装清单补上 `torchaudio`，并把错误提示也扩展到这个依赖。

我再把代码里的依赖缺失提示补成 `torch/torchaudio` 都能给出同一个 CPU 安装命令。

补丁已完成，我做个快速语法检查。

很好，`torch` 已经装成功了。现在缺的是 `torchaudio`：

```text
ModuleNotFoundError("No module named 'torchaudio'")
```

我已经把 `requirements-funasr-cpu.txt` 补上了 `torchaudio`。继续执行：

```bash
python -m pip install -r requirements-funasr-cpu.txt
```

然后再验证：

```bash
python scripts/check_python_env.py
```

目标是看到：

```text
import funasr:
  ok ...
```

那些 `SyntaxWarning: invalid escape sequence` 暂时不用管，是依赖包内部 warning，不影响运行。

---

**User**

 python scripts/check_python_env.py
python: /home/fiend/WorkSpace/python/translator_intime/venv/bin/python
version: 3.12.3 (main, Mar 23 2026, 19:04:32) [GCC 13.3.0]
prefix: /home/fiend/WorkSpace/python/translator_intime/venv
base_prefix: /usr
site-packages:
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/local/lib/python3.12/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3/dist-packages
   /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/dist-packages
   /usr/lib/python3.12/site-packages
   /usr/local/lib/python3.12/dist-packages
   /usr/lib/python3/dist-packages
   /usr/lib/python3.12/dist-packages

pip:
pip 24.0 from /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/pip (python 3.12)

import funasr:
  ok 1.3.9 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py

import faster_whisper:
  ok 1.2.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/faster_whisper/__init__.py

import aiohttp:
  ok 3.14.0 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/aiohttp/__init__.py

import soundfile:
  ok 0.13.1 /home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/soundfile.py


---

**Cursor**

现在环境正常了，`FunASR` 已经能导入：

```text
import funasr:
  ok 1.3.9
```

可以启动测试：

```bash
python run.py
```

如果你想确认正在用 Fun-ASR-Nano，设置里保持：

- `使用云端模型`: 关闭（否则会走火山云端）
- `ASR后端`: `auto` 或 `funasr`
- `本地模型优先`: `Fun-ASR-Nano 优先，Whisper 兜底`
- `运行设备`: `cpu`

启动日志应该出现类似：

```text
下载/加载本地 ASR 模型（backend=auto）
加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu
Fun-ASR-Nano 模型加载成功
```

首次运行会下载模型，可能比较慢。

---

**User**

WARNING:root:trust_remote_code: True
Loading remote code failed: ./model.py, No module named 'model'


---

**Cursor**

这个报错是我接入时把示例里的 `remote_code="./model.py"` 写死了，但你的项目目录下没有这个文件。FunASR 从 ModelScope/HF 加载远程代码时不应该强制指定本地 `./model.py`，我马上去掉这个参数。

我已去掉本地 `remote_code` 参数。现在做一下语法检查。

已修复。问题是代码里写了：

```python
remote_code="./model.py"
```

但当前项目没有 `model.py`，所以 FunASR 报：

```text
Loading remote code failed: ./model.py, No module named 'model'
```

我已经去掉这个参数，让 FunASR 按 `trust_remote_code=True` 从模型仓库加载远程代码。语法检查和 lint 都通过。

重新运行：

```bash
python run.py
```

---

**User**

 INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=ms
funasr version: 1.3.9.
Check update of funasr, and it would cost few times. You may disable it by set `disable_update=True` in AutoModel
You are using the latest version of funasr-1.3.9
Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512
WARNING:root:trust_remote_code: True
Loading remote code failed: model, No module named 'model'


---

**Cursor**

这次不是本地路径问题了，而是 `funasr==1.3.9` 从 ModelScope 拉到 `Fun-ASR-Nano-2512` 后加载远程代码失败。这个模型比较新，可能需要 FunASR GitHub 最新版，或者改用 HF hub。我先确认本地缓存结构和官方示例参数。

这个错误是 Fun-ASR-Nano 当前生态里的已知坑：ModelScope 这份缓存没有 `model.py`，而 pip 版 FunASR 对 Nano 的远程代码/注册支持不稳定。可行方案有两个：优先改用 HF hub 的加载方式；如果仍失败，再把 FunASR 升到 GitHub 最新版。 我先检查你当前 pip 包里是否已经带 `fun_asr_nano` 模块。

我会把 FunASR 加载改成“按 hub 候选重试”：配置是 `ms` 时先试 ModelScope，遇到这个远程代码错误后自动试 `hf`；配置是 `hf` 时反过来。这样不用你手动改配置反复试。

我还把默认 `funasr_hub` 调整为 `hf`，因为官方 HF 示例不需要本地 `model.py`，ModelScope 路径更容易触发这个注册问题；如果你网络不通，它会自动回试 `ms`。

现在我做语法和 lint 检查。

这是 Fun-ASR-Nano 当前的已知加载坑：ModelScope 路径会找 `model.py`，但缓存里没有，所以报：

```text
Loading remote code failed: model, No module named 'model'
```

我已改成：

- 默认优先用 `hub=hf`
- 如果失败，自动回试 `hub=ms`
- `vad_model` 在 HF 下用 `funasr/fsmn-vad`
- 加了 `disable_update=True`，避免每次启动都检查 FunASR 更新
- 如果两个 hub 都失败，会提示安装 FunASR GitHub 最新源码版

你现在直接重启：

```bash
python run.py
```

如果 HF 网络能通，应该会开始从 HuggingFace 下载并加载模型。若仍然失败并提示 `No module named 'model'`，执行：

```bash
python -m pip install -U git+https://github.com/modelscope/FunASR.git
```

然后再试。

---

**User**

 translator_intime  python run.py
11:49:13 | INFO     | 日志系统初始化完成
11:49:13 | INFO     | ============================================================
11:49:13 | INFO     | Translator InTime v0.1.0 启动
11:49:13 | INFO     | 实时游戏语音双向翻译系统
11:49:13 | INFO     | ============================================================
11:49:13 | INFO     | GUI 已启动
(venv) [10.6.3.58]☁  translator_intime  python run.py
11:49:44 | INFO     | 日志系统初始化完成
11:49:44 | INFO     | ============================================================
11:49:44 | INFO     | Translator InTime v0.1.0 启动
11:49:44 | INFO     | 实时游戏语音双向翻译系统
11:49:44 | INFO     | ============================================================
11:49:44 | INFO     | GUI 已启动
11:49:46 | INFO     | 云端模型已关闭，忽略 volc 后端，改用 auto 本地/文本翻译策略
11:49:46 | INFO     | 翻译策略: microsoft (降级: google)
11:49:46 | INFO     | 初始化翻译管道...
11:49:46 | INFO     | 虚拟音频设备 'translator_virtual' 已存在
11:49:46 | INFO     | Monitor 源自动可用: translator_virtual_sink.monitor
11:49:46 | INFO     | 下载/加载本地 ASR 模型（backend=auto）...
11:49:51 | INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=hf
funasr version: 1.3.9.
Fetching 21 files:   0%|                                 | 0/21 [00:00<?, ?it/s]
Download: FunAudioLLM/Fun-ASR-Nano-2512 failed!: An error happened while trying to locate the file on the Hub and we cannot find the requested files in the local cache. Please check your connection and try again or make sure your Internet connection is on.
WARNING:root:trust_remote_code: True
11:49:53 | WARNING  | Fun-ASR-Nano 模型加载失败 (hub=hf): model 'FunAudioLLM/Fun-ASR-Nano-2512' is not registered.
Registered model keys (48): BAT, BiCifParaformer, Branchformer, CAMPPlus, CTC, CTTransformer, CTTransformerStreaming, Conformer, ContextualParaformer, EBranchformer, EParaformer, ERes2NetV2, Emotion2vec, FsmnKWS, FsmnKWSConvert, FsmnKWSMT, FsmnKWSMTConvert, FsmnVADStreaming, FunASRNano, GLMASR, LCBNet, LLMASR, LLMASR2, LLMASR3, LLMASR4, LLMASRNAR, LLMASRNARPrompt, MonotonicAligner, OpenAIWhisperLIDModel, OpenAIWhisperModel, Paraformer, ParaformerStreaming, Paraformer_v2_community, Qwen/Qwen3-ASR-0.6B, Qwen/Qwen3-ASR-1.7B, Qwen3ASR, SANM, SCAMA, SanmKWS, SanmKWSStreaming, SeacoParaformer, SenseVoiceSmall, Transducer, Transformer, UniASR, ZhipuAI/GLM-ASR-Nano-2512, iic/speech_eres2netv2_sv_zh-cn_16k-common, zai-org/GLM-ASR-Nano-2512
Some modules may have failed to import during auto-registration. Set FUNASR_IMPORT_DEBUG=1 to print failures during import, or FUNASR_STRICT_IMPORT=1 to fail fast.
Recorded import failures:
  - funasr.bin._server_app: ImportError: funasr-server requires additional packages. Install with: pip install vllm fastapi uvicorn python-multipart
  - funasr.bin.train: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.bin.train_ds: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.frontends.default: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.fused: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.s3prl: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.dnn_wpe: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.frontend: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.models.fun_asr_nano.tools.whisper_mix_normalize: ModuleNotFoundError: No module named 'cn_tn'
  - funasr.models.language_model.rnn.decoders: ModuleNotFoundError: No module named 'funasr.models.transformer.utils.scorers'
  - funasr.models.language_model.seq_rnn_lm: ModuleNotFoundError: No module named 'funasr.train'
  - funasr.models.language_model.transformer_lm: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mfcca.e2e_asr_mfcca: ImportError: cannot import name 'ErrorCalculator' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.mfcca.mfcca_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mossformer.e2e_ss: ModuleNotFoundError: No module named 'funasr.models.base_model'
  - funasr.models.mossformer.mossformer: ModuleNotFoundError: No module named 'einops'
  - funasr.models.mossformer.mossformer_encoder: ModuleNotFoundError: No module named 'einops'
  - funasr.models.qwen_audio.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.sa_asr.beam_search_sa_asr: ImportError: cannot import name 'end_detect' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.sa_asr.e2e_sa_asr: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.models.sense_voice.whisper_lib.normalizers: ModuleNotFoundError: No module named 'more_itertools'
  - funasr.models.sense_voice.whisper_lib.triton_ops: SyntaxError: invalid syntax (triton_ops.py, line 57)
  - funasr.models.sond.e2e_diar_sond: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.sond.encoder.conv_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.fsmn_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.resnet34_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.self_attention_encoder: ImportError: cannot import name 'CTC' from 'funasr.models.ctc' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/models/ctc/__init__.py)
  - funasr.models.sond.sv_decoder: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.whisper.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.decoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.encoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.eres2net.simple_avg: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.xvector.e2e_sv: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.utils.speaker_utils: ModuleNotFoundError: No module named 'funasr.utils.modelscope_file'
11:49:53 | INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=ms
funasr version: 1.3.9.
Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512
Downloading (incomplete total...): 0.00B [00:01, ?B/s]
WARNING:root:trust_remote_code: True
Loading remote code failed: model, No module named 'model'

---

**Cursor**

现在日志已经把问题暴露得很清楚：FunASR 不是单纯加载参数问题，它的 Nano 模型注册/依赖链还缺东西，尤其是 `cn_tn`，并且 HF 下载失败后被当作模型名注册查找。我先看本地模型配置和 FunASR 包里 Nano 代码，确认最小修复是补依赖还是改模型名。

日志里的关键不是网络，而是 FunASR pip 包里 `fun_asr_nano` 的内部导入用了裸模块名，比如 `cn_tn`/`ctc`，导致自动注册失败。你本地包里其实有这些文件，我会在加载 FunASR 前把对应目录加入 `sys.path`，并手动注册 `FunASRNano`，绕过这个已知坑。

我会再微调一下导入顺序：最好在 `funasr` 顶层导入前就尝试手动注册路径依赖，但现在已经加了路径准备和注册。先跑语法检查。

这次日志里的关键是：

```text
FunASRNano 已经在 Registered model keys 里
但 funasr.models.fun_asr_nano.tools.whisper_mix_normalize 缺 cn_tn
```

这是 FunASR pip 包里 Fun-ASR-Nano 的已知导入坑：包内明明有 `tools/cn_tn.py`，但代码用了裸导入 `import cn_tn`，从你的项目目录运行时找不到。

我已经做了兼容修复：

- 加载 FunASR 前，把 `funasr/models/fun_asr_nano` 和 `funasr/models/fun_asr_nano/tools` 加入 `sys.path`
- 手动注册 `FunASRNano` 到 FunASR 的 registry
- 这样可以绕过 `cn_tn`/`ctc` 这类裸导入导致的自动注册失败

语法和 lint 已通过。

现在再运行：

```bash
python run.py
```

如果还失败，把新的 `Fun-ASR-Nano 模型加载失败` 后面的具体错误贴出来。当前这一步已经不是缺 `torch/torchaudio` 了，而是在处理 FunASR Nano 自身包结构问题。

---

**User**

ERROR    | FunASR 导入失败: name 'self' is not defined

---

**Cursor**

这个是我刚才补丁缩进放错了：`_load_funasr_model()` 的主体被提前截断，导致类方法里的代码跑到了没有 `self` 的作用域。我马上修正结构。

我看到问题了：第 123 行开始的加载模型逻辑误缩进到了 `_register_funasr_nano()` 里面，所以静态方法里引用了 `self`。我会把这一段移回 `_load_funasr_model()`。

缩进结构已修正。现在我跑语法和 lint 检查。

修好了，是我刚才补丁缩进放错导致的：

```text
name 'self' is not defined
```

原因是 FunASR 加载逻辑误缩进到了 `@staticmethod _register_funasr_nano()` 里面。现在已经移回 `_load_funasr_model()`，语法检查和 lint 都通过。

重新运行：

```bash
python run.py
```

---

**User**

INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=hf
funasr version: 1.3.9.
Fetching 21 files:   0%|                                 | 0/21 [00:00<?, ?it/s]
Download: FunAudioLLM/Fun-ASR-Nano-2512 failed!: An error happened while trying to locate the file on the Hub and we cannot find the requested files in the local cache. Please check your connection and try again or make sure your Internet connection is on.
WARNING:root:trust_remote_code: True
11:52:35 | WARNING  | Fun-ASR-Nano 模型加载失败 (hub=hf): model 'FunAudioLLM/Fun-ASR-Nano-2512' is not registered.
Registered model keys (48): BAT, BiCifParaformer, Branchformer, CAMPPlus, CTC, CTTransformer, CTTransformerStreaming, Conformer, ContextualParaformer, EBranchformer, EParaformer, ERes2NetV2, Emotion2vec, FsmnKWS, FsmnKWSConvert, FsmnKWSMT, FsmnKWSMTConvert, FsmnVADStreaming, FunASRNano, GLMASR, LCBNet, LLMASR, LLMASR2, LLMASR3, LLMASR4, LLMASRNAR, LLMASRNARPrompt, MonotonicAligner, OpenAIWhisperLIDModel, OpenAIWhisperModel, Paraformer, ParaformerStreaming, Paraformer_v2_community, Qwen/Qwen3-ASR-0.6B, Qwen/Qwen3-ASR-1.7B, Qwen3ASR, SANM, SCAMA, SanmKWS, SanmKWSStreaming, SeacoParaformer, SenseVoiceSmall, Transducer, Transformer, UniASR, ZhipuAI/GLM-ASR-Nano-2512, iic/speech_eres2netv2_sv_zh-cn_16k-common, zai-org/GLM-ASR-Nano-2512
Some modules may have failed to import during auto-registration. Set FUNASR_IMPORT_DEBUG=1 to print failures during import, or FUNASR_STRICT_IMPORT=1 to fail fast.
Recorded import failures:
  - funasr.bin._server_app: ImportError: funasr-server requires additional packages. Install with: pip install vllm fastapi uvicorn python-multipart
  - funasr.bin.train: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.bin.train_ds: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.frontends.default: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.fused: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.s3prl: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.dnn_wpe: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.frontend: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.models.fun_asr_nano.tools.whisper_mix_normalize: ModuleNotFoundError: No module named 'pyopenjtalk'
  - funasr.models.language_model.rnn.decoders: ModuleNotFoundError: No module named 'funasr.models.transformer.utils.scorers'
  - funasr.models.language_model.seq_rnn_lm: ModuleNotFoundError: No module named 'funasr.train'
  - funasr.models.language_model.transformer_lm: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mfcca.e2e_asr_mfcca: ImportError: cannot import name 'ErrorCalculator' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.mfcca.mfcca_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mossformer.e2e_ss: ModuleNotFoundError: No module named 'funasr.models.base_model'
  - funasr.models.mossformer.mossformer: ModuleNotFoundError: No module named 'einops'
  - funasr.models.mossformer.mossformer_encoder: ModuleNotFoundError: No module named 'einops'
  - funasr.models.qwen_audio.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.sa_asr.beam_search_sa_asr: ImportError: cannot import name 'end_detect' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.sa_asr.e2e_sa_asr: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.models.sense_voice.whisper_lib.normalizers: ModuleNotFoundError: No module named 'more_itertools'
  - funasr.models.sense_voice.whisper_lib.triton_ops: SyntaxError: invalid syntax (triton_ops.py, line 57)
  - funasr.models.sond.e2e_diar_sond: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.sond.encoder.conv_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.fsmn_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.resnet34_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.self_attention_encoder: ImportError: cannot import name 'CTC' from 'funasr.models.ctc' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/models/ctc/__init__.py)
  - funasr.models.sond.sv_decoder: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.whisper.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.decoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.encoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.eres2net.simple_avg: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.xvector.e2e_sv: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.utils.speaker_utils: ModuleNotFoundError: No module named 'funasr.utils.modelscope_file'
11:52:35 | INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=ms
funasr version: 1.3.9.
Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512
Downloading (incomplete total...): 0.00B [00:01, ?B/s]
WARNING:root:trust_remote_code: True
Loading remote code failed: model, attempted relative import with no known parent package



Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
WARNING:root:trust_remote_code: False

11:53:00 | INFO     | Fun-ASR-Nano 模型加载成功 ✓
11:53:00 | INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=hf
funasr version: 1.3.9.
Fetching 21 files:   0%|                                 | 0/21 [00:00<?, ?it/s]
Download: FunAudioLLM/Fun-ASR-Nano-2512 failed!: An error happened while trying to locate the file on the Hub and we cannot find the requested files in the local cache. Please check your connection and try again or make sure your Internet connection is on.
WARNING:root:trust_remote_code: True
11:53:01 | WARNING  | Fun-ASR-Nano 模型加载失败 (hub=hf): model 'FunAudioLLM/Fun-ASR-Nano-2512' is not registered.
Registered model keys (48): BAT, BiCifParaformer, Branchformer, CAMPPlus, CTC, CTTransformer, CTTransformerStreaming, Conformer, ContextualParaformer, EBranchformer, EParaformer, ERes2NetV2, Emotion2vec, FsmnKWS, FsmnKWSConvert, FsmnKWSMT, FsmnKWSMTConvert, FsmnVADStreaming, FunASRNano, GLMASR, LCBNet, LLMASR, LLMASR2, LLMASR3, LLMASR4, LLMASRNAR, LLMASRNARPrompt, MonotonicAligner, OpenAIWhisperLIDModel, OpenAIWhisperModel, Paraformer, ParaformerStreaming, Paraformer_v2_community, Qwen/Qwen3-ASR-0.6B, Qwen/Qwen3-ASR-1.7B, Qwen3ASR, SANM, SCAMA, SanmKWS, SanmKWSStreaming, SeacoParaformer, SenseVoiceSmall, Transducer, Transformer, UniASR, ZhipuAI/GLM-ASR-Nano-2512, iic/speech_eres2netv2_sv_zh-cn_16k-common, zai-org/GLM-ASR-Nano-2512
Some modules may have failed to import during auto-registration. Set FUNASR_IMPORT_DEBUG=1 to print failures during import, or FUNASR_STRICT_IMPORT=1 to fail fast.
Recorded import failures:
  - funasr.bin._server_app: ImportError: funasr-server requires additional packages. Install with: pip install vllm fastapi uvicorn python-multipart
  - funasr.bin.train: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.bin.train_ds: ImportError: cannot import name 'AutoModel' from partially initialized module 'funasr' (most likely due to a circular import) (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/__init__.py)
  - funasr.frontends.default: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.fused: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.s3prl: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.dnn_wpe: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.frontends.utils.frontend: ModuleNotFoundError: No module named 'pytorch_wpe'
  - funasr.models.fun_asr_nano.tools.whisper_mix_normalize: ModuleNotFoundError: No module named 'pyopenjtalk'
  - funasr.models.language_model.rnn.decoders: ModuleNotFoundError: No module named 'funasr.models.transformer.utils.scorers'
  - funasr.models.language_model.seq_rnn_lm: ModuleNotFoundError: No module named 'funasr.train'
  - funasr.models.language_model.transformer_lm: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mfcca.e2e_asr_mfcca: ImportError: cannot import name 'ErrorCalculator' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.mfcca.mfcca_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.mossformer.e2e_ss: ModuleNotFoundError: No module named 'funasr.models.base_model'
  - funasr.models.mossformer.mossformer: ModuleNotFoundError: No module named 'einops'
  - funasr.models.mossformer.mossformer_encoder: ModuleNotFoundError: No module named 'einops'
  - funasr.models.qwen_audio.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.sa_asr.beam_search_sa_asr: ImportError: cannot import name 'end_detect' from 'funasr.metrics' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/metrics/__init__.py)
  - funasr.models.sa_asr.e2e_sa_asr: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.models.sense_voice.whisper_lib.normalizers: ModuleNotFoundError: No module named 'more_itertools'
  - funasr.models.sense_voice.whisper_lib.triton_ops: SyntaxError: invalid syntax (triton_ops.py, line 57)
  - funasr.models.sond.e2e_diar_sond: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.sond.encoder.conv_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.fsmn_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.resnet34_encoder: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.sond.encoder.self_attention_encoder: ImportError: cannot import name 'CTC' from 'funasr.models.ctc' (/home/fiend/WorkSpace/python/translator_intime/venv/lib/python3.12/site-packages/funasr/models/ctc/__init__.py)
  - funasr.models.sond.sv_decoder: ModuleNotFoundError: No module named 'funasr.models.decoder'
  - funasr.models.whisper.model: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.decoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.encoder: ModuleNotFoundError: No module named 'whisper'
  - funasr.models.whisper_lid.eres2net.simple_avg: ModuleNotFoundError: No module named 'funasr.models.encoder'
  - funasr.models.xvector.e2e_sv: ModuleNotFoundError: No module named 'funasr.layers'
  - funasr.utils.speaker_utils: ModuleNotFoundError: No module named 'funasr.utils.modelscope_file'
11:53:01 | INFO     | 加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=ms
funasr version: 1.3.9.
Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512
WARNING:root:trust_remote_code: True
Loading remote code failed: model, attempted relative import with no known parent package
Downloading (incomplete total...): 0.00B [00:02, ?B/s]
Downloading Model from https://www.modelscope.cn to directory: /home/fiend/.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
WARNING:root:trust_remote_code: False
11:53:27 | INFO     | Fun-ASR-Nano 模型加载成功 ✓


---

**Cursor**

从日志看，Fun-ASR-Nano 实际已经加载成功了，两次 `Fun-ASR-Nano 模型加载成功` 分别是中文 ASR 和外语 ASR 两个实例。现在主要问题是：它先试 HF 失败再试 ModelScope，日志很吓人；而且同一个大模型被加载了两遍。我会改成你的环境默认优先 ModelScope，并让两个 ASR 实例共享同一个本地模型。

我会把默认 hub 改回 `ms`，减少无效 HF 下载；同时给 ASR 引擎加一个 `share_model_from()`，让外语 ASR 直接复用中文 ASR 已加载的 FunASR 模型。

默认 hub 已改为 ModelScope。现在加模型共享方法。

现在改管道加载逻辑，让第二个 ASR 引擎复用第一个模型，不再重复下载/加载。

我检查一下语法和 lint。

这次其实已经成功了，关键行是：

```text
11:53:00 | INFO | Fun-ASR-Nano 模型加载成功 ✓
```

后面又加载第二次，是因为项目有两个本地 ASR 实例：

- 你的语言：`zh`
- 游戏外语：`en`

之前它会给两个实例各加载一次 FunASR，所以你看到第二轮 HF/MS 日志。

我已经优化了两点：

- 默认 `funasr_hub` 改回 `ms`，因为你这里 ModelScope 能成功，HF 连接失败。
- 第二个 ASR 实例会复用第一个已加载的 FunASR 模型，不再重复加载。

下次启动应该只看到一次：

```text
加载 FunASR 模型: FunAudioLLM/Fun-ASR-Nano-2512, device=cpu, hub=ms
Fun-ASR-Nano 模型加载成功 ✓
ASR 模型已复用（backend=funasr, language=en）
```

`Loading remote code failed...` 如果还出现但最终成功，可以先忽略；那是 FunASR 内部尝试远程代码失败后走了我们手动注册/本地注册路径。

---

**User**

11:56:50 | INFO     | 系统音频输入电平 rms=0.13680
rtf_avg: 0.062: 100%|█████████████████████████████| 1/1 [00:00<00:00, 48.86it/s]
  0%|                                                     | 0/1 [00:00<?, ?it/s]
rtf_avg: 0.040: 100%|█████████████████████████████| 1/1 [00:00<00:00, 72.12it/s]
  0%|                                                     | 0/1 [00:00<?, ?it/s]
  0%|                                                     | 0/1 [00:00<?, ?it/s]我音频都关了半天了，怎么还有输出？

---

**Cursor**

这看起来不是字幕输出，而是 FunASR/底层推理库的进度条在刷终端；同时程序可能还在对缓冲区里残留的音频片段做识别。我要把 FunASR 的进度条关掉，并在入站静音时避免继续触发空音频识别。

我确认了进度条来自 FunASR `generate()`，参数是 `disable_pbar=True`。另外本地入站逻辑在静音累计后会反复调用 `transcribe()`，即使缓冲只剩静音，也会触发进度条。我会同时修这两个点。

我会改两处：FunASR `generate(..., disable_pbar=True)`；`transcribe()` 预处理后如果能量太低直接丢弃，不进入 ASR 推理。

我还要避免入站静音时把静音 chunk 不断喂进 ASR 缓冲。当前代码在有声时喂，静音时会触发转写；这没错，但关闭音频后可能会对残留缓冲多次触发。加一个“只有本轮有过有效声音才在静音后 flush”的标志。

我也给出站管道加同样的标志，避免麦克风静音后重复触发空识别。

我继续给出站本地循环加 `had_voice`。

现在运行语法和 lint 检查。

这里有两件事：

1. `rtf_avg: ... 100%|...` 是 FunASR 的 tqdm 进度条，不是字幕输出。
2. 你关掉音频后，程序还可能在处理之前缓冲区里残留的一小段音频，所以会再跑几次 ASR。

我已经修了：

- FunASR 调用加了 `disable_pbar=True`，不再刷 `rtf_avg` 进度条。
- 本地入站/出站 ASR 加了 `had_voice` 标志，只有这一轮确实检测到过非静音，才会在静音后触发识别。
- `transcribe()` 预处理后如果是空音频或低于噪声门，直接返回，不进入 FunASR 推理。

检查已通过。重启后应该不会再出现关掉音频还不断刷进度条的情况。

---

**User**

还有个问题，字幕显示出来不是完整的句子，更像是一个个单词短语

---

**Cursor**

我会把本地 ASR 路径的分段策略调成“句子级输出”：减少过早 `transcribe_non_blocking()` 的短片段输出，并用更长静音判定后一次性识别一段话。先看当前本地出入站循环和缓冲参数。

是的，这个短语化主要来自本地管道还在“非阻塞流式识别”：有声时每积累一点音频就调用一次 ASR，所以 FunASR 还没等到完整句子就被切片识别了。我会改成本地 ASR 只在静音断句或超长兜底时识别。

我加了一个“超长兜底切分”判断。接下来把出站/入站循环里的实时短片段识别去掉，只在静音断句或超长时识别。

识别触发策略已改成句子级。现在跑语法和 lint 检查。

已修。短语化的原因是本地 ASR 管道还在做“非阻塞流式识别”，有声时缓存到很短一段就调用一次 ASR，所以字幕会变成词组/短语。

现在改成：

- 默认等检测到一段静音后再识别整句话。
- 如果连续说话太久，超过约 12 秒才兜底切分。
- 出站和入站都不再每 0.25/3 秒主动切小块识别。

这样会牺牲一点实时性，但字幕会更像完整句子。语法和 lint 都通过了，重启后再试。

---

**User**

[EN] 鸟飞的鸟，我飞的飞快的，蹲着的，像一个被囚禁的囚犯。
-> 鸟飞的鸟，我飞的飞快的 ，蹲着的 ，像一个被囚禁的囚犯。这不对吧，我播放的TED演讲，而且只有当我暂停才有输出，这句子切分还是有问题，大模型是怎么做的？

---

**Cursor**

你说得对：现在这个表现有两个问题混在一起了。第一，TED 英文被本地 FunASR 输出成中文，说明语言参数/模型行为不适合当前场景；第二，“暂停才输出”是我上次为了完整句子把本地 ASR 改成了静音断句模式。大模型实时字幕通常不是这么做，它会发 partial/final 两类结果：播放中持续更新同一行，句子结束后再定稿。我先做一个小修复：FunASR 现在把整份“游戏词库”都作为 hotwords 传进去，里面大量中文词会强烈干扰英文 TED 识别，这很可能导致 `[EN]` 原文也变成中文胡话。我会按识别语言过滤 hotwords：英文只给英文热词，中文才给中文热词；同时把连续语音兜底切分从 12 秒降到 6 秒，避免一直等暂停。

修复已完成。现在做语法和 lint 检查。

对，这不对。你播放的是英文 TED，但 `[EN]` 原文已经变成中文，说明问题发生在 ASR 阶段，不是翻译阶段。

我刚修了一个很可能的干扰源：我们之前把整份“游戏词库”都传给 FunASR 当 hotwords，里面大量中文词会污染英文识别。现在改成：

- 英文识别只传英文热词。
- 中文识别只传中文热词。
- 自动识别不传热词。
- 连续语音兜底切分从 12 秒降到 6 秒，减少“一直等暂停”。

关于“大模型怎么做”：

实时大模型字幕通常不是等完整暂停才输出，也不是把每个短片段 append 出来。它一般有两层结果：

- `partial`：播放中持续更新同一行，可能会改写前面的字。
- `final`：检测到句子结束或语义稳定后，把这一行定稿。

也就是说 UI 应该是“更新当前字幕行”，而不是“每个片段新增一行”。我们现在本地 FunASR 路径还是偏离线识别：等一段音频后推理，再输出结果。要做到大模型那种体验，下一步要做的是“临时字幕行 + final 定稿”机制，而不是继续 append。

---

**User**

我发现翻译准确度和语言性比较差，有其他好的方案吗？？

---

**Cursor**

有。按你这个场景，我建议不要把重点放在 `Fun-ASR-Nano + 免费文本翻译` 这条链路上，它现在更像“能跑”，但 TED/游戏语音这种连续口语场景体验不够稳。

更好的方案分三档：

### 1. 最推荐：继续用火山 AST 2.0 云端同传
如果目标是实时字幕和自然翻译，火山 AST 这类端到端同传模型更适合。它不是 `ASR -> 文本翻译` 的级联链路，而是语音理解后直接输出译文，口语断句、语义补全通常会好很多。

适合：你愿意用云端，追求可用体验。  
建议：把 `使用云端模型` 打开，后端选 `volc`，优先把火山链路做稳。

### 2. 本地高准确率：Whisper large-v3 / faster-whisper medium+
如果必须本地，当前 `Fun-ASR-Nano CPU` 未必适合实时字幕。建议回到 `faster-whisper`，但用更大的模型：

- `medium`：速度和准确率折中
- `large-v3`：准确率更好，但 CPU 会慢很多

适合：不想云端，机器性能还可以。  
缺点：仍然只是 ASR，后面文本翻译质量还要靠翻译模型。

### 3. 翻译质量更好：ASR + 大语言模型翻译
你现在用 `microsoft/google` 这类免费翻译，口语上下文和游戏语境会比较差。可以改用：

- OpenAI `gpt-4o-mini`
- 通义千问/Qwen API
- 火山方舟豆包模型
- 本地 Ollama Qwen2.5/3，但实时性看机器

这类模型可以带上下文提示词，比如“这是 TED 演讲字幕，保持自然中文，不逐词直译”。翻译自然度会明显好于传统机器翻译。

我建议路线：

```text
优先方案：火山 AST 2.0
备选本地：faster-whisper medium/large-v3 + LLM 翻译
不要优先：Fun-ASR-Nano CPU + Microsoft 翻译
```

如果你想继续优化当前项目，我建议下一步做“翻译后端加 LLM 口语润色/上下文翻译”，收益会比继续调 ASR 分段更明显。

---

**User**

现在的项目可以直接在windows上跑吗？

---

**Cursor**

现在这个项目**不能保证直接在 Windows 上跑**。GUI 和大部分 Python 逻辑可以跨平台，但音频路由这块目前明显偏 Linux/PulseAudio/PipeWire。

主要阻碍：

- 当前系统音频捕获用了 Linux 工具：`pactl`、`parec`、`translator_virtual_sink.monitor`。
- 虚拟音频设备创建逻辑 Linux 下用 `module-null-sink`，Windows 下只是提示安装 VB-Cable，并没有完整自动配置。
- 游戏/系统声音捕获在 Windows 需要 WASAPI loopback 或 VB-Cable `CABLE Output`，现在项目里没有像 `sayhey` 那样成熟的 Windows loopback 实现。
- `scripts/setup_windows.ps1` 只能做部分环境安装，不能补齐音频路由体验。

如果你想在 Windows 上跑，最小方案是：

1. 安装 VB-Cable。
2. 用 `CABLE Input` 作为系统/游戏输出。
3. 用 `CABLE Output` 作为工具的“游戏声音捕获”输入。
4. TTS 输出如果要进游戏语音，也要路由到对应虚拟设备。

但以当前实现看，Windows 设备枚举、loopback、fallback、啸叫检测都不够稳。  
如果目标是 Windows 使用体验，建议直接复用/迁移 `sayhey` 的音频设备层和 loopback 逻辑。

---

**User**

WASAPI loopback这个不可以用吗？

---

**Cursor**

可以用，而且 Windows 上**更推荐 WASAPI loopback**。

它的优势是：

- 不一定需要把游戏声音手动切到 VB-Cable。
- 可以直接捕获某个扬声器/耳机的系统输出。
- 比“虚拟声卡 monitor”更符合 Windows 音频模型。
- `sayhey` 里已经有类似实现，可以参考迁移。

但你当前项目还没接 WASAPI loopback。现在主要用的是 Linux 的：

```text
pactl / parec / translator_virtual_sink.monitor
```

Windows 上需要新增一套音频捕获实现，大概有两种方案：

### 方案 A：用 soundcard 库
比较简单，适合快速接入：

```python
import soundcard as sc

speaker = sc.default_speaker()
with sc.get_microphone(id=str(speaker.name), include_loopback=True).recorder(samplerate=16000) as mic:
    data = mic.record(numframes=1600)
```

这个就是 WASAPI loopback 捕获系统输出。

### 方案 B：用 sounddevice / PyAudio WASAPI
更底层、更麻烦，需要枚举 WASAPI host API 和 loopback 设备，兼容性要自己处理。

我建议用 **方案 A：soundcard**。工作量不算大，主要要改：

- `requirements.txt` 加 `soundcard`
- `src/audio/stream.py` 增加 Windows loopback 输入类/分支
- 设置界面里区分：
  - Linux: Pulse monitor
  - Windows: WASAPI loopback speaker
- `list_audio_devices()` 在 Windows 下列出可 loopback 的 speaker

粗略工作量：半天到一天能做出可用版；要做得像 `sayhey` 一样稳，可能 2-3 天。

---

**User**

我目前想做linux和windows双端的，所以windows也得适配，目前的具体问题出在哪里？

---

**Cursor**

我会基于当前代码做一次针对 Windows 适配的缺口梳理，定位具体卡点在哪些文件和设计上，而不是泛泛说“不支持”。

可以做 Linux + Windows 双端，但目前 Windows 的具体问题主要集中在**音频层**，不是 GUI 或 ASR/翻译模型。

## 具体问题在哪

### 1. 系统音频捕获是 Linux 方案
当前 `src/audio/stream.py` 里捕获系统输出主要靠：

```text
pactl
parec
translator_virtual_sink.monitor
```

这些都是 PulseAudio/PipeWire 方案，Windows 没有。

Windows 上应该走：

```text
WASAPI loopback
```

也就是捕获当前扬声器/耳机正在播放的声音，而不是 `monitor` 源。

### 2. 设备列表没有 Windows loopback 概念
当前 `list_audio_devices()` 主要列：

- PortAudio 输入设备
- PortAudio 输出设备
- Linux PulseAudio source/sink

但 Windows 系统输出不一定会作为普通输入设备出现。  
所以“游戏声音捕获”下拉框在 Windows 上可能看不到真正可捕获的系统输出。

需要新增：

```text
[Loopback] 扬声器 / 耳机 / CABLE Input
```

### 3. `VirtualAudioDevice` 的 Windows 实现只是提示
`src/audio/virtual_device.py` 里 Windows 分支只是：

```python
logger.info("Windows: 请确保已安装 VB-Cable")
return True
```

它没有：

- 检查 VB-Cable 是否安装
- 查找 `CABLE Input`
- 查找 `CABLE Output`
- 检测路由是否可用
- 避免啸叫/回环

所以 Windows 下“虚拟设备管理”基本还是空壳。

### 4. 当前输入流没有 WASAPI loopback 分支
`AudioStream.open_input()` 现在只有：

- 字符串设备名 -> Linux `parec`
- 普通设备 ID -> `sounddevice.InputStream`

Windows 需要新增类似：

```python
soundcard.get_microphone(speaker.name, include_loopback=True)
```

或者 PyAudio/sounddevice WASAPI loopback 分支。

### 5. setup_windows.ps1 只做到安装提示
`scripts/setup_windows.ps1` 目前只是：

- 创建 venv
- 安装 requirements
- 提醒安装 VB-Cable
- 列出 sounddevice 设备

它没有安装/配置/验证 Windows loopback 捕获。

## 推荐适配方案

最现实的方案是参考 `sayhey`：

- `sayhey/SayHey/app_core/system_audio.py`：已经用 `soundcard` 做 WASAPI loopback。
- `sayhey/SayHey/app_core/audio_devices.py`：已经有 Windows 设备解析、HostAPI fallback、VB-Cable 识别逻辑。

迁移到当前项目大概这样：

1. `requirements.txt` 加 `soundcard`
2. `src/audio/stream.py` 加 Windows loopback 输入类
3. `list_audio_devices()` Windows 下列出 speakers 作为 loopback 捕获源
4. 设置页里把“游戏声音捕获”区分为：
   - Linux: Pulse monitor
   - Windows: WASAPI loopback speaker
5. `VirtualAudioDevice` Windows 分支增加 VB-Cable 检测和提示
6. 可选：加啸叫检测，避免把 TTS 输出又捕回输入

## 难度判断

做一个 Windows 可用版：**半天到一天**。  
做一个比较稳的 Windows 版：**2-3 天**。  
做到接近 `sayhey` 的产品化音频层：**约一周**。

---

**User**

如果你来实现呢？

---

**Cursor**

如果我来实现，我会先做“可用且不破坏 Linux”的跨平台音频抽象：保留当前 Linux `parec` 路径，给 Windows 新增 `soundcard` WASAPI loopback 路径，然后再补设置页和安装脚本。这个改动涉及多个模块，我先制定一个简短计划再动手。

我已经看过核心音频文件。方案是最小侵入：`src/audio/stream.py` 内新增 Windows loopback 设备 ID 约定，比如 `wasapi_loopback:<speaker name>`，这样配置层仍能保存字符串，Linux 逻辑不受影响。

我开始改 `stream.py`：增加平台判断、Windows speaker 枚举、`soundcard` loopback 录音，并保留现有 Linux `parec` 捕获。

Windows loopback 捕获和设备枚举已加。接下来补依赖和设置页里的固定 Linux 虚拟项，避免 Windows 下显示 Linux 的 `translator_virtual_sink.monitor`。

我会在依赖里加入 `soundcard`，并让设置页只在非 Windows 时追加 Linux 虚拟 monitor/sink 项。

依赖和设置页已更新。接下来改 `setup_windows.ps1` 的使用说明：新增 WASAPI loopback 方式，VB-Cable 只作为 TTS 输出到游戏麦克风的可选项。

Windows 脚本说明已更新。现在我补一个诊断脚本，Windows 下可以列出 `soundcard` speaker 和 loopback ID，方便验证。

诊断脚本已加。最后我会更新 README 的 Windows 音频说明，避免继续写成只能用 VB-Cable。

实现和文档已经完成。现在运行语法/lint 检查，确保 Linux 现有路径没有被破坏。

语法和 lint 都通过了。我再确认默认配置里的 Linux 字符串不会影响 Windows；Windows 上用户重新保存设备后会写入 `wasapi_loopback:...`。

如果我来实现，我会先做“Windows 可用版”，不追求一次性做到 `sayhey` 那么完整。现在我已经把第一版 WASAPI loopback 接进去了。

完成的改动：

- `src/audio/stream.py`
  - 新增 Windows WASAPI loopback 设备枚举。
  - 新增设备 ID 格式：`wasapi_loopback:<speaker name>`。
  - Windows 下选择这个设备时，用 `soundcard` 捕获扬声器/耳机系统输出。
  - Linux 原有 `parec` / `translator_virtual_sink.monitor` 路径保留。

- `requirements.txt`
  - 新增 `soundcard>=0.4.3`。

- `src/gui/settings_dialog.py`
  - Windows 下不再额外塞 Linux 的 `translator_virtual_sink.monitor`。
  - 设备列表会显示 `[Loopback] ...` 作为游戏声音捕获选项。

- `scripts/setup_windows.ps1`
  - 改成推荐 WASAPI loopback 捕获系统声音。
  - VB-Cable 变成“需要把 TTS 送进游戏麦克风时”的可选项。

- `scripts/check_windows_audio.py`
  - 新增 Windows 音频诊断脚本，列出可用 loopback 设备。

- `README.md`
  - 更新 Windows 音频说明。

已验证：相关 Python 文件语法检查通过，lint 无错误。

Windows 上使用方式大概是：

```powershell
python -m pip install -r requirements.txt
python scripts/check_windows_audio.py
python run.py
```

然后在设置里：

- `麦克风`: 你的物理麦克风
- `游戏声音捕获`: 选择 `[Loopback] 你的扬声器/耳机`
- `TTS输出设备`: 默认扬声器；如果要把语音送进游戏麦克风，再选 VB-Cable 的 `CABLE Input`

这版先解决“Windows 能直接捕获系统输出声音”的核心问题。后续如果要更稳，再补设备 fallback、啸叫检测、VB-Cable 自动识别和路由自检。

---

**User**

写一个后续需要补充功能的设计文档

---

**Cursor**

我会把它写成项目内的 Markdown 设计文档，覆盖 Windows/Linux 双端、音频路由、云端/本地模型、字幕体验和稳定性这些后续要补的模块。先看一下项目里是否已有 docs 目录或类似文档结构。

项目里还没有 `docs/` 目录，我会新建一个 `docs/future-design.md`，作为后续功能补充设计文档。

目录已创建。我现在写设计文档，按“背景、现状、目标架构、模块设计、优先级路线、验收标准”组织，方便后续按阶段实现。

设计文档已经写入项目。我再快速检查一下文件内容结构，确认没有明显格式问题。

已写好设计文档：`docs/future-design.md`。

内容覆盖了后续需要补齐的核心方向：跨平台音频抽象、Windows WASAPI loopback、VB-Cable/虚拟麦克风输出、云端/本地模型策略、FunASR/Whisper 回退、partial/final 字幕模型、Overlay、热词术语、LLM 润色、诊断、测试计划和分阶段实施路线。

这是文档变更，没有运行代码测试。

---

**User**

zip打包命令

---

**Cursor**

在项目目录执行：

```bash
zip -r translator_intime.zip . -x "venv/*" "__pycache__/*" "*.pyc" ".git/*" "logs/*" "models/*"
```

如果想打包到上一级目录：

```bash
zip -r ../translator_intime.zip . -x "venv/*" "__pycache__/*" "*.pyc" ".git/*" "logs/*" "models/*"
```

如果需要把模型也一起打包，去掉 `"models/*"` 这一项即可。

---

**User**

我尝试了火山引擎大模型，速度和准确率基本达到要求了，现在我们来优化一下界面显示，首先，主界面仅仅保留配置项即可，游戏语音翻译做成像桌面歌词一样的形式，可以缩放，可以锁定，当然麦克风输入显示也可以做成同样的形式，可以选择仅显示游戏语音翻译或者仅显示麦克风输入，再添加一下是否需要转语音输出，最好吧麦克风和游戏语音选项分开，主界面显示风格仿照MAC风格

