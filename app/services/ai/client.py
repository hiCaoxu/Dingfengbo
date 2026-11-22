"""AI 客户端工厂与调用封装。"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

from ...config import load_settings
from .openai_compat import OpenAICompatProvider
from .provider import AIError

logger = logging.getLogger(__name__)


class AIConfigError(AIError):
    """AI 未配置或配置不完整。"""


def _validate_base_url(base_url: str) -> str:
    """校验服务地址：只接受 http(s)，并对本机/内网地址显式告警。

    ``ai_base_url`` 可自由填写，而此时请求会带上真实 API Key 与用户 @引用的笔记内容；
    一个手误或诱导成 ``http://evil`` 的地址就能把它们发走。这里不阻断自建代理（那是
    合法用法），但要求 scheme 合法，并对私网目标留下明确警告。
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise AIConfigError("AI 服务地址必须以 http:// 或 https:// 开头")
    host = (parts.hostname or "").lower()
    if not host:
        raise AIConfigError("AI 服务地址缺少主机名")
    private = host == "localhost" or host.endswith(".local")
    try:
        ip = ipaddress.ip_address(host)
        private = private or ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    if private:
        logger.warning("AI 服务地址指向本机/内网：%s（API Key 与引用内容会发往该地址）", base_url)
    if parts.scheme == "http":
        logger.warning("AI 服务地址使用明文 HTTP：%s", base_url)
    return base_url


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
    return OpenAICompatProvider(_validate_base_url(base_url), api_key, model)


def chat(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> str:
    return create_provider(settings).chat(messages)
