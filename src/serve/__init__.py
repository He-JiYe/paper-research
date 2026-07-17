"""
Paper Researcher Server 模块，提供 Web 服务接口
"""

from src.serve.renderer import generate_notes_index_html, generate_summary_html
from src.serve.server import run_server

__all__ = [
    "generate_notes_index_html",
    "generate_summary_html",
    "run_server",
]
