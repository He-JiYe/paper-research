"""static_meta 测试：默认值、app-meta 读写（单一来源，无每键合并）、短标题建议"""

import json

from src.core.text import suggest_short_title
from src.static_meta import (
    _default_app_meta,
    ensure_static_data,
    load_app_meta,
)


def test_defaults_present():
    """默认静态数据与旧 renderer 常量一致"""
    meta = _default_app_meta()
    assert "cs.AI" in meta["categories"]
    assert "stat.ML" in meta["categories"]
    assert meta["remark_labels"]["important"] == "⭐ 重要"
    assert meta["remark_colors"]["important"] == "#e74c3c"
    assert meta["section_labels"]["unmarked"] == "待审核"
    assert meta["section_labels"]["lurk"] == "延后处理"


def test_suggest_short_title_empty_title():
    """空标题兜底 → Paper"""
    paper = {"published": "2024-01-01", "title": ""}
    assert suggest_short_title(paper).endswith("Paper")


def test_load_app_meta_missing_file_falls_back(tmp_path):
    """文件缺失 → 返回默认值"""
    missing = tmp_path / "nope.json"
    meta = load_app_meta(missing)
    assert meta["categories"] == _default_app_meta()["categories"]
    assert meta["remark_labels"] == _default_app_meta()["remark_labels"]


def test_load_app_meta_corrupt_falls_back(tmp_path):
    """文件损坏 → 返回默认值"""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    meta = load_app_meta(bad)
    assert meta["remark_colors"] == _default_app_meta()["remark_colors"]


def test_load_app_meta_returns_file_as_is(tmp_path):
    """单一来源：文件存在则原样返回（不做每键合并）"""
    p = tmp_path / "app-meta.json"
    p.write_text(json.dumps({"categories": ["cs.AI", "cs.CV"]}), encoding="utf-8")
    meta = load_app_meta(p)
    assert meta == {"categories": ["cs.AI", "cs.CV"]}


def test_ensure_static_data_writes(tmp_path):
    """不存在时落盘默认值 JSON"""
    p = tmp_path / "app-meta.json"
    ensure_static_data(p)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data) == {"categories", "remark_labels", "remark_colors", "section_labels"}
    assert data["categories"] == _default_app_meta()["categories"]


def test_ensure_static_data_does_not_overwrite(tmp_path):
    """已存在时不覆盖用户编辑"""
    p = tmp_path / "app-meta.json"
    p.write_text(json.dumps({"categories": ["cs.XYZ"]}), encoding="utf-8")
    ensure_static_data(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["categories"] == ["cs.XYZ"]
