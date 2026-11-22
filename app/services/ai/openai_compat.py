"""OpenAI 兼容 Provider：用 httpx 调用 /chat/completions，对接任意国产大模型。"""

from __future__ import annotations

import logging
import time

import httpx

from .provider import AIError

logger = logging.getLogger(__name__)

# 429/5xx 多为瞬时抖动：有限退避重试，避免用户看到一个「服务暂时不可用」就以为挂了。
_MAX_ATTEMPTS = 3
_RETRY_DELAYS = (0.6, 1.5)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
        for attempt in range(_MAX_ATTEMPTS):
            retry_left = attempt + 1 < _MAX_ATTEMPTS
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                # 不记录请求/返回内容，仅记录错误类型与状态码
                logger.warning("AI 请求被拒：status=%s", code)
                if code in _RETRYABLE_STATUS and retry_left:
                    time.sleep(_RETRY_DELAYS[attempt])
                    continue
                if code in (401, 403):
                    raise AIError("API Key 无效或没有权限，请检查设置") from exc
                if code == 429:
                    raise AIError("请求过于频繁或额度不足，请稍后再试") from exc
                raise AIError(f"模型服务返回错误（{code}）") from exc
            except httpx.RequestError as exc:
                logger.warning("AI 网络错误：%s", type(exc).__name__)
                if retry_left:
                    time.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise AIError("网络连接失败，请检查网络或服务地址") from exc
            except ValueError as exc:
                # 200 但不是 JSON（常见于被网关/代理拦截后返回的 HTML 错误页）。
                # 以前这里会抛未捕获的 ValueError，直接在后台线程里静默退出，
                # 客户端就永远停在「正在思考…」。
                logger.warning("AI 返回非 JSON 内容")
                raise AIError("模型服务返回了非 JSON 内容，请检查服务地址或代理设置") from exc
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                logger.warning("AI 返回格式异常")
                raise AIError("模型返回格式异常，请尝试更换模型") from exc
            if not isinstance(content, str) or not content.strip():
                # 推理类模型在 tool-call/超长思考时可能返回 content=null。
                raise AIError("模型返回内容为空，请稍后重试或更换模型")
            return content
        raise AIError("模型服务暂时不可用，请稍后再试")
