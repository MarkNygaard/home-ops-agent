"""Tests for api/settings.py — settings management and API key masking."""

from home_ops_agent.api.settings import ALLOWED_SETTING_KEYS, _mask_key

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
    # 9 chars — key[:7] + "..." + key[-4:]
    result = _mask_key("123456789")
    assert result == "1234567...6789"


# --- Settings whitelist test ---


def test_allowed_keys_contains_critical_keys():
    """Verify critical keys are in the actual whitelist."""
    assert "agent_enabled" in ALLOWED_SETTING_KEYS
    assert "pr_mode" in ALLOWED_SETTING_KEYS
    assert "anthropic_api_key" in ALLOWED_SETTING_KEYS


def test_allowed_keys_excludes_dangerous_keys():
    """Verify dangerous keys are NOT in the actual whitelist."""
    assert "database_url" not in ALLOWED_SETTING_KEYS
    assert "github_token" not in ALLOWED_SETTING_KEYS
    assert "session_secret" not in ALLOWED_SETTING_KEYS


def test_allowed_keys_includes_all_model_keys():
    """Verify all model keys are settable via the API."""
    model_keys = {
        "model_pr_review",
        "model_alert_triage",
        "model_alert_fix",
        "model_code_fix",
        "model_deep_review",
        "model_chat",
    }
    assert model_keys.issubset(ALLOWED_SETTING_KEYS)


# --- Prompt defaults test ---


def test_prompt_defaults_available():
    from home_ops_agent.agent.prompts import DEFAULTS as PROMPT_DEFAULTS

    assert "cluster_context" in PROMPT_DEFAULTS
    assert "pr_review" in PROMPT_DEFAULTS
    assert "alert_response" in PROMPT_DEFAULTS
    assert "chat" in PROMPT_DEFAULTS
