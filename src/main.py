"""CLI 入口：命令行参数解析和命令分发"""

import argparse
import sys
from pathlib import Path

# 将项目根目录加入 sys.path（支持 uv run paper-research 和 python src/main.py 两种方式）
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main():
    # 强制 UTF-8 输出（Windows GBK 终端兼容）
    import sys as _sys

    if _sys.stdout.encoding != "utf-8":
        try:
            _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="paper-research",
        description="论文自动调研工具：定时抓取 Arxiv，LLM 摘要，交互式审阅",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="抓取 Arxiv 论文并生成摘要")
    fetch_parser.add_argument("--keyword", "-k", help="仅抓取指定关键词")
    fetch_parser.add_argument(
        "--dry-run", action="store_true", help="预览模式，不实际写入"
    )
    fetch_parser.add_argument(
        "--serve", "-s", action="store_true", help="抓取后自动启动 Web 服务"
    )

    # serve
    subparsers.add_parser("serve", help="启动本地 Web 审阅服务")

    # review
    review_parser = subparsers.add_parser("review", help="查看待审核论文列表")
    review_group = review_parser.add_mutually_exclusive_group()
    review_group.add_argument(
        "--head", "-n", type=int, default=10, help="显示待审核论文数量，默认 10 篇"
    )
    review_group.add_argument(
        "--all", "-a", action="store_true", help="显示所有待审核论文"
    )

    # mark
    mark_parser = subparsers.add_parser("mark", help="标记论文")
    mark_parser.add_argument("arxiv_id", help="ArXiv ID，如 2401.00123")
    mark_parser.add_argument(
        "--type",
        "-t",
        choices=["ignore", "skim", "deep-read", "lurk"],
        required=True,
        help="标记类型",
    )

    # note
    note_parser = subparsers.add_parser("note", help="生成或打开阅读笔记")
    note_parser.add_argument("arxiv_id", help="ArXiv ID")

    # list
    list_parser = subparsers.add_parser("list", help="列出论文")
    list_parser.add_argument("--keyword", "-k", help="按关键词筛选")
    list_parser.add_argument(
        "--status", "-s", choices=["summarized", "marked"], help="按状态筛选"
    )
    list_parser.add_argument(
        "--mark",
        "-m",
        choices=["ignore", "lurk", "skim", "deep_read"],
        help="按用户标记筛选",
    )
    list_parser.add_argument(
        "--sort",
        "-o",
        choices=["date", "rdate", "score", "rscore"],
        default="d",
        help="排序方式， date 和 score 分别表示按日期和评分降序, rdate 和 rscore 分别表示按日期和评分升序, 默认为按日期降序",
    )
    list_group = list_parser.add_mutually_exclusive_group()
    list_group.add_argument(
        "--head", "-n", type=int, default=10, help="显示论文数量，默认 10 篇"
    )
    list_group.add_argument("--all", "-a", action="store_true", help="显示所有论文")

    # status
    subparsers.add_parser("status", help="查看统计信息")

    # notify
    subparsers.add_parser("notify", help="手动重新发送通知")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    dispatch(args)


def dispatch(args):
    """根据命令分发到对应处理函数"""
    from src.config import get_db_path, load_settings
    from src.db import get_connection, init_db

    # 初始化配置和数据库
    settings = load_settings()
    db_path = get_db_path()
    init_db(db_path)
    conn = get_connection(db_path)

    try:
        if args.command == "fetch":
            from src.commands import cmd_fetch

            cmd_fetch(args, settings, conn)
        elif args.command == "serve":
            from src.commands import cmd_serve

            cmd_serve(args, settings, conn)
        elif args.command == "review":
            from src.commands import cmd_review

            cmd_review(args, settings, conn)
        elif args.command == "mark":
            from src.commands import cmd_mark

            cmd_mark(args, settings, conn)
        elif args.command == "note":
            from src.commands import cmd_note

            cmd_note(args, settings, conn)
        elif args.command == "list":
            from src.commands import cmd_list

            cmd_list(args, settings, conn)
        elif args.command == "status":
            from src.commands import cmd_status

            cmd_status(args, settings, conn)
        elif args.command == "notify":
            from src.commands import cmd_notify

            cmd_notify(args, settings, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
