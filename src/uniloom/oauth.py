"""
OAuth for Uniloom's MCP, with uniAuth as the sign-in server. The bot is a public client
named by its Client ID Metadata Document (the MCP authorization spec's preferred way, so
nothing is registered and no client secret exists), and it follows the specs uniAuth
implements:

- RFC 9728 and RFC 8414: find uniAuth from Uniloom's protected-resource metadata, and
  check that both documents are about who they claim to be.
- RFC 7636: PKCE with S256 on every sign-in.
- RFC 9207: the redirect must name uniAuth as its issuer, so a code from another server
  can't be slipped in.
- RFC 8707: tokens are asked for Uniloom's /mcp only.
- OpenID Connect: the ID token says who signed in. It comes straight from the token
  endpoint over TLS, so its issuer, audience, expiry and nonce are checked and its
  signature is not (OIDC Core 3.1.3.7).
- RFC 6749 refresh with rotation, and RFC 7009 revocation.

The MCP SDK has an OAuth client too, but it is built for one person on one machine: it
asks for every scope uniAuth lists, forgets a token's expiry when loading it from storage,
and starts a fresh sign-in instead of refreshing after a restart. The bot holds many
people's sign-ins for weeks, so it does this part itself.
"""
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

import httpx2

# Who they are, and a refresh token so they don't sign in every 15 minutes. Uniloom gives
# a token exactly its person's project role, so it needs no scope of its own.
SCOPE = "openid profile offline_access"
# How long a sign-in link works
LOGIN_SECONDS = 600


class OAuthError(Exception):
    """Signing in or refreshing failed. code is the OAuth error code when uniAuth sent one."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class SignInExpired(OAuthError):
    """The refresh token was refused for good (expired, revoked or reused): sign in again."""


@dataclass(frozen=True)
class AuthServer:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    subject: str = ""
    name: str = ""


@dataclass
class Login:
    """One sign-in link that has not come back yet."""
    state: str
    user_id: str
    verifier: str
    nonce: str
    server: AuthServer
    client_id: str
    redirect_uri: str
    started: float
    # Called with the saved Account once the sign-in is done
    on_done: Optional[Callable[..., Awaitable[None]]] = None


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def _well_known(url: str, name: str) -> str:
    """RFC 8414 and 9728: the well-known name goes between the host and the path."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}/.well-known/{name}{path}"


async def _get_json(http: httpx2.AsyncClient, url: str) -> Optional[dict]:
    try:
        response = await http.get(url, headers={"Accept": "application/json"})
    except httpx2.HTTPError as e:
        raise OAuthError(f"could not reach {url}: {e!r}") from e
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def discover(http: httpx2.AsyncClient, mcp_url: str) -> AuthServer:
    """Who signs people in for this MCP server, and where, checked against both documents."""
    resource = None
    for url in (_well_known(mcp_url, "oauth-protected-resource"),
                _well_known(f"{urlsplit(mcp_url).scheme}://{urlsplit(mcp_url).netloc}", "oauth-protected-resource")):
        resource = await _get_json(http, url)
        if resource:
            break
    if not resource:
        raise OAuthError("Uniloom has no protected-resource metadata")
    if not _same_url(str(resource.get("resource", "")), mcp_url):
        raise OAuthError("Uniloom's metadata is for another resource")
    servers = resource.get("authorization_servers") or []
    if not servers or not isinstance(servers[0], str):
        raise OAuthError("Uniloom names no sign-in server")
    issuer = servers[0].rstrip("/")

    metadata = None
    for url in (_well_known(issuer, "oauth-authorization-server"), _well_known(issuer, "openid-configuration"),
                f"{issuer}/.well-known/openid-configuration"):
        metadata = await _get_json(http, url)
        if metadata:
            break
    if not metadata:
        raise OAuthError("the sign-in server has no metadata")
    if str(metadata.get("issuer", "")).rstrip("/") != issuer:
        raise OAuthError("the sign-in server's metadata names another issuer")
    if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
        raise OAuthError("the sign-in server does not support PKCE with S256")
    if not metadata.get("client_id_metadata_document_supported"):
        raise OAuthError("the sign-in server does not take client ID metadata documents")
    endpoints = {}
    for name in ("authorization_endpoint", "token_endpoint"):
        value = str(metadata.get(name, ""))
        if urlsplit(value).scheme != "https":
            raise OAuthError(f"the sign-in server's {name} is not HTTPS")
        endpoints[name] = value
    return AuthServer(resource=mcp_url, issuer=issuer, revocation_endpoint=str(metadata.get("revocation_endpoint", "")),
                      **endpoints)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


