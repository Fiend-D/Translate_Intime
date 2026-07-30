"""Windows Live Captions ASR 后端.

通过 UI Automation (UIA) 捕获 Windows 系统实时字幕的识别文本.
- 微软系统级优化模型, 低占用高准确率, 接近辅助字幕水平
- 无需加载本地 ASR 模型, 不占 RAM/GPU
- 仅 Windows 11 22H2+ 可用

实现参考: LiveCaptions-Translator 项目 (github.com/sakirinn/LiveCaptions-Translator)
核心: 通过 uiautomation 库读取 LiveCaptions 窗口中 CaptionsTextBlock 元素的 Name 属性.
"""

from __future__ import annotations

import ctypes
import queue
import subprocess
import threading
import time
from typing import Any

from src.utils.logger import logger

# Windows Live Captions 标识
_LIVECAPTIONS_PROCESS = "LiveCaptions"
_LIVECAPTIONS_WINDOW_CLASS = "LiveCaptionsDesktopWindow"
_CAPTIONS_TEXTBLOCK_AUTOMATION_ID = "CaptionsTextBlock"
# LiveCaptions 等待音频时显示的控件 (非识别状态)
_READY_TEXTBLOCK_AUTOMATION_ID = "ReadyToCaptionTextBlock"

# 轮询间隔 (ms), 与 LiveCaptions-Translator 一致
_POLL_INTERVAL_MS = 50
# 启动超时 (秒)
_STARTUP_TIMEOUT = 30

# Win32 API 常量 (隐藏窗口用)
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_SW_MINIMIZE = 6


def _is_windows_11() -> bool:
    """检测是否为 Windows 11 (22H2+)."""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        # Windows 11 build >= 22000
        ver = sys.getwindowsversion()  # type: ignore[attr-defined]
        return ver.build >= 22000
    except Exception:
        return False


def _is_livecaptions_running() -> bool:
    """检查 LiveCaptions 进程是否已在运行."""
    try:
        result = subprocess.run(
            ["tasklist", "/fi", "imagename eq LiveCaptions.exe", "/nh"],
            capture_output=True, text=True, timeout=5,
        )
        return "LiveCaptions.exe" in result.stdout
    except Exception:
        return False


