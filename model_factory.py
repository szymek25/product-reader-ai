"""
Unified model factory for product-reader-ai.

Controlled by the LLM_PROVIDER environment variable:

  LLM_PROVIDER=bedrock   (default)
    Uses Amazon Bedrock via BedrockModel.
    Relevant env vars: AWS_REGION, BEDROCK_MODEL_ID (+ per-agent overrides).

  LLM_PROVIDER=ollama
    Uses a local Ollama server via LiteLLM.
    Relevant env vars: OLLAMA_BASE_URL, OLLAMA_MODEL_ID (+ per-agent overrides).

  LLM_PROVIDER=openai_compatible
    Uses any OpenAI-compatible local HTTP endpoint (LM Studio, vLLM, etc.)
    via LiteLLM.
    Relevant env vars: LOCAL_LLM_BASE_URL, LOCAL_LLM_API_KEY, LOCAL_LLM_MODEL_ID
    (+ per-agent overrides).

Per-agent model overrides
─────────────────────────
Each agent can point to a different model by setting:
  BEDROCK_MODEL_ID              main agent (bedrock provider)
  PRODUCT_PAGE_AGENT_MODEL_ID   product-page sub-agent
  PRODUCT_LINKS_AGENT_MODEL_ID  product-links sub-agent

For local providers the same pattern applies via the per-agent env vars defined
in .env.example.  When a per-agent var is absent the agent-class default is used.
"""

from __future__ import annotations

import os

# ── Constants (never change at runtime) ────────────────────────────────────
_BEDROCK_DEFAULT: str = "us.anthropic.claude-sonnet-4-5-v1:0"
_OLLAMA_DEFAULT: str = "llama3.2"
_LOCAL_LLM_DEFAULT: str = "openai/local-model"


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "bedrock").lower()


def _local_model_id(env_var: str, default: str) -> str:
    """Return model ID from env var, falling back to *default*."""
    return os.environ.get(env_var, "") or default


def build_model(model_id: str, max_tokens: int = 2048):
    """
    Build and return a Strands model instance for the given *model_id*.

    The type of model returned depends on ``LLM_PROVIDER``:

    * ``bedrock``          → ``BedrockModel(model_id=model_id, ...)``
    * ``ollama``           → ``LiteLLMModel`` targeting the local Ollama server.
                             *model_id* is used as the Ollama model name.
    * ``openai_compatible``→ ``LiteLLMModel`` targeting ``LOCAL_LLM_BASE_URL``.
                             *model_id* is passed through as-is.

    Args:
        model_id:   Model identifier.  Meaning depends on the active provider.
        max_tokens: Maximum tokens for generated responses.

    Returns:
        A Strands model instance compatible with ``Agent(model=...)``.
    """
    provider = _provider()

    if provider == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=model_id,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            max_tokens=max_tokens,
        )

    if provider == "ollama":
        from strands.models.litellm import LiteLLMModel

        return LiteLLMModel(
            model_id=f"ollama/{model_id}",
            params={
                "api_base": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                "max_tokens": max_tokens,
            },
        )

    if provider == "openai_compatible":
        from strands.models.litellm import LiteLLMModel

        return LiteLLMModel(
            model_id=model_id,
            params={
                "api_base": os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"),
                "api_key": os.environ.get("LOCAL_LLM_API_KEY", "local"),
                "max_tokens": max_tokens,
            },
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. "
        "Supported values: bedrock, ollama, openai_compatible."
    )


# ── Per-agent model ID helpers ──────────────────────────────────────────────


def main_agent_model_id() -> str:
    provider = _provider()
    if provider == "bedrock":
        return _local_model_id("BEDROCK_MODEL_ID", _BEDROCK_DEFAULT)
    if provider == "ollama":
        return _local_model_id("OLLAMA_MODEL_ID", _OLLAMA_DEFAULT)
    return _local_model_id("LOCAL_LLM_MODEL_ID", _LOCAL_LLM_DEFAULT)


def product_page_agent_model_id() -> str:
    provider = _provider()
    if provider == "bedrock":
        return _local_model_id("PRODUCT_PAGE_AGENT_MODEL_ID", main_agent_model_id())
    if provider == "ollama":
        return _local_model_id("PRODUCT_PAGE_AGENT_OLLAMA_MODEL_ID", _local_model_id("OLLAMA_MODEL_ID", _OLLAMA_DEFAULT))
    return _local_model_id("PRODUCT_PAGE_AGENT_LOCAL_MODEL_ID", _local_model_id("LOCAL_LLM_MODEL_ID", _LOCAL_LLM_DEFAULT))


def product_links_agent_model_id() -> str:
    provider = _provider()
    if provider == "bedrock":
        return _local_model_id("PRODUCT_LINKS_AGENT_MODEL_ID", main_agent_model_id())
    if provider == "ollama":
        return _local_model_id("PRODUCT_LINKS_AGENT_OLLAMA_MODEL_ID", _local_model_id("OLLAMA_MODEL_ID", _OLLAMA_DEFAULT))
    return _local_model_id("PRODUCT_LINKS_AGENT_LOCAL_MODEL_ID", _local_model_id("LOCAL_LLM_MODEL_ID", _LOCAL_LLM_DEFAULT))
