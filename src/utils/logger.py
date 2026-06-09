"""
日志模块 - 统一日志管理
"""
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_dir: Path = Path("logs"), level: str = "INFO") -> None:
    """配置全局logger"""
    logger.remove()  # 移除默认handler

    # 控制台输出
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=level,
        colorize=True,
    )

    # 文件输出
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "translator_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level="DEBUG",
    )

    logger.info("日志系统初始化完成")


__all__ = ["logger", "setup_logger"]
