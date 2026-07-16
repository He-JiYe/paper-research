"""BaseAgent 基类测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import BaseAgent


class TestInit:
    def test_default_init(self):
        agent = BaseAgent()
        assert agent.api_key == ""
        assert agent.model == "deepseek-v4-flash"
        assert agent.temperature == 0.3
        assert agent.max_tokens == 2000

    def test_custom_init(self):
        agent = BaseAgent(
            api_key="sk-test",
            model="gpt-4",
            temperature=0.5,
            max_tokens=4000,
            api_base="https://api.openai.com",
        )
        assert agent.api_key == "sk-test"
        assert agent.model == "gpt-4"
        assert agent.api_base == "https://api.openai.com"

    def test_from_config(self, mock_settings):
        agent = BaseAgent.from_config(mock_settings.llm)
        assert agent.api_key == mock_settings.llm.api_key
        assert agent.model == mock_settings.llm.model

    def test_client_lazy_loading(self):
        """client 和 async_client 懒加载"""
        agent = BaseAgent(api_key="sk-test")
        assert agent._client is None
        assert agent._async_client is None

        with patch("openai.OpenAI") as mockopenai:
            client = agent.client
            assert client is not None
            mockopenai.assert_called_once_with(
                api_key="sk-test", base_url="https://api.deepseek.com"
            )
            # 再次访问应使用缓存
            assert mockopenai.call_count == 1  # 只创建一次

    def test_async_client_lazy_loading(self):
        """async_client 懒加载且缓存"""
        agent = BaseAgent(api_key="sk-test")
        with patch("openai.AsyncOpenAI") as MockAsync:
            ac = agent.async_client
            assert ac is not None
            MockAsync.assert_called_once_with(
                api_key="sk-test", base_url="https://api.deepseek.com"
            )
            assert MockAsync.call_count == 1


class TestExtractJson:
    def test_extract_direct_json(self):
        result = BaseAgent._extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_extract_code_block(self):
        text = 'Some text\n```json\n{"key": "value"}\n```\nmore text'
        result = BaseAgent._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_code_block_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        result = BaseAgent._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_braces(self):
        text = 'Here is the result: {"key": "value"} and more text'
        result = BaseAgent._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = BaseAgent._extract_json(text)
        assert result["outer"]["inner"] == [1, 2, 3]

    def test_extract_invalid_text(self):
        assert BaseAgent._extract_json("not json at all") is None

    def test_extract_empty_string(self):
        assert BaseAgent._extract_json("") is None

    def test_extract_brace_with_leading_text(self):
        text = 'Output: {"a": 1, "b": 2} End'
        result = BaseAgent._extract_json(text)
        assert result == {"a": 1, "b": 2}


class TestCall:
    def test_call_no_api_key(self):
        agent = BaseAgent()
        result = agent._call("system", "user")
        assert result is None

    def test_call_success(self):
        """模拟 OpenAI 客户端调用成功"""
        agent = BaseAgent(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        # mock client 的 chat.completions.create
        agent._client = MagicMock()
        agent._client.chat.completions.create = MagicMock(return_value=mock_response)

        result = agent._call("System prompt", "User prompt")
        assert result == '{"result": "ok"}'
        agent._client.chat.completions.create.assert_called_once()

    def test_call_retry_on_failure(self):
        """第一次失败后重试"""
        agent = BaseAgent(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        mock_create = MagicMock(side_effect=[Exception("API Error"), mock_response])

        agent._client = MagicMock()
        agent._client.chat.completions.create = mock_create

        result = agent._call("System", "User")
        assert result == '{"result": "ok"}'
        assert mock_create.call_count == 2

    def test_call_all_failures(self):
        """全部重试失败后返回 None"""
        agent = BaseAgent(api_key="sk-test")
        agent._client = MagicMock()
        agent._client.chat.completions.create = MagicMock(
            side_effect=Exception("Always fails")
        )
        result = agent._call("System", "User")
        assert result is None

    def test_call_empty_response(self):
        """空响应时重试"""
        agent = BaseAgent(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "   "

        agent._client = MagicMock()
        agent._client.chat.completions.create = MagicMock(return_value=mock_response)

        result = agent._call("System", "User")
        assert result is None


class TestCallAsync:
    @pytest.mark.asyncio
    async def test_call_async_no_api_key(self):
        agent = BaseAgent()
        result = await agent._call_async("system", "user")
        assert result is None

    @pytest.mark.asyncio
    async def test_call_async_success(self):
        agent = BaseAgent(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        agent._async_client = MagicMock()
        agent._async_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await agent._call_async("System", "User")
        assert result == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_call_async_retry(self):
        agent = BaseAgent(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        mock_create = AsyncMock(side_effect=[Exception("API Error"), mock_response])

        agent._async_client = MagicMock()
        agent._async_client.chat.completions.create = mock_create

        result = await agent._call_async("System", "User")
        assert result == '{"result": "ok"}'
        assert mock_create.call_count == 2


class TestFallback:
    def test_fallback_not_implemented(self):
        agent = BaseAgent()
        with pytest.raises(NotImplementedError):
            agent._fallback("test")
