"""
Tests for the product-reader-ai agent.

These tests are unit-level and do NOT require real AWS credentials,
a running GitHub MCP server, or a real browser – all external calls
are mocked at the boundary.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _minimal_env(overrides: dict | None = None) -> dict:
    """Return the minimum required environment for the agent."""
    base = {
        "GITHUB_TOKEN": "ghp_test",
        "TARGET_REPO": "owner/repo",
        "WEBSHOP_URLS": "https://example-shop.com/products",
        "BASE_BRANCH": "main",
        "NEW_BRANCH": "feature/product-profile",
        "VALIDATE_WORKFLOW": "validate.yml",
        "PUBLISH_WORKFLOW": "publish.yml",
    }
    if overrides:
        base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Prompt tests
# ─────────────────────────────────────────────────────────────────────────────


def test_system_prompt_is_non_empty():
    from prompts import SYSTEM_PROMPT

    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT.strip()) > 0


def test_system_prompt_mentions_examples():
    from prompts import SYSTEM_PROMPT

    # The agent must be instructed to look for examples in the target repository
    assert "example" in SYSTEM_PROMPT.lower(), (
        "SYSTEM_PROMPT should instruct the agent to check for example profiles"
    )


def test_task_prompt_template_contains_placeholders():
    from prompts import TASK_PROMPT_TEMPLATE

    required_placeholders = [
        "{webshop_urls}",
        "{target_repo}",
        "{base_branch}",
        "{new_branch}",
        "{examples_path}",
        "{validate_workflow}",
        "{publish_workflow}",
    ]
    for placeholder in required_placeholders:
        assert placeholder in TASK_PROMPT_TEMPLATE, (
            f"TASK_PROMPT_TEMPLATE is missing placeholder: {placeholder}"
        )


def test_task_prompt_format():
    from prompts import TASK_PROMPT_TEMPLATE

    rendered = TASK_PROMPT_TEMPLATE.format(
        webshop_urls="  - https://example.com",
        target_repo="owner/repo",
        base_branch="main",
        new_branch="feature/test",
        examples_path="examples",
        validate_workflow="validate.yml",
        publish_workflow="publish.yml",
    )
    assert "https://example.com" in rendered
    assert "owner/repo" in rendered
    assert "feature/test" in rendered
    assert "examples" in rendered


# ─────────────────────────────────────────────────────────────────────────────
# Configuration / validation tests
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_config_raises_when_github_token_missing():
    env = _minimal_env()
    env.pop("GITHUB_TOKEN")
    with patch.dict(os.environ, env, clear=True):
        # Re-load module globals after patching env
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
            agent_module._validate_config()


def test_validate_config_raises_when_target_repo_missing():
    env = _minimal_env()
    env.pop("TARGET_REPO")
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        with pytest.raises(EnvironmentError, match="TARGET_REPO"):
            agent_module._validate_config()


def test_validate_config_raises_when_webshop_urls_missing():
    env = _minimal_env()
    env.pop("WEBSHOP_URLS")
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        with pytest.raises(EnvironmentError, match="WEBSHOP_URLS"):
            agent_module._validate_config()


def test_validate_config_passes_with_all_required_vars():
    env = _minimal_env()
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        # Should not raise
        agent_module._validate_config()


# ─────────────────────────────────────────────────────────────────────────────
# Task prompt builder test
# ─────────────────────────────────────────────────────────────────────────────


def test_build_task_prompt_single_url():
    env = _minimal_env({"WEBSHOP_URLS": "https://shop.example.com"})
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        prompt = agent_module._build_task_prompt()

    assert "https://shop.example.com" in prompt
    assert "owner/repo" in prompt
    assert "feature/product-profile" in prompt
    assert "validate.yml" in prompt
    assert "publish.yml" in prompt
    # Default examples path should appear in the prompt
    assert "examples" in prompt


def test_build_task_prompt_custom_examples_path():
    env = _minimal_env({"EXAMPLES_PATH": "samples/profiles"})
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        prompt = agent_module._build_task_prompt()

    assert "samples/profiles" in prompt


def test_build_task_prompt_multiple_urls():
    env = _minimal_env(
        {
            "WEBSHOP_URLS": (
                "https://shop1.example.com, https://shop2.example.com,"
                "https://shop3.example.com"
            )
        }
    )
    with patch.dict(os.environ, env, clear=True):
        import importlib

        import agent as agent_module

        importlib.reload(agent_module)
        prompt = agent_module._build_task_prompt()

    assert "https://shop1.example.com" in prompt
    assert "https://shop2.example.com" in prompt
    assert "https://shop3.example.com" in prompt
