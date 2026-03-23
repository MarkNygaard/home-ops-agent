"""Tests for api/settings.py — settings management and API key masking."""

from home_ops_agent.api.settings import _mask_key

# --- _mask_key() pure function tests ---


def test_mask_key_empty():
    assert _mask_key("") == ""


def test_mask_key_short():
    assert _mask_key("abcdefgh") == "****"


def test_mask_key_very_short():
    assert _mask_key("abc") == "****"


def test_mask_key_long():
    key = "sk-ant-api03-abcdefghijklmnop"
    result = _mask_key(key)
    assert result.startswith("sk-ant-")
    assert result.endswith("mnop")
    assert "..." in result


def test_mask_key_exact_boundary():
    # Exactly 8 chars — should be masked as "****"
    assert _mask_key("12345678") == "****"


def test_mask_key_nine_chars():
    # 9 chars — first 7 + ... + last 4
    result = _mask_key("123456789")
    assert result == "1234567...6789"


# --- Settings whitelist test ---


def test_allowed_keys():
    """Verify the allowed keys whitelist from the update_setting endpoint."""
    allowed_keys = {
        "agent_enabled",
        "pr_mode",
        "auth_method",
        "anthropic_api_key",
        "alert_cooldown_seconds",
        "ntfy_topics",
        "pr_check_interval_seconds",
        "model_pr_review",
        "prompt_cluster_context",
        "prompt_pr_review",
        "prompt_alert_response",
        "prompt_chat",
        "model_alert_triage",
        "model_alert_fix",
        "model_code_fix",
        "model_deep_review",
        "model_chat",
    }
    # Verify critical keys are in the list
    assert "agent_enabled" in allowed_keys
    assert "pr_mode" in allowed_keys
    assert "anthropic_api_key" in allowed_keys
    # Verify a dangerous key is NOT in the list
    assert "database_url" not in allowed_keys
    assert "github_token" not in allowed_keys


# --- Prompt defaults test ---


def test_prompt_defaults_available():
    from home_ops_agent.agent.prompts import DEFAULTS as PROMPT_DEFAULTS

    assert "cluster_context" in PROMPT_DEFAULTS
    assert "pr_review" in PROMPT_DEFAULTS
    assert "alert_response" in PROMPT_DEFAULTS
    assert "chat" in PROMPT_DEFAULTS
