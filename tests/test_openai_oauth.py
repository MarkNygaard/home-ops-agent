"""Tests for the ChatGPT PKCE sign-in.

The parts worth guarding are the ones that are silently wrong rather than
loudly broken: a challenge that does not match its verifier, a state check that
never fires, and an account id that is only present inside the JWT.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from home_ops_agent.auth import openai_oauth


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_challenge_is_the_sha256_of_the_verifier():
    """If these drift the exchange fails at the very last step, after the
    operator has already signed in — the worst place to discover it."""
    started = openai_oauth.start_login()
    params = parse_qs(urlsplit(started["authorize_url"]).query)

    expected = _b64url(hashlib.sha256(started["verifier"].encode()).digest())
    assert params["code_challenge"][0] == expected
    assert params["code_challenge_method"][0] == "S256"


def test_authorize_url_requests_a_refresh_token():
    """offline_access is what makes the credential outlive the hour.

    Without it the exchange succeeds and returns no refresh token, which then
    surfaces much later as a credential that cannot be renewed.
    """
    started = openai_oauth.start_login()
    params = parse_qs(urlsplit(started["authorize_url"]).query)
    assert "offline_access" in params["scope"][0]


def test_not_the_device_code_flow():
    """Device-code auth is gated behind a ChatGPT workspace security setting.

    Where an admin has disabled it there is nothing the operator can do, so the
    authorization-code flow is deliberate rather than incidental.
    """
    started = openai_oauth.start_login()
    params = parse_qs(urlsplit(started["authorize_url"]).query)
    assert params["response_type"][0] == "code"


def test_each_start_is_unique():
    """A reused verifier or state would let one sign-in complete another."""
    a, b = openai_oauth.start_login(), openai_oauth.start_login()
    assert a["verifier"] != b["verifier"]
    assert a["state"] != b["state"]


@pytest.mark.parametrize(
    "pasted",
    [
        "http://localhost:1455/auth/callback?code=abc123&state=xyz",
        "http://localhost:1455/auth/callback?state=xyz&code=abc123#frag",
        "?code=abc123&state=xyz",
    ],
)
def test_extract_param_tolerates_what_a_browser_bar_yields(pasted):
    """The operator is copying out of an address bar, not calling an API."""
    assert openai_oauth.extract_param(pasted, "code") == "abc123"


def test_account_id_comes_from_the_jwt_not_the_response():
    """The token response body does not carry it; only the access token does."""
    claims = {openai_oauth.JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-123"}}
    token = f"header.{_b64url(json.dumps(claims).encode())}.signature"
    assert openai_oauth.account_id_from_jwt(token) == "acct-123"


def test_account_id_missing_is_reported_not_guessed():
    assert openai_oauth.account_id_from_jwt("not.a.jwt") is None
    claims = {"unrelated": True}
    token = f"header.{_b64url(json.dumps(claims).encode())}.signature"
    assert openai_oauth.account_id_from_jwt(token) is None


@pytest.mark.asyncio
async def test_state_mismatch_is_refused():
    """Guards against completing a flow with someone else's redirect."""
    with pytest.raises(ValueError, match="state mismatch"):
        await openai_oauth.complete_login(
            "http://localhost:1455/auth/callback?code=abc&state=attacker",
            "expected",
            "verifier",
        )


@pytest.mark.asyncio
async def test_empty_paste_is_refused():
    with pytest.raises(ValueError, match="no authorization code"):
        await openai_oauth.complete_login("   ", "state", "verifier")


@pytest.mark.asyncio
async def test_exchange_stores_tokens_and_reads_the_account_id(monkeypatch):
    claims = {openai_oauth.JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-999"}}
    access = f"header.{_b64url(json.dumps(claims).encode())}.sig"
    stored: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "access_token": access,
                "refresh_token": "refresh-999",
                "expires_in": 3600,
            }

    class _Client:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            return _Resp()

    async def _store(values):
        stored.update(values)

    monkeypatch.setattr(openai_oauth.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(openai_oauth.creds, "store_settings", _store)

    result = await openai_oauth.complete_login("code-abc", "state", "verifier")

    assert result["account_id"] == "acct-999"
    assert stored[openai_oauth.creds.OPENAI_ACCESS_TOKEN_KEY] == access
    assert stored[openai_oauth.creds.OPENAI_REFRESH_TOKEN_KEY] == "refresh-999"
    assert stored[openai_oauth.creds.OPENAI_ACCOUNT_ID_KEY] == "acct-999"
    # An expiry must be written, or ensure_openai_token cannot tell when to renew.
    assert stored[openai_oauth.creds.OPENAI_EXPIRES_AT_KEY]


@pytest.mark.asyncio
async def test_provider_error_is_surfaced_verbatim(monkeypatch):
    """The operator needs OpenAI's own words; a generic 500 helps nobody."""

    class _Resp:
        status_code = 400

        def json(self):
            return {"error_description": "PKCE verification failed"}

    class _Client:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(openai_oauth.httpx, "AsyncClient", _Client)

    with pytest.raises(ValueError, match="PKCE verification failed"):
        await openai_oauth.complete_login("code-abc", "state", "verifier")


@pytest.mark.asyncio
async def test_missing_refresh_token_fails_loudly(monkeypatch):
    """A credential with no refresh token dies in an hour, silently."""

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "a.b.c"}

    class _Client:
        def __init__(self, *_a, **_k): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(openai_oauth.httpx, "AsyncClient", _Client)

    with pytest.raises(ValueError, match="missing access_token or refresh_token"):
        await openai_oauth.complete_login("code-abc", "state", "verifier")
