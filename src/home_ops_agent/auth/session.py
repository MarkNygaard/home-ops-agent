"""Simple session management for the web UI."""

import secrets
from datetime import UTC, datetime, timedelta

# In-memory session store (simple for single-pod deployment)
_sessions: dict[str, dict] = {}


def create_session(data: dict | None = None) -> str:
    """Create a new session and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "data": data or {},
        "created_at": datetime.now(UTC),
    }
    return session_id


def get_session(session_id: str) -> dict | None:
    """Get session data by ID."""
    session = _sessions.get(session_id)
    if session is None:
        return None

    # Expire sessions after 24 hours
    if datetime.now(UTC) - session["created_at"] > timedelta(hours=24):
        _sessions.pop(session_id, None)
        return None

    return session["data"]


def delete_session(session_id: str):
    """Delete a session."""
    _sessions.pop(session_id, None)
