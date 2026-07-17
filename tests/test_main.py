"""CLI 参数解析测试"""

import pytest
from unittest.mock import MagicMock, patch

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
        with patch("sys.argv", [
            "paper-research", "fetch", "-k", "test-time adaptation",
            "-m", "historical", "--max-results", "50",
        ]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                args = mock_dispatch.call_args[0][0]
                assert args.keyword == "test-time adaptation"
                assert args.mode == "historical"
                assert args.max_results == 50


class TestDispatch:
    """测试 dispatch 函数（命令分发）"""

    def test_dispatch_fetch(self):
        """dispatch 应调用 cmd_fetch"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_fetch") as mock_cmd:
                    args = MagicMock(command="fetch")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_serve(self):
        """dispatch 应调用 cmd_serve"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_serve") as mock_cmd:
                    args = MagicMock(command="serve")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_review(self):
        """dispatch 应调用 cmd_review"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_review") as mock_cmd:
                    args = MagicMock(command="review")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_mark(self):
        """dispatch 应调用 cmd_mark"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_mark") as mock_cmd:
                    args = MagicMock(command="mark")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_note(self):
        """dispatch 应调用 cmd_note"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_note") as mock_cmd:
                    args = MagicMock(command="note")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_list(self):
        """dispatch 应调用 cmd_list"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_list") as mock_cmd:
                    args = MagicMock(command="list")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_status(self):
        """dispatch 应调用 cmd_status"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_status") as mock_cmd:
                    args = MagicMock(command="status")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_notify(self):
        """dispatch 应调用 cmd_notify"""
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"), patch("src.db.get_connection"):
                with patch("src.commands.cmd_notify") as mock_cmd:
                    args = MagicMock(command="notify")
                    from src.main import dispatch
                    dispatch(args)
                    mock_cmd.assert_called_once()

    def test_dispatch_closes_conn(self):
        """dispatch 应在 finally 中关闭连接"""
        mock_conn = MagicMock()
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"):
                with patch("src.db.get_connection", return_value=mock_conn):
                    with patch("src.commands.cmd_fetch"):
                        args = MagicMock(command="fetch")
                        from src.main import dispatch
                        dispatch(args)
                        mock_conn.close.assert_called_once()


class TestMainEdgeCases:
    def test_main_no_command(self, capsys):
        """无命令时打印帮助信息且不触发 dispatch"""
        with patch("sys.argv", ["paper-research"]):
            with patch("src.main.dispatch") as mock_dispatch:
                main()
                mock_dispatch.assert_not_called()
                captured = capsys.readouterr()
                assert "usage:" in captured.out.lower() or captured.out

    def test_dispatch_exception_closes_conn(self):
        """dispatch 在命令抛出异常时仍关闭连接"""
        mock_conn = MagicMock()
        with patch("src.config.load_settings"), patch("src.config.get_db_path"):
            with patch("src.db.init_db"):
                with patch("src.db.get_connection", return_value=mock_conn):
                    with patch(
                        "src.commands.cmd_fetch", side_effect=Exception("test error")
                    ):
                        args = MagicMock(command="fetch")
                        from src.main import dispatch
                        with pytest.raises(Exception, match="test error"):
                            dispatch(args)
                        mock_conn.close.assert_called_once()
