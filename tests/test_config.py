"""Tests for config.py — application settings."""

from home_ops_agent.config import Settings


def test_default_model_values():
    s = Settings()
    assert s.model_pr_review == "claude-haiku-4-5"
    assert s.model_alert_triage == "claude-haiku-4-5"
    assert s.model_alert_fix == "claude-sonnet-4-6"
    assert s.model_code_fix == "claude-sonnet-4-6"
    assert s.model_deep_review == "claude-opus-4-6"
    assert s.model_chat == "claude-sonnet-4-6"


def test_default_intervals():
    s = Settings()
    assert s.pr_check_interval_seconds == 1800
    assert s.alert_cooldown_seconds == 900


def test_default_github_repo():
    s = Settings()
    assert s.github_repo == "MarkNygaard/home-ops"


def test_default_cluster_domain():
    s = Settings()
    assert s.cluster_domain == "mnygaard.io"


def test_database_url_default():
    s = Settings()
    assert "asyncpg" in s.database_url


# --- Environment variable override tests ---


def test_env_override_model(monkeypatch):
    monkeypatch.setenv("MODEL_PR_REVIEW", "claude-opus-4-6")
    s = Settings()
    assert s.model_pr_review == "claude-opus-4-6"


def test_env_override_interval(monkeypatch):
    monkeypatch.setenv("PR_CHECK_INTERVAL_SECONDS", "600")
    s = Settings()
    assert s.pr_check_interval_seconds == 600


def test_env_override_github_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "other-owner/other-repo")
    s = Settings()
    assert s.github_repo == "other-owner/other-repo"


def test_env_override_cluster_domain(monkeypatch):
    monkeypatch.setenv("CLUSTER_DOMAIN", "example.com")
    s = Settings()
    assert s.cluster_domain == "example.com"


def test_env_override_cooldown(monkeypatch):
    monkeypatch.setenv("ALERT_COOLDOWN_SECONDS", "300")
    s = Settings()
    assert s.alert_cooldown_seconds == 300
