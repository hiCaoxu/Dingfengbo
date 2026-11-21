"""AI 客户端工厂与调用封装。"""

from __future__ import annotations

from typing import Any

from ...config import load_settings
from .openai_compat import OpenAICompatProvider
from .provider import AIError


class AIConfigError(AIError):
    """AI 未配置或配置不完整。"""


def _parts(settings: dict[str, Any]) -> tuple[str, str, str]:
    base_url = (settings.get("ai_base_url") or "").strip()
    api_key = (settings.get("ai_api_key") or "").strip()
    model = (settings.get("ai_model") or "").strip()
    return base_url, api_key, model


def ai_configured(settings: dict[str, Any] | None = None) -> bool:
    base_url, api_key, model = _parts(settings or load_settings())
    return bool(base_url and api_key and model)


def create_provider(settings: dict[str, Any] | None = None) -> OpenAICompatProvider:
    base_url, api_key, model = _parts(settings or load_settings())
    if not api_key:
        raise AIConfigError("未配置 API Key，请先在设置中配置")
    if not base_url:
        raise AIConfigError("未配置服务地址（base_url）")
    if not model:
        raise AIConfigError("未配置模型名")
    return OpenAICompatProvider(base_url, api_key, model)


def chat(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> str:
    return create_provider(settings).chat(messages)
