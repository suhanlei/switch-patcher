import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_console_handler = None
_initialized_loggers: set[str] = set()


def setup_console_logger(level: int = logging.INFO) -> None:
    global _console_handler
    if _console_handler:
        return
    _console_handler = logging.StreamHandler(sys.stdout)
    _console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root = logging.getLogger("switch_patcher")
    root.setLevel(level)
    root.addHandler(_console_handler)


def get_device_logger(hostname: str, run_id: str, logs_dir: str) -> logging.Logger:
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
    lg.propagate = True  # also emit to root (console)

    _initialized_loggers.add(logger_name)
    return lg
