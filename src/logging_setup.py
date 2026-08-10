"""统一日志基建：每日文件 + 错误文件 + 控制台（幂等）

设计：
- ``setup_logging()`` 挂到 **ROOT logger**，所有模块的 ``logging.getLogger(__name__)`` 自动生效，
  无需每个模块各自配置 handler；
- 每日 ``log/daily/YYYY-MM-DD.log``（INFO+）——``DateFileHandler`` 按 emit 当日日期
  直接命名为日期文件，跨天自动切换；
- 单独 ``log/errors.log``（WARNING+ 追加）——集中关键错误/警告，避免被每日日志淹没；
- ``redirect_stdio_if_detached()`` 收敛 CLI 与 headless 的 stdio 重定向（pythonw 下 stdout/stderr 为 None）。
  ``serve-stdout.log`` 只承接重定向后的**裸 stdio**（print / traceback），结构化日志仍由 daily/errors 负责——
  console handler 仅在真实交互终端（``isatty()``）挂载，避免无控制台启动时日志重复写两份。
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from src.paths import LOG_DIR

_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


class DateFileHandler(logging.Handler):
    """按 emit 当日日期写 ``YYYY-MM-DD.log``，跨天自动切换文件流。

    线程安全由 ``Handler.handle()`` 内置的 handler 锁保证（emit 在 acquire 内调用）。
    """

    def __init__(self, log_dir: Path, level: int = logging.NOTSET):
        super().__init__(level)
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str | None = None
        self._stream = None

    def _rotate(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._current_date == today and self._stream is not None:
            return
        if self._stream is not None:
            self._stream.close()
        self._current_date = today
        self._stream = open(  # noqa: SIM115 — 句柄需存活整个进程，不能 with
            self._log_dir / f"{today}.log", "a", encoding="utf-8", errors="replace"
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._rotate()
            self._stream.write(self.format(record) + "\n")  # type: ignore[union-attr]
            self._stream.flush()  # type: ignore[union-attr]
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


def _mark(handler: logging.Handler) -> logging.Handler:
    """打标记，供 setup_logging 幂等识别本项目 handler。"""
    handler._paper_logging = True
    return handler


def setup_logging(
    log_dir: Path | str | None = None,
    *,
    level: int = logging.INFO,
    console: bool = True,
    force: bool = False,
) -> None:
    """幂等配置根 logger：每日文件 + errors.log + 控制台。

    Args:
        log_dir: 日志根目录（默认 ``LOG_DIR``，含 daily/ 子目录）。
        level: 主日志级别（每日文件/控制台）。
        console: 交互终端是否回显控制台。
        force: 测试注入临时目录时置 True（清掉旧的本项目 handler 后重建）。
    """
    if log_dir is None:
        log_dir = LOG_DIR
    log_dir = Path(log_dir)
    daily_dir = log_dir / "daily"

    root = logging.getLogger()
    root.setLevel(logging.NOTSET)  # 让各 handler 按自己的 level 过滤

    if force:
        for h in list(root.handlers):
            if getattr(h, "_paper_logging", False):
                root.removeHandler(h)
    elif any(getattr(h, "_paper_logging", False) for h in root.handlers):
        return  # 幂等：已配置过

    fmt = logging.Formatter(_FORMAT, datefmt=_DATE_FMT)

    daily = DateFileHandler(daily_dir)
    daily.setLevel(level)
    daily.setFormatter(fmt)
    root.addHandler(_mark(daily))

    errors = logging.FileHandler(log_dir / "errors.log", encoding="utf-8", mode="a")
    errors.setLevel(logging.WARNING)
    errors.setFormatter(fmt)
    root.addHandler(_mark(errors))

    if console and _stdout_is_tty():
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(level)
        stream.setFormatter(fmt)
        root.addHandler(_mark(stream))

    # uvicorn 的 access 日志较吵，降噪（保留 error/warning）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _stdout_is_tty() -> bool:
    """是否挂了真实交互终端。pythonw/后台任务下 stdout 为 None 或已重定向为文件，
    此时不应挂 console handler（否则日志重复写一份 serve-stdout.log），用 isatty() 判断。"""
    if sys.stdout is None:
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def redirect_stdio_if_detached(log_dir: Path | str | None = None) -> None:
    """无控制台环境（pythonw.exe / 后台任务）下把 stdout/stderr 重定向到日志文件。

    收敛自 ``src/commands.py`` 与 ``serve_headless.py`` 的两份重复实现：
    - 优先 ``serve-stdout.log``；被占用时按 PID 命名，避免启动即崩。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    if log_dir is None:
        log_dir = LOG_DIR
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(  # noqa: SIM115 — 句柄需存活整个进程，不能 with
            log_dir / "serve-stdout.log", "a", encoding="utf-8", errors="replace", buffering=1
        )
    except OSError:
        fh = open(  # noqa: SIM115 — 句柄需存活整个进程，不能 with
            log_dir / f"serve-stdout-{os.getpid()}.log",
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh
