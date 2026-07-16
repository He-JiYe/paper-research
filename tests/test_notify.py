"""通知模块测试"""

from unittest.mock import MagicMock, patch

from src.notify import send_email_if_configured, send_windows_toast


class TestSendWindowsToast:
    def test_send_toast_basic(self):
        """send_windows_toast 基本调用"""
        with patch("src.notify.toast") as mock_toast:
            send_windows_toast("Title", "Message")
            mock_toast.assert_called_once()
            args, _kwargs = mock_toast.call_args
            assert "Title" in args
            assert "Message" in args

    def test_send_toast_with_keywords(self):
        """send_windows_toast 包含关键词和 URL"""
        keywords = [{"keyword": "ML", "count": 3}, {"keyword": "CV", "count": 5}]
        with patch("src.notify.toast") as mock_toast:
            send_windows_toast(
                "Title", "Body", keywords=keywords, url="http://localhost"
            )
            _args, kwargs = mock_toast.call_args
            assert kwargs["buttons"] is not None
            assert len(kwargs["buttons"]) == 1
            assert kwargs["buttons"][0]["arguments"] == "http://localhost"

    def test_send_toast_exception(self, capsys):
        """toast 失败时打印错误"""
        with patch("src.notify.toast", side_effect=Exception("no win11")):
            send_windows_toast("Title", "Message")  # 不应抛出异常
            captured = capsys.readouterr()
            assert "Toast notification failed" in captured.out


class TestSendEmailIfConfigured:
    def test_email_not_enabled(self, mock_settings):
        """email 未启用时返回 False"""
        mock_settings.notification.email.enabled = False
        result = send_email_if_configured(
            mock_settings, {"important": 0}, "http://localhost"
        )
        assert result is False

    def test_email_config_incomplete(self, mock_settings):
        """SMTP 配置不完整时跳过"""
        mock_settings.notification.email.enabled = True
        mock_settings.notification.email.smtp_host = ""
        result = send_email_if_configured(mock_settings, {}, "http://localhost")
        assert result is False

    def test_email_send_success(self, mock_settings):
        """成功发送邮件"""
        mock_settings.notification.email.enabled = True
        mock_settings.notification.email.smtp_host = "smtp.test.com"
        mock_settings.notification.email.smtp_port = 465
        mock_settings.notification.email.username = "user@test.com"
        mock_settings.notification.email.password = "pass"
        mock_settings.notification.email.from_addr = "from@test.com"
        mock_settings.notification.email.to_addr = "to@test.com"

        stats = {"important": 3, "useful": 5, "summarized_pending": 10}

        with patch("smtplib.SMTP_SSL") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance

            result = send_email_if_configured(
                mock_settings, stats, "http://localhost:8899"
            )
            assert result is True
            mock_instance.login.assert_called_once_with("user@test.com", "pass")
            mock_instance.send_message.assert_called_once()

    def test_email_send_with_keywords(self, mock_settings):
        """包含关键词信息的邮件"""
        mock_settings.notification.email.enabled = True
        mock_settings.notification.email.smtp_host = "smtp.test.com"
        mock_settings.notification.email.smtp_port = 465
        mock_settings.notification.email.username = "u@t.com"
        mock_settings.notification.email.password = "p"
        mock_settings.notification.email.from_addr = "f@t.com"
        mock_settings.notification.email.to_addr = "t@t.com"

        keywords = [{"keyword": "ML", "count": 3}]

        with patch("smtplib.SMTP_SSL") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance
            result = send_email_if_configured(
                mock_settings,
                {"important": 1, "useful": 2, "summarized_pending": 5},
                "http://localhost",
                keywords=keywords,
            )
            assert result is True

    def test_email_send_failure(self, mock_settings, capsys):
        """发送失败时打印错误"""
        mock_settings.notification.email.enabled = True
        mock_settings.notification.email.smtp_host = "smtp.test.com"
        mock_settings.notification.email.smtp_port = 465
        mock_settings.notification.email.username = "user"
        mock_settings.notification.email.password = "pass"
        mock_settings.notification.email.from_addr = "f@t.com"
        mock_settings.notification.email.to_addr = "t@t.com"

        with patch("smtplib.SMTP_SSL", side_effect=Exception("Connection refused")):
            result = send_email_if_configured(mock_settings, {}, "http://localhost")
            assert result is False
            captured = capsys.readouterr()
            assert "Email send failed" in captured.out
