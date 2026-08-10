"""CLI 入口：命令行参数解析和命令分发"""

import argparse
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main():
    # 统一日志：每日 YYYY-MM-DD.log + errors.log（覆盖所有 CLI 路径，幂等）
    from src.logging_setup import setup_logging

    setup_logging()

    # 强制 UTF-8 输出（pythonw 等无控制台环境下 sys.stdout 为 None，需判空）
    import contextlib

    if sys.stdout is not None and sys.stdout.encoding != "utf-8":
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="paper-research",
        description="论文自动调研工具：定时抓取论文，LLM 评分，交互式审阅，Zotero 集成",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="抓取论文并生成摘要")
    fetch_parser.add_argument("--keyword", "-k", help="仅抓取指定关键词")
    fetch_parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    fetch_parser.add_argument(
        "--mode",
        "-m",
        choices=["incremental", "historical"],
        default="incremental",
        help="抓取模式: incremental（增量）或 historical（全量）",
    )
    fetch_parser.add_argument(
        "--max-results",
        "-n",
        type=int,
        default=0,
        help="每个关键词最大结果数，0 表示使用配置默认值",
    )

    # serve
    subparsers.add_parser("serve", help="启动本地 Web 审阅服务")

    # status
    subparsers.add_parser("status", help="查看统计信息")

    # notify
    subparsers.add_parser("notify", help="手动发送通知邮件")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    dispatch(args)


def dispatch(args):
    """根据命令分发到对应处理函数。"""
    from src.config.loader import load_settings

    settings = load_settings()

    # 验证 Zotero 配置
    if args.command in ("serve", "status"):
        if not settings.zotero.api_key:
            print("  ⚠️ 未配置 Zotero API Key（导入 Zotero 功能将不可用）")
            print("  💡 请设置环境变量 ZOTERO_API_KEY 或修改 config/config.yaml")
        if not settings.zotero.library_id:
            print("  ⚠️ 未配置 Zotero Library ID")
            print("  💡 请设置环境变量 ZOTERO_LIBRARY_ID 或修改 config/config.yaml")

    if args.command == "fetch":
        from src.commands import cmd_fetch

        cmd_fetch(args, settings)
    elif args.command == "serve":
        from src.commands import cmd_serve

        cmd_serve(args, settings)
    elif args.command == "status":
        from src.commands import cmd_status

        cmd_status(args, settings)
    elif args.command == "notify":
        from src.commands import cmd_notify

        cmd_notify(args, settings)


if __name__ == "__main__":
    main()
