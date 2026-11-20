"""
Signing in to Uniloom (docs/uniloom-plan.md). uniAuth is the OAuth server, so these follow
the specs it implements: RFC 9728 and 8414 discovery, PKCE (RFC 7636), the issuer check on
the redirect (RFC 9207), resource indicators (RFC 8707), refresh token rotation and
revocation (RFC 7009). A fake uniAuth answers over httpx2's MockTransport.
"""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from src.uniloom import oauth

MCP_URL = "https://loom.example/mcp"
ISSUER = "https://auth.example/api/auth"
PUBLIC_URL = "https://chrono.example"
CLIENT_ID = f"{PUBLIC_URL}/oauth/client.json"
REDIRECT = f"{PUBLIC_URL}/oauth/callback"

PRM = {"resource": MCP_URL, "authorization_servers": [ISSUER], "bearer_methods_supported": ["header"]}
ASM = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oauth2/authorize",
    "token_endpoint": f"{ISSUER}/oauth2/token",
    "revocation_endpoint": f"{ISSUER}/oauth2/revoke",
    "code_challenge_methods_supported": ["S256"],
    "authorization_response_iss_parameter_supported": True,
    "client_id_metadata_document_supported": True,
    "scopes_supported": ["openid", "profile", "email", "offline_access", "posts:write"],
}


def _jwt(claims: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'EdDSA'})}.{part(claims)}.c2ln"


class FakeAuth:
    """A uniAuth that records what it was sent."""

    def __init__(self, prm=PRM, asm=ASM):
        self.prm, self.asm = prm, asm
        self.requests = []
        self.token_status = 200
        self.token_body: dict = {}
        self.nonce = ""

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource/mcp" and request.url.host == "loom.example":
            return httpx2.Response(200, json=self.prm)
        if path == "/.well-known/oauth-authorization-server/api/auth" and request.url.host == "auth.example":
            return httpx2.Response(200, json=self.asm)
        if path == "/api/auth/oauth2/token":
            form = parse_qs(request.content.decode())
            body = self.token_body or {
                "access_token": "access-1", "token_type": "Bearer", "expires_in": 900,
                "refresh_token": "refresh-1", "scope": "openid profile offline_access",
                "id_token": _jwt({"iss": ISSUER, "aud": CLIENT_ID, "sub": "u-1", "name": "Oakar",
                                  "exp": 4102444800, "nonce": self.nonce or form.get("nonce", [""])[0]}),
            }
            return httpx2.Response(self.token_status, json=body)
        if path == "/api/auth/oauth2/revoke":
            return httpx2.Response(200)
        return httpx2.Response(404)

    def form(self, index=-1) -> dict:
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}


@pytest.fixture
def auth():
    return FakeAuth()


@pytest.fixture
def http(auth):
    return httpx2.AsyncClient(transport=httpx2.MockTransport(auth.handle))


# ── discovery ────────────────────────────────────────────────────────────────

async def test_discovery_finds_uniauth_through_the_resource_metadata(http):
    server = await oauth.discover(http, MCP_URL)
    assert server.issuer == ISSUER
    assert server.token_endpoint == f"{ISSUER}/oauth2/token"
    assert server.revocation_endpoint == f"{ISSUER}/oauth2/revoke"
    assert server.resource == MCP_URL


async def test_discovery_refuses_metadata_for_another_resource(auth, http):
    auth.prm = {**PRM, "resource": "https://evil.example/mcp"}
    with pytest.raises(oauth.OAuthError):
        await oauth.discover(http, MCP_URL)


async def test_discovery_refuses_a_server_that_names_another_issuer(auth, http):
    auth.asm = {**ASM, "issuer": "https://evil.example"}
    with pytest.raises(oauth.OAuthError):
        await oauth.discover(http, MCP_URL)


async def test_discovery_needs_pkce_with_s256(auth, http):
    auth.asm = {**ASM, "code_challenge_methods_supported": ["plain"]}
    with pytest.raises(oauth.OAuthError):
        await oauth.discover(http, MCP_URL)


async def test_discovery_needs_client_id_metadata_documents(auth, http):
    auth.asm = {k: v for k, v in ASM.items() if k != "client_id_metadata_document_supported"}
    with pytest.raises(oauth.OAuthError):
        await oauth.discover(http, MCP_URL)


# ── the sign-in link ─────────────────────────────────────────────────────────

async def _server(http):
    return await oauth.discover(http, MCP_URL)


