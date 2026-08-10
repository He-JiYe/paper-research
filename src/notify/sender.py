"""SMTP 邮件发送：EmailNotifier 只负责组装 MIME 并传输，内容生成委托 renderer。

调用方（CLI notify / 调度器）均以 ``EmailNotifier(config).send_fetch_report(...)``
使用本模块。
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.core.config import EmailConfig
from src.notify.renderer import render_email_report

logger = logging.getLogger(__name__)


class EmailNotifier:
    """邮件通知器 —— 抓取完成后发送报告邮件。"""

    def __init__(self, config: EmailConfig):
        self.config = config

    def send_fetch_report(
        self,
        stats: dict,
        new_papers: list[dict],
        keywords: list[str],
        server_url: str,
        top_n: int = 3,
    ) -> bool:
        """发送简洁邮件：统计摘要 + Top 3 论文 + 审阅页面链接。

        Args:
            stats: 统计信息
            new_papers: 新论文列表（取 Top 3）
            keywords: 关键词列表
            server_url: Web 审阅服务地址
            top_n: 展示前 n 条

        Returns:
            是否成功发送
        """
        cfg = self.config
        if not cfg.enabled:
            return False
        if not cfg.smtp_host or not cfg.username:
            logger.warning("Email enabled but SMTP config incomplete, skipping")
            return False

        try:
            content = render_email_report(stats, new_papers, keywords, server_url, top_n)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = content.subject
            msg["From"] = cfg.from_addr
            msg["To"] = cfg.to_addr
            msg.attach(MIMEText(content.html, "html", "utf-8"))
            msg.attach(MIMEText(content.text, "plain", "utf-8"))

            with self._smtp(cfg) as server:
                server.login(cfg.username, cfg.password)
                server.send_message(msg)
            logger.info("邮件通知发送成功")
            return True
        except Exception as e:
            logger.warning("邮件发送失败: %s", e)
            return False

    @staticmethod
    def _smtp(cfg):
        """按端口选择传输：465 用 SSL（SMTP_SSL），其余端口用 SMTP + STARTTLS。

        常见 QQ/163 的 465 走 SSL；Gmail 等 587 走 STARTTLS，两者都支持。
        """
        if cfg.smtp_port == 465:
            return smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port)
        server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()
        return server
