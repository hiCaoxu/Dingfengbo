"""OpenAI 兼容 Provider：用 httpx 调用 /chat/completions，对接任意国产大模型。"""

from __future__ import annotations

import logging

import httpx

from .provider import AIError

logger = logging.getLogger(__name__)


class OpenAICompatProvider:
    """对接 OpenAI 兼容协议的大模型服务。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            # 不记录请求/返回内容，仅记录错误类型与状态码
            logger.warning("AI 请求被拒：status=%s", code)
            if code in (401, 403):
                raise AIError("API Key 无效或没有权限，请检查设置") from exc
            if code == 429:
                raise AIError("请求过于频繁或额度不足，请稍后再试") from exc
            raise AIError(f"模型服务返回错误（{code}）") from exc
        except httpx.RequestError as exc:
            logger.warning("AI 网络错误：%s", type(exc).__name__)
            raise AIError("网络连接失败，请检查网络或服务地址") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("AI 返回格式异常")
            raise AIError("模型返回格式异常，请尝试更换模型") from exc