async def test_the_link_asks_for_little_and_carries_pkce(http):
    logins = oauth.Logins()
    url, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith(f"{ISSUER}/oauth2/authorize?")
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID and query["redirect_uri"] == REDIRECT
    # Only what the bot needs: who they are and a refresh token. Not the other apps' scopes
    assert query["scope"] == "openid profile offline_access"
    assert query["resource"] == MCP_URL
    assert query["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(login.verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == expected
    assert query["state"] == login.state and len(login.state) >= 32
    assert query["nonce"] == login.nonce
    # OIDC asks for the consent screen when offline_access is wanted
    assert query["prompt"] == "consent"


async def test_a_login_works_once(http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    assert logins.take(login.state) is login
    assert logins.take(login.state) is None


async def test_a_login_expires_after_ten_minutes(http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT, now=1000.0)
    assert logins.take(login.state, now=1000.0 + oauth.LOGIN_SECONDS + 1) is None


async def test_a_new_link_replaces_the_old_one_for_the_same_person(http):
    logins = oauth.Logins()
    server = await _server(http)
    _, first = logins.start("760", server, CLIENT_ID, REDIRECT)
    _, second = logins.start("760", server, CLIENT_ID, REDIRECT)
    assert logins.take(first.state) is None
    assert logins.take(second.state) is second


async def test_an_unknown_state_is_refused(http):
    assert oauth.Logins().take("made-up") is None


# ── the code and the tokens ──────────────────────────────────────────────────

async def test_the_code_is_exchanged_with_the_verifier_and_the_resource(auth, http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    auth.nonce = login.nonce
    tokens = await oauth.exchange_code(http, login, "the-code")
    form = auth.form()
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
    assert form["code_verifier"] == login.verifier
    assert form["redirect_uri"] == REDIRECT and form["client_id"] == CLIENT_ID
    assert form["resource"] == MCP_URL
    assert tokens.access_token == "access-1" and tokens.refresh_token == "refresh-1"
    assert tokens.subject == "u-1" and tokens.name == "Oakar"


async def test_an_id_token_for_another_client_is_refused(auth, http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    auth.token_body = {"access_token": "a", "token_type": "Bearer", "expires_in": 900, "refresh_token": "r",
                       "id_token": _jwt({"iss": ISSUER, "aud": "someone-else", "sub": "u-1", "exp": 4102444800,
                                         "nonce": login.nonce})}
    with pytest.raises(oauth.OAuthError):
        await oauth.exchange_code(http, login, "the-code")


async def test_an_id_token_with_another_nonce_is_refused(auth, http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    auth.nonce = "replayed"
    with pytest.raises(oauth.OAuthError):
        await oauth.exchange_code(http, login, "the-code")


async def test_a_refused_code_says_why(auth, http):
    logins = oauth.Logins()
    _, login = logins.start("760", await _server(http), CLIENT_ID, REDIRECT)
    auth.token_status, auth.token_body = 400, {"error": "invalid_grant"}
    with pytest.raises(oauth.OAuthError) as error:
        await oauth.exchange_code(http, login, "the-code")
    assert error.value.code == "invalid_grant"


async def test_refreshing_sends_the_refresh_token_and_keeps_the_new_one(auth, http):
    server = await _server(http)
    auth.token_body = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 900,
                       "refresh_token": "refresh-2"}
    tokens = await oauth.refresh(http, server.token_endpoint, CLIENT_ID, "refresh-1", MCP_URL)
    form = auth.form()
    assert form == {"grant_type": "refresh_token", "refresh_token": "refresh-1", "client_id": CLIENT_ID,
                    "resource": MCP_URL}
    assert (tokens.access_token, tokens.refresh_token) == ("access-2", "refresh-2")


async def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(auth, http):
    server = await _server(http)
    auth.token_body = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 900}
    tokens = await oauth.refresh(http, server.token_endpoint, CLIENT_ID, "refresh-1", MCP_URL)
    assert tokens.refresh_token == "refresh-1"


async def test_an_expired_or_revoked_refresh_token_means_signing_in_again(auth, http):
    server = await _server(http)
    auth.token_status, auth.token_body = 400, {"error": "invalid_grant"}
    with pytest.raises(oauth.SignInExpired):
        await oauth.refresh(http, server.token_endpoint, CLIENT_ID, "refresh-1", MCP_URL)


async def test_a_server_error_on_refresh_is_not_a_lost_sign_in(auth, http):
    server = await _server(http)
    auth.token_status, auth.token_body = 503, {}
    with pytest.raises(oauth.OAuthError) as error:
        await oauth.refresh(http, server.token_endpoint, CLIENT_ID, "refresh-1", MCP_URL)
    assert not isinstance(error.value, oauth.SignInExpired)


async def test_revoking_names_the_token_and_the_client(auth, http):
    server = await _server(http)
    assert await oauth.revoke(http, server.revocation_endpoint, CLIENT_ID, "refresh-1")
    assert auth.form() == {"token": "refresh-1", "token_type_hint": "refresh_token", "client_id": CLIENT_ID}


def test_the_client_metadata_document_describes_a_public_client():
    document = oauth.client_metadata(PUBLIC_URL)
    assert document["client_id"] == CLIENT_ID
    assert document["redirect_uris"] == [REDIRECT]
    assert document["token_endpoint_auth_method"] == "none"
    assert document["grant_types"] == ["authorization_code", "refresh_token"]
    assert document["response_types"] == ["code"]
    assert document["client_name"] == "ChronoChunk"
    assert document["scope"] == "openid profile offline_access"


def test_a_nonce_that_is_not_plain_text_is_refused_not_a_crash():
    token = _jwt({"iss": ISSUER, "aud": CLIENT_ID, "sub": "u-1", "exp": 4102444800, "nonce": "é"})
    with pytest.raises(oauth.OAuthError):
        oauth.read_id_token(token, ISSUER, CLIENT_ID, "abc")
