"""
logger.py —— demo07 统一日志配置

为什么用 logging 而不是 print()？
  1. 级别控制：DEBUG/INFO/WARNING/ERROR 可按需过滤，print 全量输出
  2. 时间戳：自带 asctime，无需手动 datetime.now()
  3. 模块名：每条日志显示来源（transport / graph / weather / main）
  4. 可扩展：后续可轻松接入文件日志、JSON 格式、ELK / Sentry 等

使用方式（在每个模块顶部）：
  from .logger import get_logger
  logger = get_logger(__name__)
  logger.info("...")
  logger.debug("...")
  logger.warning("...")

日志级别控制（环境变量）：
  LOG_LEVEL=DEBUG   → 显示所有日志（包括 API 原始参数、耗时细节）
  LOG_LEVEL=INFO    → 默认，显示主要流程节点
  LOG_LEVEL=WARNING → 只显示警告和错误
"""

import logging
import os
import sys


# ── ANSI 颜色代码 ─────────────────────────────────────────────────────────────
_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_CYAN    = "\033[36m"
_GREEN   = "\033[32m"
_YELLOW  = "\033[33m"
_RED     = "\033[31m"
_MAGENTA = "\033[35m"
_BLUE    = "\033[34m"
_WHITE   = "\033[37m"


class _PlainFormatter(logging.Formatter):
    """纯文本格式（非 TTY 环境），模块名只取最后一段"""

    LEVEL_LABELS = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO ",
        logging.WARNING:  "WARN ",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRIT ",
    }

    def format(self, record: logging.LogRecord) -> str:
        time_str = self.formatTime(record, "%H:%M:%S")
        label    = self.LEVEL_LABELS.get(record.levelno, record.levelname[:5])
        module   = record.name.split(".")[-1][:14]
        msg      = record.getMessage()
        exc      = ("\n" + self.formatException(record.exc_info)) if record.exc_info else ""
        return f"{time_str} [{label}] {module:<14}| {msg}{exc}"


class _ColorFormatter(logging.Formatter):
    """
    带 ANSI 颜色的日志格式化器。
    格式：HH:MM:SS [LEVEL] module_short | message
    """

    LEVEL_COLORS = {
        logging.DEBUG:    _CYAN,
        logging.INFO:     _GREEN,
        logging.WARNING:  _YELLOW,
        logging.ERROR:    _RED,
        logging.CRITICAL: _MAGENTA,
    }

    LEVEL_LABELS = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO ",
        logging.WARNING:  "WARN ",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRIT ",
    }

    def format(self, record: logging.LogRecord) -> str:
        # 时间
        time_str = self.formatTime(record, "%H:%M:%S")

        # 级别（带颜色）
        color = self.LEVEL_COLORS.get(record.levelno, "")
        label = self.LEVEL_LABELS.get(record.levelno, record.levelname[:5])
        level_str = f"{color}{_BOLD}[{label}]{_RESET}"

        # 模块名（取最后一段，截断到12字符，左对齐）
        module = record.name.split(".")[-1][:12]
        module_str = f"{_DIM}{module:<12}{_RESET}"

        # 消息体（ERROR 加红色）
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            msg = f"{_RED}{msg}{_RESET}"
        elif record.levelno == logging.WARNING:
            msg = f"{_YELLOW}{msg}{_RESET}"

        # 异常信息
        exc = ""
        if record.exc_info:
            exc = "\n" + self.formatException(record.exc_info)

        return f"{_DIM}{time_str}{_RESET} {level_str} {module_str}| {msg}{exc}"


def configure_logging() -> None:
    """
    配置全局日志系统，只需在应用启动时调用一次。
    重复调用是安全的（有 root handlers 存在时跳过）。
    """
    root = logging.getLogger()
    if root.handlers:
        return  # 已经配置过，跳过

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # 控制台 handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    # Windows 终端可能不支持 ANSI，自动降级为纯文本
    use_color = (
        sys.stdout.isatty()
        or os.getenv("FORCE_COLOR", "").lower() in ("1", "true", "yes")
    )
    if use_color:
        handler.setFormatter(_ColorFormatter())
    else:
        # 非 TTY（如 uvicorn 子进程）：纯文本格式，模块名截断到最后一段
        handler.setFormatter(_PlainFormatter())

    root.setLevel(level)
    root.addHandler(handler)

    # 抑制第三方库的噪声日志
    for noisy in ("httpx", "httpcore", "openai", "langchain", "langgraph"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 Logger。
    name 传入 __name__，日志会显示为最后一级模块名（如 transport, graph）。
    """
    configure_logging()
    return logging.getLogger(name)
