"""Zotero 集成模块

提供与 Zotero 文献管理系统的完整集成能力。

主要组件:
- ZoteroClient: 基于 pyzotero 的 API 客户端（src.zotero.client）
"""

from src.zotero.client import ZoteroClient

__all__ = [
    "ZoteroClient",
]
