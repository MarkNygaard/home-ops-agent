"""Tests for the chat socket's Origin check.

WebSockets are not covered by the same-origin policy — a browser will connect to
a different host and hand the page the result — so the server has to check
`Origin` itself. Without it, any page open in a browser on the trusted VLAN
could drive the agent, and the chat can call every tool the agent has.
"""

from __future__ import annotations

from home_ops_agent.api.chat import _origin_allowed
from home_ops_agent.config import settings


def test_the_agents_own_ui_is_allowed(monkeypatch):
    monkeypatch.setattr(settings, "base_url", "https://agent.mnygaard.io")
    assert _origin_allowed("https://agent.mnygaard.io") is True
    # A trailing slash is the same origin, and browsers are inconsistent.
    assert _origin_allowed("https://agent.mnygaard.io/") is True


def test_another_site_is_refused(monkeypatch):
    """The vector: a page you visit, in a browser that can reach the agent."""
    monkeypatch.setattr(settings, "base_url", "https://agent.mnygaard.io")
    for origin in (
        "https://evil.example",
        "http://agent.mnygaard.io",  # scheme matters
        "https://agent.mnygaard.io.evil.example",  # prefix trick
        "null",
    ):
        assert _origin_allowed(origin) is False, origin


def test_dev_servers_are_allowed(monkeypatch):
    monkeypatch.setattr(settings, "base_url", "")
    assert _origin_allowed("http://localhost:3000") is True


def test_no_origin_is_allowed(monkeypatch):
    """Browsers always send Origin on a WebSocket handshake, so its absence
    means a non-browser client — a script, a probe. Those are not what
    cross-site request forgery is about, and refusing them would break local
    tooling while stopping nothing."""
    monkeypatch.setattr(settings, "base_url", "https://agent.mnygaard.io")
    assert _origin_allowed(None) is True
    assert _origin_allowed("") is True


def test_the_socket_closes_before_accepting():
    """Closing after accept would hand the page an open socket and then tell it
    off for using one."""
    import inspect

    from home_ops_agent.api import chat

    source = inspect.getsource(chat.websocket_chat)
    assert source.index("_origin_allowed") < source.index("await websocket.accept()")