def _kill_livecaptions() -> None:
    """终止已有 LiveCaptions 进程."""
    try:
        subprocess.run(
            ["taskkill", "/f", "/im", "LiveCaptions.exe"],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def _hide_window(hwnd: int) -> None:
    """隐藏 LiveCaptions 窗口 (最小化 + 从任务栏移除), 但保持 UIA 可读."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.ShowWindow(hwnd, _SW_MINIMIZE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex_style | _WS_EX_TOOLWINDOW)
    except Exception as exc:
        logger.warning(f"隐藏 LiveCaptions 窗口失败: {exc}")


class LiveCaptionsAsr:
    """Windows Live Captions ASR 后端.

    - start(): 启动 LiveCaptions 进程 + UIA 轮询线程
    - recognize(): 返回轮询线程捕获的新文本 (从内部队列取出)
    - 后台轮询 LiveCaptions 窗口的 CaptionsTextBlock.Name 属性
    - 文本变化时, 计算增量 (新文本 - 上次文本), 将新增部分入队
    """

    def __init__(self, *, device_preference: str = "auto") -> None:
        self._device_preference = (device_preference or "auto").strip().lower()
        self._started = False
        self._ready = False
        self._failed = False
        self._loading = False

        # UIA 元素
        self._window: Any = None
        self._text_block: Any = None

        # 轮询线程
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 文本队列: recognize() 从这里取新文本
        self._text_queue: queue.Queue[str] = queue.Queue()
        self._last_full_text = ""
        # 按行去重: 已输出过的完整行文本集合 (防止 LiveCaptions 修订导致重复)
        self._emitted_lines: set[str] = set()
        # 最后一行 (正在输入的句子) 的缓冲状态
        self._pending_line = ""
        self._pending_since = 0.0

        # 平台检测
        self._platform_ok = _is_windows_11()

    # ---- 公开接口 ----

    @property
    def configured(self) -> bool:
        return self._ready

    @property
    def warming_up(self) -> bool:
        return self._loading and not self._ready

    @property
    def model_id(self) -> str:
        return "windows-live-captions"

    def start(self) -> None:
        self._started = True
        self.start_loading()

    def stop(self) -> None:
        self._started = False
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None
        self._window = None
        self._text_block = None
        self._ready = False
        self._loading = False
        self._last_full_text = ""
        self._emitted_lines.clear()
        self._pending_line = ""
        self._pending_since = 0.0

    def start_loading(self) -> None:
        """异步启动 LiveCaptions + UIA 轮询."""
        if self._ready or self._failed or self._loading:
            return
        if not self._platform_ok:
            logger.warning("Windows Live Captions 仅支持 Windows 11 22H2+")
            self._failed = True
            return

        self._loading = True
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="livecaptions-poll",
            daemon=True,
        )
        self._poll_thread.start()

    def recognize(self, pcm: bytes, *, language: str) -> str | None:
        """返回轮询线程捕获的新文本.

        PCM 参数被忽略 (Live Captions 自己捕获系统音频).
        由 engine 的 PCM 驱动循环触发调用, 每次调用取出一条新文本.
        """
        del pcm, language
        if not self._started or not self._ready:
            return None
        try:
            text = self._text_queue.get_nowait()
            return text or None
        except queue.Empty:
            return None

    # ---- 内部方法 ----

    def _poll_loop(self) -> None:
        """轮询 LiveCaptions 文本的主循环."""
        try:
            if not self._launch_and_find():
                self._failed = True
                self._loading = False
                return

            self._ready = True
            self._loading = False
            logger.info("Windows Live Captions ASR 已就绪")

            # 首次读取: 将已有文本标记为已输出, 避免上一次会话的残留字幕被入队
            stale = self._read_captions()
            if stale:
                for line in stale.split('\n'):
                    t = line.strip()
                    if t:
                        self._emitted_lines.add(t)
                self._last_full_text = stale
                logger.info("已跳过 LiveCaptions 残留字幕")

            while not self._stop_event.is_set():
                try:
                    text = self._read_captions()
                    if text and text != self._last_full_text:
                        self._process_text_change(text)
                        self._last_full_text = text
                    elif text and self._pending_line:
                        # 文本未变但有 pending 行: 超时后输出一次
                        import time as _time
                        if _time.time() - self._pending_since > 5.0:
                            if self._pending_line not in self._emitted_lines:
                                self._text_queue.put(self._pending_line)
                                self._emitted_lines.add(self._pending_line)
                            self._pending_line = ""
                except Exception as exc:
                    # 元素可能失效 (窗口被关闭), 重新定位
                    logger.warning(f"LiveCaptions 读取异常, 重新定位: {exc}")
                    self._text_block = None
                    self._window = None
                    if not self._relaunch_and_find():
                        logger.warning("LiveCaptions 重新定位失败, 等待重试")
                        time.sleep(2.0)

                self._stop_event.wait(_POLL_INTERVAL_MS / 1000.0)

        except Exception as exc:
            logger.warning(f"LiveCaptions 轮询线程异常: {exc}")
            self._failed = True
            self._loading = False

    def _launch_and_find(self) -> bool:
        """启动 LiveCaptions 进程并定位窗口和文本块.

        策略: 优先连接已运行的 LiveCaptions 实例, 避免杀进程后重启
        导致的长时间模型加载等待. 仅在未运行时启动新进程.
        """
        try:
            import uiautomation as ua
        except ImportError:
            logger.warning(
                "uiautomation 未安装，请执行: pip install uiautomation"
            )
            return False

        # 检查 LiveCaptions 是否已在运行
        already_running = _is_livecaptions_running()
        if already_running:
            logger.info("检测到 LiveCaptions 已在运行, 直接连接现有实例")
        else:
            logger.info("正在启动 Windows Live Captions…")
            try:
                subprocess.Popen([_LIVECAPTIONS_PROCESS])
            except Exception as exc:
                logger.warning(f"启动 LiveCaptions 失败: {exc}")
                return False

        # 等待窗口出现
        start = time.time()
        while time.time() - start < _STARTUP_TIMEOUT:
            if self._stop_event.is_set():
                return False
            win_ctrl = ua.WindowControl(
                ClassName=_LIVECAPTIONS_WINDOW_CLASS,
                searchDepth=1,
                searchInterval=0.2,
            )
            if win_ctrl.Exists(0, 0):
                self._window = win_ctrl
                break
            time.sleep(0.3)

        if self._window is None:
            logger.warning(
                f"未找到 LiveCaptions 窗口 (ClassName={_LIVECAPTIONS_WINDOW_CLASS}), "
                f"请确认 Windows 11 22H2+ 且已开启实时字幕功能"
            )
            return False

        # 关键: 先查找文本块, 再隐藏窗口
        # (窗口最小化后 UIA 无法遍历子控件树)
        if not self._find_text_block():
            return False

        # 找到文本块后再隐藏窗口 (最小化 + 从任务栏移除), 保持 UIA 可读
        try:
            hwnd = self._window.NativeWindowHandle
            if hwnd:
                _hide_window(int(hwnd))
        except Exception:
            pass

        return True

    def _find_text_block(self, *, max_retries: int = 30) -> bool:
        """查找 CaptionsTextBlock 元素 (带重试, 双策略).

        LiveCaptions 启动后先显示 ReadyToCaptionTextBlock (等待音频状态),
        检测到音频后才切换为 CaptionsTextBlock (识别状态).
        因此需要持续重试直到 CaptionsTextBlock 出现.

        策略 1 (主): 全局声明式搜索 TextControl(AutomationId=...)
        策略 2 (备): BFS 遍历窗口子树 GetChildren()
        """
        if self._window is None:
            return False

        import uiautomation as ua

        ready_warned = False

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                return False

            if attempt > 1:
                time.sleep(1.5)
                # 重新搜索窗口, 刷新控件引用
                try:
                    win_ctrl = ua.WindowControl(
                        ClassName=_LIVECAPTIONS_WINDOW_CLASS,
                        searchDepth=1,
                        searchInterval=0.2,
                    )
                    if win_ctrl.Exists(1, 0.2):
                        self._window = win_ctrl
                except Exception:
                    pass
                logger.info(f"重试查找 CaptionsTextBlock (第 {attempt}/{max_retries} 次)…")
            else:
                logger.info("正在查找 CaptionsTextBlock…")

            # ---- 策略 1: 全局声明式搜索 (从桌面根节点) ----
            try:
                text_ctrl = ua.TextControl(
                    AutomationId=_CAPTIONS_TEXTBLOCK_AUTOMATION_ID,
                    searchDepth=30,
                    searchInterval=0.5,
                )
                if text_ctrl.Exists(1, 0.2):
                    self._text_block = text_ctrl
                    logger.info("已找到 CaptionsTextBlock (全局搜索)")
                    return True
            except Exception as exc:
                logger.debug(f"全局搜索失败: {exc}")

            # ---- 策略 2: BFS 遍历窗口子树 ----
            found = self._bfs_find_text_block(self._window)
            if found is not None:
                self._text_block = found
                logger.info("已找到 CaptionsTextBlock (BFS 遍历)")
                return True

            # ---- 检测 ReadyToCaptionTextBlock (等待音频状态) ----
            if not ready_warned:
                try:
                    ready_ctrl = ua.TextControl(
                        AutomationId=_READY_TEXTBLOCK_AUTOMATION_ID,
                        searchDepth=30,
                        searchInterval=0.3,
                    )
                    if ready_ctrl.Exists(1, 0.2):
                        ready_name = ready_ctrl.Name or ""
                        logger.info(
                            f"LiveCaptions 处于等待状态 ({ready_name}), "
                            f"请播放音频以触发识别…"
                        )
                        ready_warned = True
                except Exception:
                    pass

        logger.warning(
            f"未找到 CaptionsTextBlock (AutomationId={_CAPTIONS_TEXTBLOCK_AUTOMATION_ID}), "
            f"LiveCaptions 可能尚未完成初始化或语言包未下载"
        )
        self._text_block = None
        return False

    def _bfs_find_text_block(self, root: Any, max_depth: int = 20) -> Any:
        """使用 GetChildren() 广度优先遍历控件树查找 CaptionsTextBlock.

        BFS 比 DFS 更适合 UIA 控件树:
        - 目标元素通常在较浅层级, BFS 能更快命中
        - GetChildren() 是 uiautomation 最基础稳定的 API, 避免了
          searchFromControl / GetFirstDescendantControl 的兼容性问题
        """
        if root is None:
            return None

        from collections import deque

        queue: deque[tuple[Any, int]] = deque([(root, 0)])

        while queue:
            control, depth = queue.popleft()
            if depth > max_depth:
                continue

            # 检查当前控件是否是目标
            try:
                aid = getattr(control, "AutomationId", None)
                if aid == _CAPTIONS_TEXTBLOCK_AUTOMATION_ID:
                    return control
            except Exception:
                pass

            # 获取子控件列表
            try:
                children = control.GetChildren()
            except Exception:
                continue

            if not children:
                continue

            for child in children:
                queue.append((child, depth + 1))

        return None

    def _relaunch_and_find(self) -> bool:
        """重新定位窗口和文本块 (不重启进程)."""
        try:
            import uiautomation as ua
        except ImportError:
            return False

        win_ctrl = ua.WindowControl(
            ClassName=_LIVECAPTIONS_WINDOW_CLASS,
            searchDepth=1,
            searchInterval=0.2,
        )
        if win_ctrl.Exists(2, 0.2):
            self._window = win_ctrl
            return self._find_text_block(max_retries=3)
        return False

    def _read_captions(self) -> str:
        """读取当前字幕文本 (CaptionsTextBlock.Name 属性)."""
        if self._text_block is None:
            return ""
        # LiveCaptions 的字幕文本存储在 UIA 元素的 Name 属性中
        # (不是 ValuePattern 或 TextPattern, 这是经验验证的)
        try:
            return self._text_block.Name or ""
        except Exception:
            # 元素失效时返回空, 下次循环会重新定位
            return ""

    def _process_text_change(self, full_text: str) -> None:
        """处理文本变化, 按行 (句子) 输出.

        LiveCaptions 在识别过程中会给中间结果加句号 (如 "And she." →
        "And she had." → "And she had to."), 因此不能用句末标点判断完成.

        策略: 最后一行永远不直接输出, 只作为 pending 缓冲.
        只有当新行出现把它"顶上去"成为非最后一行时才输出 (此时是最终版本).
        超时兜底: 文本停止变化 5 秒后, pending 行被输出一次 (由轮询循环处理).
        """
        if not full_text:
            return

        import time

        lines = full_text.split('\n')

        # ---- 完整行 (非最后一行): 最终版本, 逐行入队 ----
        for line in lines[:-1]:
            text = line.strip()
            if not text or text in self._emitted_lines:
                continue
            self._text_queue.put(text)
            self._emitted_lines.add(text)
            if len(self._emitted_lines) > 100:
                self._emitted_lines = set(list(self._emitted_lines)[-50:])

        # ---- 最后一行: 仅作为 pending 缓冲, 不直接输出 ----
        last = lines[-1].strip() if lines else ""
        if last:
            self._pending_line = last
            self._pending_since = time.time()
        else:
            self._pending_line = ""
