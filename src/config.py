"""配置加载模块：读取 settings.json 和 keywords.json"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "output"


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_base: str = "https://api.deepseek.com"
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 2000


@dataclass
class ArxivConfig:
    delay_between_requests: float = 0.3
    max_concurrent_requests: int = 3
    lookback_days: int = 90
    user_agent: str = ""
    target_new_per_keyword: int = 25


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8899


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""


@dataclass
class NotificationConfig:
    windows_toast: bool = True
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class ScoringConfig:
    min_relevance_score: float = 0.3


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    arxiv: ArxivConfig = field(default_factory=ArxivConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)


def _load_json(filepath: Path) -> dict:
    """加载 JSON 文件，不存在则返回空字典"""
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings() -> AppConfig:
    """加载 settings.json 并返回 AppConfig 对象"""
    data = _load_json(CONFIG_DIR / "settings.json")

    # 解析 LLM 配置，API Key 优先从环境变量读取，其次从配置文件
    llm_data = data.get("llm", {})
    config_api_key = llm_data.get("api_key", "")
    env_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    llm_data["api_key"] = env_api_key if env_api_key else config_api_key

    llm = LLMConfig(
        provider=llm_data.get("provider", "deepseek"),
        model=llm_data.get("model", "deepseek-v4-flash"),
        api_base=llm_data.get("api_base", "https://api.deepseek.com"),
        api_key=llm_data["api_key"],
        temperature=llm_data.get("temperature", 0.3),
        max_tokens=llm_data.get("max_tokens", 2000),
    )

    arxiv_data = data.get("arxiv", {})
    arxiv = ArxivConfig(
        delay_between_requests=arxiv_data.get("delay_between_requests", 1.0),
        max_concurrent_requests=arxiv_data.get("max_concurrent_requests", 10),
        lookback_days=arxiv_data.get("lookback_days", 90),
        user_agent=arxiv_data.get("user_agent", ""),
        target_new_per_keyword=arxiv_data.get("target_new_per_keyword", 25),
    )

    server_data = data.get("server", {})
    server = ServerConfig(
        host=server_data.get("host", "127.0.0.1"),
        port=server_data.get("port", 8899),
    )

    notif_data = data.get("notification", {})
    email_data = notif_data.get("email", {})
    email = EmailConfig(
        enabled=email_data.get("enabled", False),
        smtp_host=email_data.get("smtp_host", ""),
        smtp_port=email_data.get("smtp_port", 465),
        username=email_data.get("username", ""),
        password=email_data.get("password", ""),
        from_addr=email_data.get("from", ""),
        to_addr=email_data.get("to", ""),
    )
    notification = NotificationConfig(
        windows_toast=notif_data.get("windows_toast", True),
        email=email,
    )

    scoring_data = data.get("scoring", {})
    scoring = ScoringConfig(
        min_relevance_score=scoring_data.get("min_relevance_score", 0.3),
    )

    return AppConfig(
        llm=llm, arxiv=arxiv, server=server, notification=notification, scoring=scoring
    )


def load_keywords() -> list[dict]:
    """加载 keywords.json 并返回关键词列表"""
    data = _load_json(CONFIG_DIR / "keywords.json")
    if isinstance(data, list):
        return data
    return []


def get_active_keywords() -> list[dict]:
    """获取所有活跃的关键词"""
    return [kw for kw in load_keywords() if kw.get("active", True)]


def get_db_path() -> str:
    """获取数据库文件路径"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return str(DATA_DIR / "papers.db")


def get_output_dir() -> Path:
    """获取输出目录"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def save_keywords(keywords: list[dict]) -> None:
    """保存关键词配置到 keywords.json"""
    import json
    from pathlib import Path

    path = ROOT_DIR / "config" / "keywords.json"
    path.write_text(
        json.dumps(keywords, ensure_ascii=False, indent=2), encoding="utf-8"
    )
