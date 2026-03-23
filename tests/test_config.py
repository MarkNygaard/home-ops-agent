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
