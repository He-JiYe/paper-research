"""配置加载模块测试"""

import json

import pytest

from src.config import (OUTPUT_DIR, ROOT_DIR, AppConfig, ArxivConfig,
                        EmailConfig, LLMConfig, NotificationConfig,
                        ScoringConfig, ServerConfig, _deep_merge, _load_json,
                        get_active_keywords, get_db_path, get_output_dir,
                        load_keywords, load_settings)


class TestHelpers:
    def test_load_json_exists(self, temp_dir):
        """_load_json 读取存在的 JSON"""
        f = temp_dir / "test.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        data = _load_json(f)
        assert data == {"key": "value"}

    def test_load_json_not_exists(self, temp_dir):
        """_load_json 对不存在的文件返回空字典"""
        f = temp_dir / "nonexistent.json"
        data = _load_json(f)
        assert data == {}

    def test_load_json_invalid(self, temp_dir):
        """_load_json 对无效 JSON 抛出异常"""
        f = temp_dir / "bad.json"
        f.write_text("{invalid", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            _load_json(f)

    def test_deep_merge_basic(self):
        """_deep_merge 合并简单键值"""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_deep_merge_nested(self):
        """_deep_merge 深度合并嵌套字典"""
        base = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 8899, "user": "admin"}}
        result = _deep_merge(base, override)
        assert result == {"db": {"host": "localhost", "port": 8899, "user": "admin"}}

    def test_deep_merge_overwrite_non_dict(self):
        """当 override 为非 dict 而 base 为 dict 时，直接覆盖"""
        base = {"key": {"nested": "value"}}
        override = {"key": "scalar"}
        result = _deep_merge(base, override)
        assert result == {"key": "scalar"}

    def test_deep_merge_empty_override(self):
        result = _deep_merge({"a": 1}, {})
        assert result == {"a": 1}


class TestDataclasses:
    def test_llm_config_defaults(self):
        cfg = LLMConfig()
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-v4-flash"
        assert cfg.temperature == 0.3
        assert cfg.max_tokens == 2000

    def test_arxiv_config_defaults(self):
        cfg = ArxivConfig()
        assert cfg.max_concurrent_requests == 10
        assert cfg.lookback_days == 90
        assert cfg.target_new_per_keyword == 25

    def test_server_config_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8899

    def test_email_config_defaults(self):
        cfg = EmailConfig()
        assert cfg.enabled is False
        assert cfg.smtp_port == 465

    def test_notification_config_defaults(self):
        cfg = NotificationConfig()
        assert cfg.windows_toast is True

    def test_scoring_config_defaults(self):
        cfg = ScoringConfig()
        assert cfg.min_relevance_score == 0.3

    def test_app_config_defaults(self):
        cfg = AppConfig()
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.arxiv, ArxivConfig)
        assert isinstance(cfg.server, ServerConfig)
        assert isinstance(cfg.notification, NotificationConfig)
        assert isinstance(cfg.scoring, ScoringConfig)

    def test_llm_config_custom(self):
        cfg = LLMConfig(
            provider="openai",
            model="gpt-4",
            api_base="https://api.openai.com",
            api_key="sk-test",
            temperature=0.7,
            max_tokens=4000,
        )
        assert cfg.provider == "openai"
        assert cfg.api_key == "sk-test"


class TestLoadSettings:
    def test_load_settings_returns_appconfig(self):
        """load_settings 返回 AppConfig 对象"""
        config = load_settings()
        assert isinstance(config, AppConfig)
        assert isinstance(config.llm, LLMConfig)
        assert isinstance(config.arxiv, ArxivConfig)
        assert isinstance(config.server, ServerConfig)
        assert isinstance(config.notification, NotificationConfig)

    def test_load_settings_env_key_priority(self, monkeypatch):
        """环境变量 DEEPSEEK_API_KEY 优先于配置文件"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
        config = load_settings()
        assert config.llm.api_key == "from-env"
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def test_load_settings_defaults_values(self):
        """部分字段使用默认值"""
        config = load_settings()
        assert config.scoring.min_relevance_score == 0.3

    def test_load_settings_has_configured_values(self):
        """从真实配置文件中读取的值"""
        config = load_settings()
        # 配置文件中的值
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8899


class TestKeywords:
    def test_load_keywords(self, monkeypatch, temp_dir):
        """load_keywords 读取 keywords.json"""
        monkeypatch.setattr("src.config.CONFIG_DIR", temp_dir)

        kw_file = temp_dir / "keywords.json"
        kw_file.write_text(
            json.dumps(
                [
                    {"keyword": "ML", "active": True},
                    {"keyword": "CV", "active": False},
                ]
            ),
            encoding="utf-8",
        )

        kws = load_keywords()
        assert len(kws) == 2
        assert kws[0]["keyword"] == "ML"

    def test_load_keywords_not_list(self, monkeypatch, temp_dir):
        """非列表 JSON 时返回空列表"""
        monkeypatch.setattr("src.config.CONFIG_DIR", temp_dir)

        kw_file = temp_dir / "keywords.json"
        kw_file.write_text('{"not": "a list"}', encoding="utf-8")

        kws = load_keywords()
        assert kws == []

    def test_get_active_keywords(self, monkeypatch, temp_dir):
        """get_active_keywords 只返回 active=True 的"""
        monkeypatch.setattr("src.config.CONFIG_DIR", temp_dir)

        kw_file = temp_dir / "keywords.json"
        kw_file.write_text(
            json.dumps(
                [
                    {"keyword": "ML", "active": True},
                    {"keyword": "CV", "active": False},
                    {"keyword": "NLP", "active": True},
                ]
            ),
            encoding="utf-8",
        )

        active = get_active_keywords()
        assert len(active) == 2
        assert all(kw["active"] for kw in active)
        assert active[0]["keyword"] == "ML"

    def test_get_active_keywords_default_active(self, monkeypatch, temp_dir):
        """无 active 字段时默认为活跃"""
        monkeypatch.setattr("src.config.CONFIG_DIR", temp_dir)

        kw_file = temp_dir / "keywords.json"
        kw_file.write_text(
            json.dumps(
                [
                    {"keyword": "ML"},
                    {"keyword": "CV", "active": False},
                ]
            ),
            encoding="utf-8",
        )

        active = get_active_keywords()
        assert len(active) == 1
        assert active[0]["keyword"] == "ML"


class TestPaths:
    def test_get_db_path(self, monkeypatch, temp_dir):
        """get_db_path 在 DATA_DIR 下创建目录并返回路径"""
        monkeypatch.setattr("src.config.DATA_DIR", temp_dir / "data")
        path = get_db_path()
        assert path.endswith("papers.db")
        assert (temp_dir / "data").exists()

    def test_get_output_dir(self, monkeypatch, temp_dir):
        """get_output_dir 创建目录并返回 Path"""
        monkeypatch.setattr("src.config.OUTPUT_DIR", temp_dir / "output")
        result = get_output_dir()
        assert result == temp_dir / "output"
        assert result.exists()

    def test_rooth_paths_defined(self):
        """ROOT_DIR 等常量已定义"""
        assert ROOT_DIR is not None
        assert ROOT_DIR.exists()
        assert OUTPUT_DIR is not None
