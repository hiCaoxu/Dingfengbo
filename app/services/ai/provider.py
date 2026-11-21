"""AI Provider 抽象：远程/本地大模型的统一调用接口。

本地大模型（Ollama 等）未来作为新的 Provider 实现接入，聊天与功能层无需改动。
"""

from __future__ import annotations

from typing import Protocol


class AIError(Exception):
    """AI 调用失败的统一异常；message 面向最终用户。"""


class AIProvider(Protocol):
    """大模型 Provider 接口。"""

    def chat(self, messages: list[dict[str, str]]) -> str:
        """发送 messages（含 role/content），返回助手回复文本。"""
        ...
