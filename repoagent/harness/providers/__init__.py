"""重导出 LLM Provider Abstraction 与主要 Concrete Provider。

PEP 562 Lazy Export 让导入 `repoagent.harness.providers` 或其 Submodule 时不提前加载 litellm，避免 Provider
SDK 主导 CLI Cold Start。Public Surface 包含 LLMProvider/LLMResponse、LiteLLM、Codex 与 Azure；
未知 Symbol 明确 AttributeError，`__dir__` 仍稳定展示可用名称。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repoagent.harness.providers.azure_openai_provider import AzureOpenAIProvider
    from repoagent.harness.providers.base import LLMProvider, LLMResponse
    from repoagent.harness.providers.litellm_provider import LiteLLMProvider
    from repoagent.harness.providers.openai_codex_provider import OpenAICodexProvider

__all__ = ["LLMProvider", "LLMResponse", "LiteLLMProvider", "OpenAICodexProvider", "AzureOpenAIProvider"]

# 惰性重新导出（PEP 562）：导入 Provider 子模块时不得提前加载
# ``litellm_provider`` -> litellm，否则会主导 CLI 冷启动耗时。
_LAZY_EXPORTS = {
    "LLMProvider": "repoagent.harness.providers.base",
    "LLMResponse": "repoagent.harness.providers.base",
    "LiteLLMProvider": "repoagent.harness.providers.litellm_provider",
    "OpenAICodexProvider": "repoagent.harness.providers.openai_codex_provider",
    "AzureOpenAIProvider": "repoagent.harness.providers.azure_openai_provider",
}


def __getattr__(name: str) -> object:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(__all__)
