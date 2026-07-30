"""诊断 LiveCaptions 窗口的 UI Automation 控件树结构.

运行: python scripts/inspect_livecaptions.py
输出: 窗口下所有控件的层级结构、AutomationId、ControlType、Name、ClassName
"""

import sys
import time

import uiautomation as ua


def dump_control(ctrl, depth=0, max_depth=15):
    """递归打印控件树."""
    if ctrl is None or depth > max_depth:
        return

    try:
        aid = ctrl.AutomationId or ""
    except Exception:
        aid = ""
    try:
        ctype = ctrl.ControlTypeName or ""
    except Exception:
        ctype = ""
    try:
        name = (ctrl.Name or "")[:80]
    except Exception:
        name = ""
    try:
        cls = ctrl.ClassName or ""
    except Exception:
        cls = ""

    indent = "  " * depth
    print(f"{indent}[{ctype}] Aid='{aid}' Class='{cls}' Name='{name}'")

    try:
        children = ctrl.GetChildren()
    except Exception as e:
        print(f"{indent}  <GetChildren error: {e}>")
        return

    if children:
        for child in children:
            dump_control(child, depth + 1, max_depth)


def main():
    print("=== LiveCaptions 控件树诊断 ===\n")

    # 确认 LiveCaptions 进程是否在运行
    import subprocess

    result = subprocess.run(
        ["tasklist", "/fi", "imagename eq LiveCaptions.exe"],
        capture_output=True, text=True, timeout=5,
    )
    print("进程检查:")
    print(result.stdout.strip())
    print()

    # 查找 LiveCaptions 窗口
    print("查找 LiveCaptions 窗口 (ClassName=LiveCaptionsDesktopWindow)…")
    win = ua.WindowControl(
        ClassName="LiveCaptionsDesktopWindow",
        searchDepth=1,
        searchInterval=0.2,
    )
    if not win.Exists(5, 0.5):
        print("  未找到窗口! 尝试搜索所有顶级窗口…")
        root = ua.GetRootControl()
        for child in root.GetChildren():
            try:
                cls = child.ClassName or ""
                name = (child.Name or "")[:60]
                ctype = child.ControlTypeName or ""
                if "live" in cls.lower() or "caption" in cls.lower() or "live" in name.lower() or "caption" in name.lower():
                    print(f"  可能匹配: [{ctype}] Class='{cls}' Name='{name}'")
            except Exception:
                pass
        print("\n所有顶级窗口列表:")
        for child in root.GetChildren():
            try:
                cls = child.ClassName or ""
                name = (child.Name or "")[:60]
                print(f"  Class='{cls}' Name='{name}'")
            except Exception:
                pass
        return

    print(f"  已找到窗口! Name='{win.Name}'")
    try:
        hwnd = win.NativeWindowHandle
        print(f"  HWND={hwnd}")
    except Exception:
        pass
    print()

    # 打印完整控件树
    print("=== 控件树 ===")
    dump_control(win, max_depth=20)

    # 额外尝试: 全局搜索 AutomationId=CaptionsTextBlock
    print("\n=== 全局搜索 AutomationId=CaptionsTextBlock ===")
    text_ctrl = ua.TextControl(
        AutomationId="CaptionsTextBlock",
        searchDepth=30,
        searchInterval=0.5,
    )
    if text_ctrl.Exists(5, 0.5):
        print(f"  找到! Name='{text_ctrl.Name}'")
    else:
        print("  未找到")

    # 额外尝试: 搜索所有带 AutomationId 的控件
    print("\n=== 窗口内所有带 AutomationId 的控件 ===")
    def find_aided(ctrl, depth=0, max_depth=20):
        if ctrl is None or depth > max_depth:
            return
        try:
            aid = ctrl.AutomationId or ""
            if aid:
                ctype = ctrl.ControlTypeName or ""
                name = (ctrl.Name or "")[:60]
                cls = ctrl.ClassName or ""
                print(f"  {'  '*depth}[{ctype}] Aid='{aid}' Class='{cls}' Name='{name}'")
        except Exception:
            pass
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        if children:
            for child in children:
                find_aided(child, depth + 1, max_depth)

    find_aided(win)


if __name__ == "__main__":
    main()
