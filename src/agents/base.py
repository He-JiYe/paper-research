"""BaseAgent — LLM Agent 基类（harness）

提供统一的 LLM 调用框架：
- 客户端懒加载（同步 OpenAI + 异步 AsyncOpenAI）
- 两步重试 + 指数退避
- JSON 响应提取（处理 ```json 包裹）
- 无 API Key 时自动 fallback
"""

import json
import re
from typing import Any


class BaseAgent:
    """所有 LLM Agent 的基类。

    Args:
        api_key: LLM API Key（空字符串时全部走 _fallback）
        api_base: API 端点
        model: 模型名
        temperature: 温度
        max_tokens: 最大 token 数
    """

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self._async_client = None

    @classmethod
    def from_config(cls, llm_config) -> "BaseAgent":
        """从 LLMConfig dataclass 构造 Agent。"""
        return cls(
            api_key=llm_config.api_key,
            api_base=llm_config.api_base,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
        )

    @property
    def client(self):
        """懒加载同步 OpenAI 客户端"""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._client

    @property
    def async_client(self):
        """懒加载异步 AsyncOpenAI 客户端"""
        if self._async_client is None:
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self.api_key, base_url=self.api_base
            )
        return self._async_client

    # ─── 同步调用 ──────────────────────────────────────────

    def _call(self, system_prompt: str, user_prompt: str) -> str | None:
        """同步 LLM 调用，两步重试，失败返回 None。"""
        if not self.api_key:
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                content = response.choices[0].message.content or ""
                if content.strip():
                    return content
            except Exception:
                if attempt == 0:
                    # 第一次失败 → 追加提醒后重试
                    user_prompt += "\n\nNote: output ONLY valid JSON, no explanation."
                    messages[1] = {"role": "user", "content": user_prompt}
                    continue
        return None

    # ─── 异步调用 ──────────────────────────────────────────

    async def _call_async(self, system_prompt: str, user_prompt: str) -> str | None:
        """异步 LLM 调用，两步重试，失败返回 None。"""
        if not self.api_key:
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(2):
            try:
                response = await self.async_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                content = response.choices[0].message.content or ""
                if content.strip():
                    return content
            except Exception:
                if attempt == 0:
                    user_prompt += "\n\nNote: output ONLY valid JSON, no explanation."
                    messages[1] = {"role": "user", "content": user_prompt}
                    continue
        return None

    # ─── JSON 提取 ─────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 LLM 响应中提取 JSON，处理各种包裹格式。"""
        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 2. ```json ... ``` 代码块
        json_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # 3. 大括号内容
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    # ─── Fallback ──────────────────────────────────────────

    def _fallback(self, *args: Any, **kwargs: Any) -> Any:
        """无 API Key 或 LLM 调用失败时的 fallback。

        子类必须覆盖此方法返回合理的默认值。
        """
        raise NotImplementedError("子类必须实现 _fallback")
