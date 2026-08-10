"""统一日志基建测试：DateFileHandler 跨日、setup_logging 幂等、stdio 重定向。"""

import datetime
import logging
import sys

from src.logging_setup import (
    DateFileHandler,
    redirect_stdio_if_detached,
    setup_logging,
)


def test_date_file_handler_writes_dated_file(tmp_path):
    handler = DateFileHandler(tmp_path)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.dfh")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.info("hello")
    handler.close()
    files = list(tmp_path.glob("*.log"))
    assert len(files) == 1
    assert files[0].name == f"{datetime.date.today().isoformat()}.log"  # 文件名按当日日期


def _paper_handlers():
    return [h for h in logging.getLogger().handlers if getattr(h, "_paper_logging", False)]


def test_setup_logging_idempotent(tmp_path):
    setup_logging(tmp_path, console=False, force=True)
    count = len(_paper_handlers())
    setup_logging(tmp_path, console=False)  # 幂等：不重复挂 handler
    assert len(_paper_handlers()) == count


def test_setup_logging_force_rebuilds(tmp_path):
    setup_logging(tmp_path, console=False, force=True)
    setup_logging(tmp_path, console=False, force=True)
    # force 清掉旧的本项目 handler 再重建（数量仍为每日+错误两个）
    assert len(_paper_handlers()) == 2  # daily + errors


def test_setup_logging_writes_errors_file(tmp_path):
    setup_logging(tmp_path, console=False, force=True)
    logging.getLogger("test.err").warning("关键警告")
    errors = tmp_path / "errors.log"
    assert errors.exists()
    assert "关键警告" in errors.read_text(encoding="utf-8")


def test_redirect_stdio_when_detached(tmp_path, monkeypatch):
    saved = (sys.stdout, sys.stderr)

    class _Null:
        pass

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", _Null())  # 仅 stdout 为 None
    redirect_stdio_if_detached(tmp_path)
    assert sys.stdout is not None  # 已重定向
    assert (tmp_path / "serve-stdout.log").exists()
    monkeypatch.setattr(sys, "stdout", saved[0])
    monkeypatch.setattr(sys, "stderr", saved[1])


def test_redirect_stdio_when_attached(tmp_path):
    # stdout/stderr 都非 None → 不重定向
    before = sys.stdout
    redirect_stdio_if_detached(tmp_path)
    assert sys.stdout is before


def test_console_handler_only_on_tty(tmp_path, monkeypatch):
    """console handler 仅在真实交互终端（isatty）挂载，无控制台启动不重复写日志。"""
    class _FakeStream:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    # 终端场景：console=True 时挂 console handler（daily+errors+console = 3 个）
    setup_logging(tmp_path, console=True, force=True)
    monkeypatch.setattr(sys, "stdout", _FakeStream(tty=True))
    setup_logging(tmp_path, console=True, force=True)
    assert len(_paper_handlers()) == 3  # daily + errors + console

    # 无控制台场景：stdout 非 None 但非 tty（如重定向到 serve-stdout.log）→ 不挂 console handler
    monkeypatch.setattr(sys, "stdout", _FakeStream(tty=False))
    setup_logging(tmp_path, console=True, force=True)
    assert len(_paper_handlers()) == 2  # daily + errors
