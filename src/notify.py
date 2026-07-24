"""通知推送模块：邮件发送（仅展示 Top 3 论文 + 审阅页面链接）"""

import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import EmailConfig


class EmailNotifier:
    """邮件通知器 —— 抓取完成后发送报告邮件。"""

    def __init__(self, config: EmailConfig):
        self.config = config

    def send_fetch_report(
        self,
        stats: dict,
        new_papers: list[dict],
        keywords: list[str],
        server_url: str = "http://localhost:8899",
    ) -> bool:
        """发送简洁邮件：统计摘要 + Top 3 论文 + 审阅页面链接。

        Args:
            stats: 统计信息
            new_papers: 新论文列表（取 Top 3）
            keywords: 关键词列表
            server_url: Web 审阅服务地址

        Returns:
            是否成功发送
        """
        cfg = self.config
        if not cfg.enabled:
            return False
        if not cfg.smtp_host or not cfg.username:
            print("  [i] Email enabled but SMTP config incomplete, skipping")
            return False

        today = datetime.date.today().isoformat()
        run_time = datetime.datetime.now().strftime("%H:%M")

        total = stats.get("total", 0)
        pending = stats.get("pending", 0)
        important = stats.get("by_remark", {}).get("important", 0) if isinstance(stats.get("by_remark"), dict) else 0
        useful = stats.get("by_remark", {}).get("useful", 0) if isinstance(stats.get("by_remark"), dict) else 0

        # Top 3 论文（按 llm_score 降序）
        top3 = sorted(new_papers, key=lambda p: p.get("llm_score", 0), reverse=True)[:3]
        papers_html = ""
        for p in top3:
            score = p.get("llm_score", 0)
            remark = p.get("llm_remark", "")
            papers_html += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">
                    <a href="{p.get('url', '#')}" style="color:#667eea;text-decoration:none;font-weight:500;">{p.get('title', '')[:80]}</a>
                </td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">
                    <span style="background:{_remark_color(remark)};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">{remark}</span>
                </td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;font-size:14px;">{score:.2f}</td>
            </tr>"""

        if papers_html:
            papers_html = f"""<h3 style="font-size:15px;margin:16px 0 8px;">Top 3 论文</h3>
            <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
                <tr style="background:#f8f9fa;">
                    <th style="padding:8px;text-align:left;font-size:13px;color:#666;">论文</th>
                    <th style="padding:8px;text-align:center;font-size:13px;color:#666;">评级</th>
                    <th style="padding:8px;text-align:center;font-size:13px;color:#666;">评分</th>
                </tr>{papers_html}</table>"""

        keywords_str = ", ".join(keywords) if keywords else "未指定"

        html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,'Microsoft YaHei',sans-serif;padding:20px;color:#333;">
<div style="max-width:600px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;padding:24px;border-radius:12px;text-align:center;margin-bottom:20px;">
        <h1 style="margin:0;font-size:22px;">Arxiv 论文跟踪</h1>
        <p style="margin:8px 0 0;opacity:0.85;">{today} {run_time}</p>
    </div>

    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
        <tr>
            <td style="text-align:center;padding:12px;background:#f8f9fa;border-radius:8px 0 0 8px;width:25%;">
                <div style="font-size:24px;font-weight:bold;color:#e74c3c;">{important}</div>
                <div style="font-size:12px;color:#888;">重要</div>
            </td>
            <td style="text-align:center;padding:12px;background:#f8f9fa;width:25%;">
                <div style="font-size:24px;font-weight:bold;color:#f39c12;">{useful}</div>
                <div style="font-size:12px;color:#888;">值得关注</div>
            </td>
            <td style="text-align:center;padding:12px;background:#f8f9fa;width:25%;">
                <div style="font-size:24px;font-weight:bold;color:#e67e22;">{pending}</div>
                <div style="font-size:12px;color:#888;">待审核</div>
            </td>
            <td style="text-align:center;padding:12px;background:#f8f9fa;border-radius:0 8px 8px 0;width:25%;">
                <div style="font-size:24px;font-weight:bold;color:#95a5a6;">{total}</div>
                <div style="font-size:12px;color:#888;">总计</div>
            </td>
        </tr>
    </table>

    {papers_html}

    <div style="background:#f0f7ff;padding:16px;border-radius:8px;margin-bottom:20px;border-left:3px solid #667eea;">
        <p style="margin:0 0 8px;font-size:14px;color:#555;">
            打开审阅页面查看完整论文列表并进行标记:
        </p>
        <a href="{server_url}" style="display:inline-block;background:#667eea;color:white;text-decoration:none;padding:10px 24px;border-radius:6px;font-size:14px;">打开审阅页面</a>
    </div>

    <div style="font-size:12px;color:#999;text-align:center;border-top:1px solid #eee;padding-top:16px;">
        <p>关键词: {keywords_str} | 本邮件由 Paper Research 自动生成</p>
    </div>
</div>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Arxiv Paper Fetch - {today} [{keywords_str}]"
        msg["From"] = cfg.from_addr
        msg["To"] = cfg.to_addr
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        text_body = f"""Arxiv Paper Research - {today}
关键词: {keywords_str}
重要: {important} | 值得关注: {useful} | 待审核: {pending} | 总计: {total}
审阅页面: {server_url}
"""
        msg.attach(MIMEText(text_body, "plain", "utf-8"))

        try:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port) as server:
                server.login(cfg.username, cfg.password)
                server.send_message(msg)
            print("  [i] 邮件通知发送成功")
            return True
        except Exception as e:
            print(f"  [i] 邮件发送失败: {e}")
            return False


def _remark_color(remark: str) -> str:
    return {
        "important": "#e74c3c",
        "useful": "#f39c12",
        "browse": "#3498db",
        "skip": "#95a5a6",
    }.get(remark, "#95a5a6")
