"""Tests for auth/session.py — in-memory session management."""

from datetime import UTC, datetime, timedelta

from home_ops_agent.auth.session import (
    _sessions,
    create_session,
    delete_session,
    get_session,
)


def test_create_session_returns_id():
    session_id = create_session()
    assert isinstance(session_id, str)
    assert len(session_id) > 20  # token_urlsafe(32) produces ~43 chars


def test_create_session_with_data():
    session_id = create_session({"foo": "bar"})
    assert session_id in _sessions
    assert _sessions[session_id]["data"] == {"foo": "bar"}


def test_get_session_returns_data():
    session_id = create_session({"key": "value"})
    data = get_session(session_id)
    assert data == {"key": "value"}


def test_get_session_nonexistent_returns_none():
    assert get_session("nonexistent-id") is None


def test_get_session_expired():
    session_id = create_session({"data": "old"})
    # Backdate the session to 25 hours ago
    _sessions[session_id]["created_at"] = datetime.now(UTC) - timedelta(hours=25)
    assert get_session(session_id) is None
    # Session should be cleaned up
    assert session_id not in _sessions


def test_get_session_not_yet_expired():
    session_id = create_session({"data": "fresh"})
    _sessions[session_id]["created_at"] = datetime.now(UTC) - timedelta(hours=23)
    assert get_session(session_id) == {"data": "fresh"}


def test_delete_session():
    session_id = create_session()
    delete_session(session_id)
    assert get_session(session_id) is None


def test_delete_session_nonexistent():
    # Should not raise
    delete_session("does-not-exist")


def test_create_session_empty_data_defaults():
    session_id = create_session()
    data = get_session(session_id)
    assert data == {}
