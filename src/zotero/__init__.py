"""Zotero 集成模块

提供与 Zotero 文献管理系统的完整集成能力。

主要组件:
- ZoteroClient: 基于 pyzotero 的 API 客户端（src.zotero.client）
- models: 标签常量、Extra 编解码、数据转换（src.zotero.models）
"""

from src.zotero.client import ZoteroClient

__all__ = [
    "ZoteroClient",
]
