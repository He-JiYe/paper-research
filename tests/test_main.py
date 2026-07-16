"""CLI 入口测试：参数解析和命令分发"""

import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestArgparse:
    """测试 argparse 参数解析"""

    def test_main_no_args(self, capsys):
        """无参数时打印帮助"""
        with patch.object(sys, "argv", ["paper-research"]):
            from src.main import main

            main()
            captured = capsys.readouterr()
            assert "usage:" in captured.out.lower() or "用法" in captured.out

    def test_main_with_args(self):
        """有参数时调用 dispatch"""
        with (
            patch.object(sys, "argv", ["paper-research", "status"]),
            patch("src.config.load_settings"),
            patch("src.config.get_db_path", return_value=":memory:"),
            patch("src.db.init_db"),
            patch("src.db.get_connection"),
            patch("src.commands.cmd_status") as mock_cmd,
        ):
            from src.main import main

            main()
            mock_cmd.assert_called_once()

    def _test_dispatch(self, command, cmd_func_name, extra_args=None):
        """辅助：测试 dispatch 正确分发到命令函数"""
        from src.main import dispatch

        base = {"command": command}
        if extra_args:
            base.update(extra_args)
        args = argparse.Namespace(**base)
        import contextlib

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("src.config.load_settings"))
            stack.enter_context(
                patch("src.config.get_db_path", return_value=":memory:")
            )
            stack.enter_context(patch("src.db.init_db"))
            stack.enter_context(patch("src.db.get_connection"))
            mock_cmd = stack.enter_context(patch(f"src.commands.{cmd_func_name}"))
            dispatch(args)
            mock_cmd.assert_called_once()

    def test_main_fetch(self):
        self._test_dispatch(
            "fetch", "cmd_fetch", {"keyword": None, "dry_run": False, "serve": False}
        )

    def test_main_fetch_with_args(self):
        self._test_dispatch(
            "fetch", "cmd_fetch", {"keyword": "ML", "dry_run": True, "serve": True}
        )

    def test_main_serve(self):
        self._test_dispatch("serve", "cmd_serve")

    def test_main_review(self):
        self._test_dispatch("review", "cmd_review", {"head": 10, "all": False})

    def test_main_mark(self):
        self._test_dispatch(
            "mark", "cmd_mark", {"arxiv_id": "2401.00001", "type": "deep-read"}
        )

    def test_main_note(self):
        self._test_dispatch("note", "cmd_note", {"arxiv_id": "2401.00001"})

    def test_main_list(self):
        self._test_dispatch(
            "list",
            "cmd_list",
            {
                "keyword": None,
                "status": None,
                "mark": None,
                "sort": "date",
                "head": 10,
                "all": False,
            },
        )

    def test_main_status(self):
        self._test_dispatch("status", "cmd_status")

    def test_main_notify(self):
        self._test_dispatch("notify", "cmd_notify")


class TestDispatchErrorHandling:
    def test_dispatch_closes_conn_on_success(self):
        """dispatch 在正常执行后关闭连接"""
        from src.main import dispatch

        mock_conn = MagicMock()
        args = argparse.Namespace(command="status")
        with (
            patch("src.config.load_settings"),
            patch("src.config.get_db_path", return_value=":memory:"),
            patch("src.db.init_db"),
            patch("src.db.get_connection", return_value=mock_conn),
            patch("src.commands.cmd_status"),
        ):
            dispatch(args)
            mock_conn.close.assert_called_once()

    def test_dispatch_closes_conn_on_error(self):
        """dispatch 在异常时也关闭连接"""
        from src.main import dispatch

        mock_conn = MagicMock()
        args = argparse.Namespace(command="status")
        with (
            patch("src.config.load_settings"),
            patch("src.config.get_db_path", return_value=":memory:"),
            patch("src.db.init_db"),
            patch("src.db.get_connection", return_value=mock_conn),
            patch("src.commands.cmd_status", side_effect=RuntimeError("test error")),
        ):
            with pytest.raises(RuntimeError):
                dispatch(args)
            mock_conn.close.assert_called_once()


class TestCommandLineParsing:
    """测试 argparse 参数解析行为"""

    def test_mark_all_argument_types(self):
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        mp = sub.add_parser("mark")
        mp.add_argument("arxiv_id")
        mp.add_argument(
            "--type",
            "-t",
            choices=["ignore", "skim", "deep-read", "lurk"],
            required=True,
        )

        for t in ["ignore", "skim", "deep-read", "lurk"]:
            ns = parser.parse_args(["mark", "2401.00001", "-t", t])
            assert ns.command == "mark"
            assert ns.arxiv_id == "2401.00001"

    def test_review_argument_group(self):
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        rp = sub.add_parser("review")
        g = rp.add_mutually_exclusive_group()
        g.add_argument("--head", "-n", type=int, default=10)
        g.add_argument("--all", "-a", action="store_true")

        ns = parser.parse_args(["review", "--head", "20"])
        assert ns.head == 20
        ns = parser.parse_args(["review", "--all"])
        assert ns.all is True
