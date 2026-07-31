"""Application entry point for Translator InTime."""

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.gui.main_window import MainWindow


def _resolve_resource(rel: str) -> str:
    """返回资源的绝对路径：PyInstaller 打包后走 sys._MEIPASS，否则走项目根。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / rel)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("声渡 SoundFerry")
    app.setOrganizationName("SoundFerry")

    # 设置全局图标（任务栏 + 窗口标题栏 + 对话框）
    icon_path = _resolve_resource("assets/app_icon.ico")
    if Path(icon_path).exists():
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    try:
        return app.exec()
    except KeyboardInterrupt:
        # Ctrl+C from a development terminal can arrive while Qt is executing
        # any Python callback. Exit cleanly instead of blaming that callback.
        app.quit()
        return 130


if __name__ == "__main__":
    sys.exit(main())
