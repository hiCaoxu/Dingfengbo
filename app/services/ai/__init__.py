"""AI 助手服务层：Provider 抽象、OpenAI 兼容客户端与 @引用上下文读取。"""

from .client import AIConfigError, ai_configured, chat, create_provider
from .provider import AIError, AIProvider

import logging

logger = logging.getLogger(__name__)




__all__ = [
    "AIError",
    "AIProvider",
    "AIConfigError",
    "ai_configured",
    "chat",
    "create_provider",
]
