"""
日志模块
- setup_console_logger: 初始化控制台日志（所有设备共享）
- get_device_logger: 为每台设备创建独立日志文件（同时输出到控制台）
- 日志格式：时间 | 级别 | 消息
"""

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_console_handler = None
_initialized_loggers: set[str] = set()


def setup_console_logger(level: int = logging.INFO) -> None:
    """初始化控制台日志处理器（只创建一次）"""
    global _console_handler
    if _console_handler:
        return
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root = logging.getLogger("switch_patcher")
    root.setLevel(level)
    root.addHandler(_console_handler)


def get_device_logger(hostname: str, run_id: str, logs_dir: str) -> logging.Logger:
    """
    获取设备专属日志记录器
    - 日志文件名：{hostname}_{run_id}.log
    - 日志同时写入文件和控制台（propagate=True）
    - 同一台设备同一次执行只创建一次
    """
    logger_name = f"switch_patcher.device.{hostname}"
    if logger_name in _initialized_loggers:
        return logging.getLogger(logger_name)

    logs_path = Path(logs_dir)
    logs_path.mkdir(exist_ok=True)

    fh = logging.FileHandler(logs_path / f"{hostname}_{run_id}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

    lg = logging.getLogger(logger_name)
    lg.setLevel(logging.DEBUG)
    lg.addHandler(fh)
    lg.propagate = True  # 同时输出到根logger（控制台）

    _initialized_loggers.add(logger_name)
    return lg
