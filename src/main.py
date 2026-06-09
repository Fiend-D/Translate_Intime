"""
应用入口 - Translator InTime
"""
import os
import sys
import signal
from pathlib import Path

# ---- 启动时清理不兼容的系统代理 ----
# 多个底层库(httpx/aiohttp/requests/huggingface)会自动读取 *_PROXY 环境变量
# 但都不支持 socks:// scheme，会导致崩溃。统一清除 socks 代理。
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")
for _pv in _PROXY_VARS:
    _val = os.environ.get(_pv, "")
    if _val.startswith("socks"):
        os.environ.pop(_pv, None)
        print(f"[main] 已忽略系统 socks 代理: {_pv}={_val}")

from dotenv import load_dotenv
load_dotenv()  # 加载 .env 文件中的环境变量

# ---- HuggingFace 国内加速 ----
# 未设置 HF_TOKEN / HF_ENDPOINT 时，自动用国内镜像
if not os.environ.get("HF_TOKEN") and not os.environ.get("HF_ENDPOINT"):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from src.utils.logger import setup_logger, logger
from src.utils.config import ConfigManager
from src.gui.main_window import MainWindow


def main() -> None:
    """应用主入口"""
    # 初始化日志
    setup_logger(level="INFO")
    logger.info("=" * 60)
    logger.info("Translator InTime v0.1.0 启动")
    logger.info("实时游戏语音双向翻译系统")
    logger.info("=" * 60)

    # 加载配置
    config_mgr = ConfigManager()
    config = config_mgr.config

    # 创建 Qt 应用
    app = QApplication(sys.argv)
    app.setApplicationName("Translator InTime")
    app.setApplicationVersion("0.1.0")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出，最小化到托盘

    # 信号处理（优雅退出）
    def cleanup(*args):
        logger.info("收到退出信号，正在清理...")
        app.quit()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 创建并显示主窗口
    window = MainWindow(config)
    window.show()

    logger.info("GUI 已启动")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
