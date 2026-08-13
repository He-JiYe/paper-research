"""CLI 命令实现测试（外部依赖全 mock，不触网）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.core.models import KeywordItem


@pytest.fixture
def settings():
    return SimpleNamespace(
        server=SimpleNamespace(host="127.0.0.1", port=8899),
        keywords=[KeywordItem(keyword="RL", active=True)],
        notification=SimpleNamespace(enabled=False),
        zotero=SimpleNamespace(api_key="k", library_id="1", library_type="user"),
    )


def test_cmd_fetch_dry_run(settings):
    args = SimpleNamespace(keyword=None, max_results=0, mode="incremental", dry_run=True)
    with (
        patch("src.db.PaperDB"),
        patch("src.pipeline.fetch.run_fetch_pipeline", new_callable=AsyncMock) as pipe,
        patch("src.config.settings.get_active_keywords", return_value=[settings.keywords[0]]),
    ):
        from src.commands import cmd_fetch

        cmd_fetch(args, settings)
    pipe.assert_awaited_once()


def test_cmd_fetch_keyword_not_found(settings, capsys):
    args = SimpleNamespace(keyword="NOPE", max_results=0, mode="incremental", dry_run=False)
    with (
        patch("src.db.PaperDB"),
        patch("src.config.settings.get_active_keywords", return_value=[settings.keywords[0]]),
    ):
        from src.commands import cmd_fetch

        cmd_fetch(args, settings)
    assert "未找到关键词" in capsys.readouterr().out


def test_cmd_status_renders(settings):
    db = MagicMock()
    db.get_stats.return_value = {
        "total": 10,
        "pending": 3,
        "by_mark": {"ignore": 1},
        "by_remark": {"important": 2, "useful": 1},
        "by_keyword": {"RL": 5},
    }
    db.get_recent_logs.return_value = [
        {
            "status": "success",
            "run_time": "2026-08-12 08:30:00",
            "papers_fetched": 5,
            "papers_new": 2,
        }
    ]
    with patch("src.db.PaperDB", return_value=db):
        from src.commands import cmd_status

        cmd_status(None, settings)  # 不抛错即通过


def test_cmd_notify_no_pending(settings):
    db = MagicMock()
    db.get_pending.return_value = []
    with (
        patch("src.db.PaperDB", return_value=db),
        patch(
            "src.notify.report.send_fetch_report",
            return_value={"sent": False, "reason": "no pending papers"},
        ) as send,
    ):
        from src.commands import cmd_notify

        cmd_notify(None, settings)
    send.assert_called_once()


def test_cmd_serve_delegates(settings):
    with (
        patch("src.logging_setup.redirect_stdio_if_detached"),
        patch("src.serve.run_server") as run,
    ):
        from src.commands import cmd_serve

        cmd_serve(None, settings)
    run.assert_called_once_with(settings)


def test_cmd_serve_open_browser_only(settings):
    """--open-browser 时仅打开浏览器，不启动服务"""
    args = SimpleNamespace(open_browser=True)
    with (
        patch("src.commands.webbrowser.open") as mock_open,
        patch("src.serve.run_server") as run,
    ):
        from src.commands import cmd_serve

        cmd_serve(args, settings)
    mock_open.assert_called_once()
    run.assert_not_called()


def test_cmd_serve_open_browser_url(settings, capsys):
    """--open-browser 打印服务地址（host:port）"""
    args = SimpleNamespace(open_browser=True)
    with patch("src.commands.webbrowser.open"):
        from src.commands import cmd_serve

        cmd_serve(args, settings)
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8899" in out


def test_is_admin_returns_bool():
    """_is_admin 应返回 bool（Windows 下 ctypes 检测可用）"""
    from src.commands import _is_admin

    assert isinstance(_is_admin(), bool)


def test_cmd_autostart_non_admin_prints_command(capsys):
    """非管理员且 action=on 时打印手动命令，不调用 subprocess"""
    args = SimpleNamespace(action="on")
    with (
        patch("src.commands._is_admin", return_value=False),
        patch("subprocess.run") as mock_run,
    ):
        from src.commands import cmd_autostart

        cmd_autostart(args)
    out = capsys.readouterr().out
    assert "需要管理员权限" in out
    assert "register_task.ps1 -Mode startup" in out
    mock_run.assert_not_called()


def test_cmd_autostart_non_admin_off_prints_command(capsys):
    """非管理员且 action=off 时同样打印手动命令"""
    args = SimpleNamespace(action="off")
    with (
        patch("src.commands._is_admin", return_value=False),
        patch("subprocess.run") as mock_run,
    ):
        from src.commands import cmd_autostart

        cmd_autostart(args)
    out = capsys.readouterr().out
    assert "register_task.ps1 -Action unregister" in out
    mock_run.assert_not_called()


def test_cmd_autostart_status_no_admin_runs():
    """status 普通用户可直接执行（不要求管理员）"""
    args = SimpleNamespace(action="status")
    with (
        patch("src.commands._is_admin", return_value=False),
        patch("subprocess.run") as mock_run,
    ):
        from src.commands import cmd_autostart

        cmd_autostart(args)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "register_task.ps1" in str(cmd)
    assert "-Action" in str(cmd) and "status" in str(cmd)


def test_cmd_autostart_admin_runs(capsys):
    """管理员且 action=off 时直接调用 subprocess 卸载计划任务"""
    args = SimpleNamespace(action="off")
    with (
        patch("src.commands._is_admin", return_value=True),
        patch("subprocess.run") as mock_run,
    ):
        from src.commands import cmd_autostart

        cmd_autostart(args)
    out = capsys.readouterr().out
    assert "需要管理员权限" not in out
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-Action" in str(cmd) and "unregister" in str(cmd)
