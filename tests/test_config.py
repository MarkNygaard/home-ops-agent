"""Tests for config.py — application settings."""

from home_ops_agent.config import Settings


def test_default_model_values():
    s = Settings()
    # Aliases, not pinned versions: the CLI resolves these to the current model.
    assert s.model_pr_review == "claude-code/haiku"
    assert s.model_alert_triage == "claude-code/haiku"
    assert s.model_alert_fix == "claude-code/sonnet"
    assert s.model_code_fix == "claude-code/sonnet"
    assert s.model_deep_review == "claude-code/opus"
    assert s.model_chat == "claude-code/sonnet"


def test_default_intervals():
    s = Settings()
    assert s.pr_check_interval_seconds == 1800
    assert s.alert_cooldown_seconds == 900


def test_identifying_settings_have_no_default():
    """A default here would point a fresh deploy at someone else's cluster.

    `github_repo` in particular: a wrong value aims the PR agent at a repository
    the operator does not own.
    """
    s = Settings()
    assert s.github_repo == ""
    assert s.cluster_domain == ""
    assert s.base_url == ""


def test_missing_required_settings_are_named():
    """An unset setting should be reported at startup, not as a later 404."""
    from home_ops_agent.config import missing_required

    reported = " ".join(missing_required())
    assert "GITHUB_REPO" in reported
    assert "BASE_URL" in reported


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


def test_no_personal_identifiers_in_tracked_source():
    """This repo is public: nobody's own cluster should be baked into it.

    A deny-list rather than a domain regex: the regex version matched every
    attribute access (`session.commit`, `logger.info`) and caught nothing real.
    This catches the actual regression — someone reintroducing a specific host,
    repo or account that belongs to one deployment.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    # Add to this list rather than removing the check.
    forbidden = ("mnygaard", "marknygaard", "mark.nygaard")

    searched = [
        *root.glob("src/**/*.py"),
        *root.glob("web/src/**/*.ts"),
        *root.glob("web/src/**/*.tsx"),
        root / "README.md",
        root / "CLAUDE.md",
        root / "architecture.excalidraw",
    ]

    offenders = [
        f"{path.relative_to(root)}: {marker}"
        for path in searched
        if path.exists()
        for marker in forbidden
        if marker in path.read_text(encoding="utf-8").lower()
    ]

    assert not offenders, "personal identifiers in a public repo: " + "; ".join(offenders)


def test_config_declares_no_concrete_public_host():
    """No default may name a real deployment's address.

    The repo slug is covered by test_identifying_settings_have_no_default; this
    catches a hostname creeping back into a default. In-cluster service DNS and
    localhost are conventional and exempt.
    """
    import pathlib
    import re

    config = (
        pathlib.Path(__file__).resolve().parent.parent / "src/home_ops_agent/config.py"
    ).read_text(encoding="utf-8")

    for value in re.findall(r'^\s*\w+: str = "([^"]+)"', config, re.M):
        if ".svc.cluster.local" in value or "localhost" in value:
            continue
        assert not re.match(r"^https?://[a-z0-9.-]+\.[a-z]{2,}", value), (
            f"config default names a concrete host: {value!r}"
        )
