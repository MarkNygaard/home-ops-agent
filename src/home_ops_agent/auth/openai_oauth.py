"""ChatGPT sign-in: the OAuth authorization-code flow with PKCE.

Before this, the only way to give the agent a ChatGPT credential was to obtain
tokens elsewhere -- typically from a local ``codex`` CLI -- and paste all three
into ``POST /api/auth/openai``. Nothing renewed them but the refresh endpoint,
so once a refresh token was spent the credential was simply dead until someone
noticed and repeated the extraction by hand. It sat expired for seventeen days.

**Authorization code with PKCE, not device code.** OpenAI gates device-code
authorisation behind a ChatGPT *workspace* security setting; where an admin has
turned it off the device flow fails with "contact your workspace admin" and
there is nothing the operator can do about it. PKCE carries no such condition.

**No callback server.** The redirect points at ``localhost:1455``, which is
where the Codex CLI would be listening and where this server is not. The
operator signs in in their own browser, the redirect fails to load, and they
paste the resulting URL -- which carries ``code`` and ``state`` in the address
bar -- back into :func:`complete_login`. That is what makes this usable against
a server in a cluster, and from a phone.

**Stateless between the two calls.** The verifier and state travel to the client
and back rather than being held server-side, so a restart mid-flow costs nothing
and two operators cannot collide.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from home_ops_agent.agent import providers
from home_ops_agent.auth import credentials as creds

logger = logging.getLogger(__name__)

# Where the Codex CLI listens. This server does not, and does not need to.
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "codex_cli_rs"

# Custom claim on the access token that carries the ChatGPT account id. It is
# not in the token response body, only inside the JWT.
JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def start_login() -> dict[str, str]:
    """Begin the flow: return the authorize URL plus the PKCE material.

    The caller hands ``verifier`` and ``state`` back to :func:`complete_login`.
    Both are single use.
    """
    verifier = _b64url(secrets.token_bytes(96))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_hex(16)

    query = urlencode(
        {
            "response_type": "code",
            "client_id": providers.OPENAI_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": ORIGINATOR,
        }
    )
    return {
        "authorize_url": f"{providers.OPENAI_AUTHORIZE_URL}?{query}",
        "state": state,
        "verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }


def extract_param(value: str, key: str) -> str | None:
    """Pull one query parameter out of a pasted redirect URL.

    Tolerant on purpose: the operator is copying out of an address bar, so the
    value may be a full URL, a bare query string, or carry a fragment.
    """
    parts = urlsplit(value.strip())
    query = parts.query or (value.split("?", 1)[1] if "?" in value else "")
    query = query.split("#", 1)[0]
    found = parse_qs(query).get(key)
    return found[0] if found else None


def account_id_from_jwt(access_token: str) -> str | None:
    """Read the ChatGPT account id out of the access token's claims.

    The token response does not carry it; only the JWT does. Padding is added
    back because the segments are base64url without it.
    """
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return None
    account = (claims.get(JWT_CLAIM_PATH) or {}).get("chatgpt_account_id")
    return account or None


async def complete_login(redirect: str, state: str, verifier: str) -> dict[str, str]:
    """Exchange the pasted redirect for tokens and store them.

    Raises :class:`ValueError` with a message meant for the operator.
    """
    code = extract_param(redirect, "code")
    if code:
        returned_state = extract_param(redirect, "state")
        # Only checked when a full URL was pasted; a bare code carries no state
        # to compare, and refusing that would reject a legitimate paste.
        if returned_state and returned_state != state:
            raise ValueError("state mismatch — start the sign-in again")
    else:
        # Accept a bare authorization code, since some browsers make the full
        # URL awkward to copy.
        code = redirect.strip()
    if not code:
        raise ValueError("no authorization code found in the pasted value")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            providers.OPENAI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": providers.OPENAI_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
        )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400:
        detail = payload.get("error_description") or payload.get("error") or "token exchange failed"
        raise ValueError(str(detail))

    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not access or not refresh:
        raise ValueError("token response was missing access_token or refresh_token")

    account_id = account_id_from_jwt(access)
    if not account_id:
        raise ValueError("could not read the ChatGPT account id from the token")

    expires_at = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    await creds.store_settings(
        {
            creds.OPENAI_ACCESS_TOKEN_KEY: access,
            creds.OPENAI_REFRESH_TOKEN_KEY: refresh,
            creds.OPENAI_ACCOUNT_ID_KEY: account_id,
            creds.OPENAI_EXPIRES_AT_KEY: expires_at.isoformat(),
        }
    )
    logger.info("ChatGPT credential stored; expires %s", expires_at.isoformat())
    return {"account_id": account_id, "expires_at": expires_at.isoformat()}
