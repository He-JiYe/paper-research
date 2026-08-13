"""CLI 参数解析测试"""

from unittest.mock import MagicMock, patch

import pytest
from src.main import main


class TestFetchArgs:
    """测试 fetch 命令参数解析"""

    def test_fetch_default_mode(self):
        """默认模式应为 incremental"""
        with patch("sys.argv", ["paper-research", "fetch"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.command == "fetch"
                assert args.mode == "incremental"

    def test_fetch_historical_mode(self):
        """--mode historical 应正确解析"""
        with patch("sys.argv", ["paper-research", "fetch", "--mode", "historical"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.command == "fetch"
                assert args.mode == "historical"

    def test_fetch_historical_short(self):
        """-m historical 短参数应正确解析"""
        with patch("sys.argv", ["paper-research", "fetch", "-m", "historical"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.mode == "historical"

    def test_fetch_invalid_mode(self):
        """非法的 mode 值应报错"""
        with patch("sys.argv", ["paper-research", "fetch", "--mode", "invalid"]):
            with pytest.raises(SystemExit):
                main()

    def test_fetch_max_results(self):
        """--max-results 应正确解析"""
        with patch("sys.argv", ["paper-research", "fetch", "--max-results", "100"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.max_results == 100

    def test_fetch_max_results_default(self):
        """未指定 --max-results 时默认应为 0（使用配置值）"""
        with patch("sys.argv", ["paper-research", "fetch"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.max_results == 0

    def test_fetch_mode_with_keyword(self):
        """--mode 和 --keyword 可以同时使用"""
        with patch(
            "sys.argv",
            [
                "paper-research",
                "fetch",
                "-k",
                "test-time adaptation",
                "-m",
                "historical",
                "--max-results",
                "50",
            ],
        ):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.keyword == "test-time adaptation"
                assert args.mode == "historical"
                assert args.max_results == 50


class TestServeArgs:
    """测试 serve 命令参数解析"""

    def test_serve_open_browser_flag(self):
        """--open-browser 应正确解析"""
        with patch("sys.argv", ["paper", "serve", "--open-browser"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.command == "serve"
                assert args.open_browser is True

    def test_serve_no_flag_default_false(self):
        """未指定 --open-browser 时默认为 False"""
        with patch("sys.argv", ["paper", "serve"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.command == "serve"
                assert args.open_browser is False


class TestAutostartArgs:
    """测试 autostart 命令参数解析"""

    def test_autostart_default_on(self):
        """未指定 action 时默认应为 on"""
        with patch("sys.argv", ["paper", "autostart"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.command == "autostart"
                assert args.action == "on"

    def test_autostart_off(self):
        """autostart off 应正确解析"""
        with patch("sys.argv", ["paper", "autostart", "off"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.action == "off"

    def test_autostart_invalid_action(self):
        """非法的 action 应报错"""
        with patch("sys.argv", ["paper", "autostart", "bogus"]):
            with pytest.raises(SystemExit):
                main()


class TestDispatch:
    """测试 dispatch 函数（命令分发）"""

    def test_dispatch_autostart(self):
        """dispatch 应调用 cmd_autostart 且不加载配置"""
        with (
            patch("src.commands.cmd_autostart") as mock_cmd,
            patch("src.config.loader.load_settings") as mock_load,
        ):
            args = MagicMock(command="autostart")
            from src.main import dispatch

            dispatch(args)
            mock_cmd.assert_called_once()
            mock_load.assert_not_called()  # autostart 不应依赖 config.yaml

    def test_dispatch_fetch(self):
        """dispatch 应调用 cmd_fetch"""
        with patch("src.config.loader.load_settings"):
            with patch("src.commands.cmd_fetch") as mock_cmd:
                args = MagicMock(command="fetch")
                from src.main import dispatch

                dispatch(args)
                mock_cmd.assert_called_once()

    def test_dispatch_serve(self):
        """dispatch 应调用 cmd_serve"""
        with patch("src.config.loader.load_settings"):
            with patch("src.commands.cmd_serve") as mock_cmd:
                args = MagicMock(command="serve")
                from src.main import dispatch

                dispatch(args)
                mock_cmd.assert_called_once()

    def test_dispatch_status(self):
        """dispatch 应调用 cmd_status"""
        with patch("src.config.loader.load_settings"):
            with patch("src.commands.cmd_status") as mock_cmd:
                args = MagicMock(command="status")
                from src.main import dispatch

                dispatch(args)
                mock_cmd.assert_called_once()

    def test_dispatch_notify(self):
        """dispatch 应调用 cmd_notify"""
        with patch("src.config.loader.load_settings"):
            with patch("src.commands.cmd_notify") as mock_cmd:
                args = MagicMock(command="notify")
                from src.main import dispatch

                dispatch(args)
                mock_cmd.assert_called_once()


class TestMainEdgeCases:
    def test_main_no_command(self, capsys):
        """无命令时打印帮助信息且不触发 dispatch"""
        with patch("sys.argv", ["paper-research"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                mock_dispatch.assert_not_called()
                captured = capsys.readouterr()
                assert "usage:" in captured.out.lower() or captured.out