class Logins:
    """The sign-in links out there. Each works once, for LOGIN_SECONDS, and a new one replaces a person's old one."""

    def __init__(self):
        self._by_state: Dict[str, Login] = {}

    def start(self, user_id: str, server: AuthServer, client_id: str, redirect_uri: str,
              on_done: Optional[Callable[..., Awaitable[None]]] = None, now: Optional[float] = None) -> Tuple[str, Login]:
        now = time.monotonic() if now is None else now
        self._drop_old(now)
        for state in [s for s, login in self._by_state.items() if login.user_id == str(user_id)]:
            del self._by_state[state]
        login = Login(state=secrets.token_urlsafe(32), user_id=str(user_id), verifier=secrets.token_urlsafe(48),
                      nonce=secrets.token_urlsafe(24), server=server, client_id=client_id,
                      redirect_uri=redirect_uri, started=now, on_done=on_done)
        self._by_state[login.state] = login
        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": login.state,
            "nonce": login.nonce,
            "code_challenge": _challenge(login.verifier),
            "code_challenge_method": "S256",
            "resource": server.resource,
            # OIDC: a refresh token (offline_access) is only given with the consent screen
            "prompt": "consent",
        }
        return f"{server.authorization_endpoint}?{urlencode(query)}", login

    def take(self, state: str, now: Optional[float] = None) -> Optional[Login]:
        """The login for this state, removed so it can't be used twice. None when unknown or too old."""
        now = time.monotonic() if now is None else now
        login = self._by_state.pop(state or "", None)
        if login is None or now - login.started > LOGIN_SECONDS:
            return None
        return login

    def _drop_old(self, now: float) -> None:
        for state in [s for s, login in self._by_state.items() if now - login.started > LOGIN_SECONDS]:
            del self._by_state[state]


def _claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise OAuthError("the ID token could not be read") from e
    if not isinstance(data, dict):
        raise OAuthError("the ID token could not be read")
    return data


def read_id_token(token: str, issuer: str, client_id: str, nonce: str, now: Optional[float] = None) -> dict:
    """The ID token's claims, after checking it is for this client and this sign-in."""
    claims = _claims(token)
    now = time.time() if now is None else now
    audience = claims.get("aud")
    audiences: List[str] = [str(a) for a in audience] if isinstance(audience, list) else [str(audience)]
    if str(claims.get("iss", "")).rstrip("/") != issuer:
        raise OAuthError("the ID token is from another issuer")
    if client_id not in audiences:
        raise OAuthError("the ID token is for another client")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise OAuthError("the ID token is for another client")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= now:
        raise OAuthError("the ID token has expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OAuthError("the ID token is from another sign-in")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OAuthError("the ID token names nobody")
    return claims


async def _post_token(http: httpx2.AsyncClient, endpoint: str, form: dict) -> Tuple[int, dict]:
    try:
        response = await http.post(endpoint, data=form, headers={"Accept": "application/json"})
    except httpx2.HTTPError as e:
        raise OAuthError(f"could not reach the sign-in server: {e!r}") from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}


def _tokens(body: dict, refresh_token: str = "") -> TokenSet:
    if str(body.get("token_type", "")).lower() != "bearer" or not isinstance(body.get("access_token"), str):
        raise OAuthError("the sign-in server sent no usable access token")
    expires_in = body.get("expires_in")
    return TokenSet(
        access_token=body["access_token"],
        # RFC 6749 section 6: a refresh may leave out refresh_token when it does not rotate
        refresh_token=str(body.get("refresh_token") or refresh_token),
        # Without one, assume it is short, so it is refreshed early rather than used expired
        expires_in=int(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else 300,
        scope=str(body.get("scope", "")))


async def exchange_code(http: httpx2.AsyncClient, login: Login, code: str) -> TokenSet:
    """Trade the code from the redirect for tokens, proving with the PKCE verifier that this bot asked for it."""
    status, body = await _post_token(http, login.server.token_endpoint, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": login.redirect_uri,
        "client_id": login.client_id,
        "code_verifier": login.verifier,
        "resource": login.server.resource,
    })
    if status != 200:
        raise OAuthError(f"the sign-in server refused the code ({status})", str(body.get("error", "")))
    tokens = _tokens(body)
    if not tokens.refresh_token:
        raise OAuthError("the sign-in server gave no refresh token")
    if not isinstance(body.get("id_token"), str):
        raise OAuthError("the sign-in server sent no ID token")
    claims = read_id_token(body["id_token"], login.server.issuer, login.client_id, login.nonce)
    tokens.subject = claims["sub"]
    tokens.name = str(claims.get("name") or claims.get("preferred_username") or "")
    return tokens


async def refresh(http: httpx2.AsyncClient, token_endpoint: str, client_id: str, refresh_token: str,
                  resource: str) -> TokenSet:
    """New tokens for a refresh token. Raises SignInExpired when it is no good any more."""
    status, body = await _post_token(http, token_endpoint, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    })
    error = str(body.get("error", ""))
    if status in (400, 401) and error in ("invalid_grant", "invalid_client", "unauthorized_client"):
        raise SignInExpired("the refresh token was refused", error)
    if status != 200:
        raise OAuthError(f"refreshing failed ({status})", error)
    return _tokens(body, refresh_token)


async def revoke(http: httpx2.AsyncClient, revocation_endpoint: str, client_id: str, token: str) -> bool:
    """Ask uniAuth to cancel a refresh token (RFC 7009). False when it could not be asked."""
    if not revocation_endpoint:
        return False
    try:
        response = await http.post(revocation_endpoint, data={
            "token": token, "token_type_hint": "refresh_token", "client_id": client_id})
    except httpx2.HTTPError:
        return False
    return response.status_code == 200


def client_metadata(public_url: str) -> dict:
    """The bot's Client ID Metadata Document, served at its own client_id URL."""
    return {
        "client_id": f"{public_url}/oauth/client.json",
        "client_name": "ChronoChunk",
        "redirect_uris": [f"{public_url}/oauth/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
        "application_type": "web",
    }


__all__ = ["AuthServer", "Login", "Logins", "OAuthError", "SignInExpired", "TokenSet", "client_metadata",
           "discover", "exchange_code", "read_id_token", "refresh", "revoke"]
