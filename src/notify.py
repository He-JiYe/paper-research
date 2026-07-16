"""通知推送模块：Windows Toast 通知（win11toast） + 邮件发送"""

import datetime

try:
    from win11toast import toast
except ImportError:
    toast = None  # Windows 专属，Linux/macOS 上不可用


def send_windows_toast(
    title: str,
    message: str,
    *,
    keywords: list[dict] | None = None,
    url: str = "",
    icon: str = "",
):
    """
    弹出 Windows 原生通知（使用 win11toast）。

    Args:
        title: 通知标题（如 "Arxiv Paper Research"）
        message: 通知正文
        keywords: 可选，格式 [{"keyword": "ML", "count": 3}, ...]
        url: 可选，通知按钮点击后打开的 URL
        icon: 可选，通知图标路径
    """
    # 构造结构化正文
    body_parts = []

    if keywords:
        body_parts.append("📌 待审阅:")
        for k in keywords:
            body_parts.append(f"  • {k['keyword']}  ({k['count']} 篇)")
        body_parts.append("")

    body_parts.append(message)
    body = "\n".join(body_parts)

    buttons = []
    if url:
        buttons.append(
            {
                "activationType": "protocol",
                "arguments": url,
                "content": "打开审阅",
            }
        )

    if toast is None:
        print("  [i] Windows Toast 通知不可用（win11toast 未安装，仅支持 Windows）")
        return

    try:
        toast(title, body, icon=icon, buttons=buttons, duration="long")
    except Exception as e:
        print(f"  [i] Toast notification failed: {e}")


def send_email_if_configured(
    settings,
    stats: dict,
    server_url: str = "http://localhost:8899",
    keywords: list[dict] | None = None,
) -> bool:
    """
    发送简洁的邮件摘要（含统计和链接，而非完整 HTML 页面）。

    Args:
        settings: AppConfig 对象
        stats: get_stats() 返回的统计字典
        server_url: Web 审阅服务的 URL
        keywords: 可选，格式 [{"keyword": "ML", "count": 3}, ...]

    Returns:
        是否成功发送
    """
    email_cfg = settings.notification.email

    if not email_cfg.enabled:
        return False

    if not email_cfg.smtp_host or not email_cfg.username:
        print("  [i] Email notification enabled but SMTP config incomplete, skipping")
        return False

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    today = datetime.date.today().isoformat()
    run_time = datetime.datetime.now().strftime("%H:%M")

    # 构建简洁的邮件正文（HTML 格式）
    important = stats.get("important", 0)
    useful = stats.get("useful", 0)
    pending = stats.get("summarized_pending", 0)

    keywords_html = ""
    if keywords:
        rows = "".join(f"""<tr>
                <td style="padding:8px 16px;background:#f8f9fa;border-radius:6px;font-size:14px;color:#333;">
                    <span style="font-weight:600;">{k["keyword"]}</span>
                    <span style="float:right;background:#e74c3c;color:white;border-radius:10px;padding:1px 10px;font-size:13px;">{k["count"]} 篇待审</span>
                </td>
            </tr>""" for k in keywords)
        keywords_html = f"""<table style="width:100%;border-collapse:separate;border-spacing:0 6px;margin-bottom:16px;">{rows}</table>"""

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, 'Microsoft YaHei', sans-serif; padding: 20px; color: #333;">
<div style="max-width: 600px; margin: 0 auto;">
    <div style="background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 24px; border-radius: 12px; text-align: center; margin-bottom: 20px;">
        <h1 style="margin: 0; font-size: 22px;">Arxiv 论文跟踪日报</h1>
        <p style="margin: 8px 0 0; opacity: 0.85;">{today} {run_time}</p>
    </div>

    {keywords_html}

    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
        <tr>
            <td style="text-align: center; padding: 12px; background: #f8f9fa; border-radius: 8px 0 0 8px;">
                <div style="font-size: 24px; font-weight: bold; color: #e74c3c;">{important}</div>
                <div style="font-size: 12px; color: #888;">Important</div>
            </td>
            <td style="text-align: center; padding: 12px; background: #f8f9fa;">
                <div style="font-size: 24px; font-weight: bold; color: #f39c12;">{useful}</div>
                <div style="font-size: 12px; color: #888;">Useful</div>
            </td>
            <td style="text-align: center; padding: 12px; background: #f8f9fa;">
                <div style="font-size: 24px; font-weight: bold; color: #e67e22;">{pending}</div>
                <div style="font-size: 12px; color: #888;">待审核</div>
            </td>
            <td style="text-align: center; padding: 12px; background: #f8f9fa; border-radius: 0 8px 8px 0;">
            </td>
        </tr>
    </table>

    <div style="background: #f0f7ff; padding: 16px; border-radius: 8px; margin-bottom: 20px; border-left: 3px solid #667eea;">
        <p style="margin: 0 0 8px; font-size: 14px; color: #555;">
            打开审阅页面查看完整论文列表并进行标记:
        </p>
        <a href="{server_url}" style="display: inline-block; background: #667eea; color: white; text-decoration: none; padding: 10px 24px; border-radius: 6px; font-size: 14px;">打开审阅页面</a>
    </div>

    <div style="font-size: 12px; color: #999; text-align: center; border-top: 1px solid #eee; padding-top: 16px;">
        <p>本邮件由 Paper Research 自动生成</p>
    </div>
</div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Arxiv Paper Daily - {today}" + (
        f" [{', '.join(k['keyword'] for k in keywords)}]" if keywords else ""
    )
    msg["From"] = email_cfg.from_addr
    msg["To"] = email_cfg.to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # 纯文本备用
    keywords_line = (
        " | ".join(f"{k['keyword']}({k['count']})" for k in keywords) + " | "
        if keywords
        else ""
    )
    text_body = f"""Arxiv Paper Research Daily - {today}

{keywords_line}Important: {important} | Useful: {useful} | Pending: {pending}
Open review page: {server_url}

This email was auto-generated by Paper Research.
"""
    msg.attach(MIMEText(text_body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(email_cfg.smtp_host, email_cfg.smtp_port) as server:
            server.login(email_cfg.username, email_cfg.password)
            server.send_message(msg)
        print("  [i] Email sent")
        return True
    except Exception as e:
        print(f"  [i] Email send failed: {e}")
        return False
