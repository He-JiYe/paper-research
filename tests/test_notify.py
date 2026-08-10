"""EmailNotifier 测试"""

from unittest.mock import MagicMock, patch

from src.core.config import EmailConfig
from src.notify.sender import EmailNotifier


class TestEmailNotifier:
    def test_disabled_email_returns_false(self):
        """未启用时返回 False"""
        cfg = EmailConfig(enabled=False)
        notifier = EmailNotifier(cfg)
        result = notifier.send_fetch_report({}, [], [], server_url="http://127.0.0.1:8899")
        assert result is False

    def test_incomplete_config_returns_false(self):
        """SMTP 配置不完整时返回 False"""
        cfg = EmailConfig(enabled=True, smtp_host="", username="")
        notifier = EmailNotifier(cfg)
        result = notifier.send_fetch_report({}, [], [], server_url="http://127.0.0.1:8899")
        assert result is False

    @patch("smtplib.SMTP_SSL")
    def test_send_success(self, mock_smtp):
        """正常发送邮件"""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        cfg = EmailConfig(
            enabled=True,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="user@qq.com",
            password="pass",
            from_addr="user@qq.com",
            to_addr="user@qq.com",
        )
        notifier = EmailNotifier(cfg)
        result = notifier.send_fetch_report(
            {"new": 3, "updated": 1, "summarized": 3},
            [
                {
                    "arxiv_id": "2401.00001",
                    "title": "Test Paper",
                    "url": "https://arxiv.org/abs/2401.00001",
                    "authors": "Author",
                }
            ],
            ["test keyword"],
            server_url="http://127.0.0.1:8899",
        )
        assert result is True
        mock_smtp.assert_called_once_with("smtp.qq.com", 465)
        mock_server.send_message.assert_called_once()

    @patch("smtplib.SMTP_SSL")
    def test_send_failure_logged(self, mock_smtp):
        """发送失败时返回 False 不抛出异常"""
        mock_smtp.return_value.__enter__.return_value.send_message.side_effect = Exception(
            "SMTP error"
        )

        cfg = EmailConfig(
            enabled=True,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="user@qq.com",
            password="pass",
            from_addr="user@qq.com",
            to_addr="user@qq.com",
        )
        notifier = EmailNotifier(cfg)
        result = notifier.send_fetch_report(
            {"new": 1, "updated": 0, "summarized": 1},
            [
                {
                    "arxiv_id": "2401.00001",
                    "title": "Test",
                    "url": "https://arxiv.org/abs/2401.00001",
                    "authors": "",
                }
            ],
            ["kw"],
            server_url="http://127.0.0.1:8899",
        )
        assert result is False

    def test_empty_papers_list(self):
        """论文列表为空时应能正常构建邮件（跳过论文表格）"""
        cfg = EmailConfig(
            enabled=True,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="user@qq.com",
            password="pass",
            from_addr="user@qq.com",
            to_addr="user@qq.com",
        )
        notifier = EmailNotifier(cfg)
        with patch("smtplib.SMTP_SSL") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_server
            result = notifier.send_fetch_report(
                {"new": 0, "updated": 0, "summarized": 0},
                [],
                [],
                server_url="http://127.0.0.1:8899",
            )
            assert result is True
