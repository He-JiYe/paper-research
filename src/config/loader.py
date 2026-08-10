"""配置加载：config.yaml 读取 + Pydantic 构建 + options 动态解析。

- ``load_config_raw``：原始 dict 读取（含 ``${ENV_VAR}`` 递归解析）；
- ``load_settings``：Pydantic ``AppConfig.model_validate`` 构建；
- ``_resolve_source_options``：按 ``source`` 注册名经 registry 把每个 SourceConfig 的
  原始参数字典解析为对应 ``FetchOptions`` 实例（未知 source 名抛错，启动即失败）。
"""

import os
import re

import yaml

from src.core.config import AppConfig
from src.network import REGISTRY
from src.paths import CONFIG_PATH

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env_vars(value: str) -> str:
    """将 ${VAR_NAME} 替换为环境变量值，未设置则保留原样。"""

    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        return os.environ.get(var_name, m.group(0))

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_env_vars_recursive(obj):
    """递归处理所有字符串值中的 ${VAR} 占位符。"""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars_recursive(item) for item in obj]
    return obj


def load_config_raw() -> dict:
    """加载 config.yaml 的原始 dict（含 ${ENV_VAR} 解析），文件不存在返回空 dict。"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _resolve_env_vars_recursive(raw)
    return {}


def save_config_raw(raw: dict) -> None:
    """把配置 dict 原样写回 config.yaml（官方写入 API，供初始化/配置工具调用）。

    与 ``load_config_raw`` 的区别：**不做** ``${ENV_VAR}`` 占位符解析/替换，原样落盘——
    调用方应传入含 ``${ENV_VAR}`` 字面量的 dict，避免把已解析的明文密钥回写。

    Args:
        raw: 顶层配置 dict（llm / fetch.sources[] / keywords[] / notification.email /
            scheduler / server / zotero）。
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)


def normalize_keyword_entry(d: dict) -> dict:
    """keywords 条目归一化为固定形式（keyword/categories/active）。"""
    return {
        "keyword": d.get("keyword", ""),
        "categories": d.get("categories") or [],
        "active": d.get("active", True),
    }


def _resolve_source_options(cfg: AppConfig) -> AppConfig:
    """把每个 SourceConfig 的 options 从原始 dict 解析为 registry 对应 FetchOptions 实例。"""
    for sc in cfg.fetch.sources:
        options_cls = REGISTRY.options.get(sc.source)  # 未知 source 名 → ValueError（fail fast）
        raw_opts = sc.options if isinstance(sc.options, dict) else {}
        sc.options = options_cls.from_dict(raw_opts)
    return cfg


def load_settings() -> AppConfig:
    """加载 config.yaml 并返回 AppConfig（含每源 Options 解析）。"""
    raw = load_config_raw()

    # config.yaml 采用三层嵌套 notification.email.*，而 AppConfig.notification 是平坦
    # EmailConfig：把内层 email 展平回顶层。字段名即 Pydantic 字段（from_addr/to_addr），
    # config.yaml 需按规范书写，加载器不做键名映射。
    notification = raw.get("notification") or {}
    email = notification.get("email") or {}
    if email:
        # 合并而非整体替换：保留 notification 下 email 之外的键（如顶层 enabled），
        # 避免用户写 `notification: {enabled: true, email: {...}}` 时 enabled 被静默丢弃。
        raw["notification"] = {**notification, **email}

    # keywords 过滤无 keyword 键的条目并归一化
    if isinstance(raw.get("keywords"), list):
        raw["keywords"] = [
            normalize_keyword_entry(k)
            for k in raw["keywords"]
            if isinstance(k, dict) and "keyword" in k
        ]

    model = AppConfig.model_validate(raw)
    return _resolve_source_options(model)
