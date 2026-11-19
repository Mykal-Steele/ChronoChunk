"""
The bot's side of Uniloom (docs/uniloom-plan.md): each person's sign-in, refreshed one at
a time, and MCP calls made only with the token of the person being answered.

Access tokens last 15 minutes and refresh tokens 30 days. uniAuth rotates the refresh
token on every use and cancels the whole sign-in if an old one comes back, so a person's
refresh runs under their own lock and the new token is saved before it is used.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Tuple

import httpx2

from config.config import Config
from src.uniloom import oauth
from src.uniloom.accounts import Account, AccountStore
from src.uniloom.state import PendingWrite, UniloomState
from src.version import __version__

logger = logging.getLogger(__name__)

# How much of one result the model reads. A long SPEC body would use up the reply otherwise
RESULT_CHARS = 6000
# A token this close to expiring is refreshed first, so it can't run out in the middle of a call
REFRESH_EARLY = timedelta(seconds=60)
# The tool list is the same for everyone and changes only when Uniloom is updated
TOOLS_SECONDS = 6 * 3600
# The sign-in server's addresses, found once in a while instead of on every sign-in
SERVER_SECONDS = 3600
HTTP_TIMEOUT = httpx2.Timeout(20.0, connect=10.0)
USER_AGENT = f"ChronoChunk/{__version__}"
# Tools that only read, when the server does not say. Anything else waits for the person's OK
_READ_PREFIXES = ("list_", "get_", "search_", "read_", "find_")


class NotConnected(Exception):
    """The person has no working sign-in. expired means they had one and it ran out or was revoked."""

    def __init__(self, expired: bool = False):
        super().__init__("expired" if expired else "not connected")
        self.expired = expired


class UniloomUnavailable(Exception):
    """uniAuth or Uniloom could not be reached, or answered with an error. The sign-in is kept."""


class Unauthorized(Exception):
    """Uniloom answered 401: the token, or the person, was refused. The message is Uniloom's."""


class Forbidden(Exception):
    """Uniloom answered 403, for example terms not accepted. The message is Uniloom's."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Optional[dict]
    read_only: bool


@dataclass
class LoginOutcome:
    ok: bool
    title: str
    message: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_read_only(name: str, annotations: Any) -> bool:
    """What the server says about the tool, and otherwise its name. Unknown means it changes things."""
    hint = getattr(annotations, "read_only_hint", None) if annotations is not None else None
    if isinstance(hint, bool):
        return hint
    return name.startswith(_READ_PREFIXES)


def _refusal(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")
        return str(body.get("message") or error or "")
    return ""


def _shorten(text: str) -> str:
    if len(text) <= RESULT_CHARS:
        return text
    return text[:RESULT_CHARS] + "\n...(cut here, ask Uniloom for less, like one item or a filter)"


def _result_text(result: Any) -> str:
    parts = [getattr(part, "text", "") for part in (getattr(result, "content", None) or [])]
    text = "\n".join(part for part in parts if part).strip()
    if not text and getattr(result, "structured_content", None) is not None:
        text = str(result.structured_content)
    if getattr(result, "is_error", False):
        return "error: " + _shorten(text or "Uniloom said the call failed")
    return _shorten(text or "(done, Uniloom sent nothing back)")


class UniloomService:
    def __init__(self, store: Optional[AccountStore] = None, http: Optional[httpx2.AsyncClient] = None,
                 mcp_url: Optional[str] = None, public_url: Optional[str] = None,
                 client_factory: Optional[Callable[[str], Any]] = None,
                 now: Optional[Callable[[], datetime]] = None):
        self._store = store
        self._http = http
        self.mcp_url = Config.UNILOOM_MCP_URL if mcp_url is None else mcp_url
        self.public_url = (Config.PUBLIC_URL if public_url is None else public_url).rstrip("/")
        self._client_factory = client_factory or self._sdk_client
        self._now = now or _utcnow
        self.logins = oauth.Logins()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._server: Optional[Tuple[oauth.AuthServer, float]] = None
        self._tools: Optional[Tuple[List[ToolSpec], str, float]] = None
        # Tasks started from the sign-in page, kept so they are not garbage collected while running
        self._tasks: set = set()

    # ── setup ────────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.mcp_url and self.public_url.startswith("https://"))

    @property
    def store(self) -> AccountStore:
        if self._store is None:
            self._store = AccountStore()
        return self._store

    @property
    def http(self) -> httpx2.AsyncClient:
        if self._http is None:
            self._http = httpx2.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
        return self._http

    @http.setter
    def http(self, value: httpx2.AsyncClient) -> None:
        self._http = value

    @property
    def client_id(self) -> str:
        return f"{self.public_url}/oauth/client.json"

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/oauth/callback"

    async def auth_server(self) -> oauth.AuthServer:
        loop = asyncio.get_running_loop()
        if self._server and loop.time() - self._server[1] < SERVER_SECONDS:
            return self._server[0]
        server = await oauth.discover(self.http, self.mcp_url)
        self._server = (server, loop.time())
        return server

    # ── signing in ───────────────────────────────────────────────────────────

    async def start_login(self, user_id: str, on_done: Optional[Callable[[Account], Any]] = None) -> str:
        """A sign-in link for this person. on_done(account) runs once they have signed in."""
        url, _ = self.logins.start(str(user_id), await self.auth_server(), self.client_id, self.redirect_uri,
                                   on_done=on_done)
        return url

    async def finish_login(self, params: Mapping[str, str]) -> LoginOutcome:
        """The redirect back from uniAuth, with what the page should tell them."""
        login = self.logins.take(params.get("state", ""))
        if login is None:
            return LoginOutcome(False, "that link didnt work",
                                "it ran out or was already used. run /uniloom connect in Discord for a new one")
        # RFC 9207: uniAuth names itself on every redirect, so one that doesn't is not from uniAuth
        if (params.get("iss") or "").rstrip("/") != login.server.issuer:
            logger.warning("A Uniloom sign-in came back from the wrong issuer and was refused")
            return LoginOutcome(False, "that sign-in didnt check out",
                                "nothing changed. run /uniloom connect in Discord to try again")
        if params.get("error"):
            if params["error"] == "access_denied":
                return LoginOutcome(False, "not connected", "u didnt allow it, so nothing changed")
            logger.warning(f"uniAuth sent a sign-in back with error {params['error']!r}")
            return LoginOutcome(False, "that didnt work", "uniAuth said no. run /uniloom connect in Discord to try again")
        if not params.get("code"):
            return LoginOutcome(False, "that didnt work", "run /uniloom connect in Discord to try again")
        try:
            tokens = await oauth.exchange_code(self.http, login, params["code"])
        except oauth.OAuthError as e:
            logger.warning(f"A Uniloom sign-in failed: {e} {e.code}".strip())
            return LoginOutcome(False, "that didnt work", "run /uniloom connect in Discord to try again")
        now = self._now()
        account = Account(
            user_id=login.user_id, subject=tokens.subject, name=tokens.name, issuer=login.server.issuer,
            client_id=login.client_id, token_endpoint=login.server.token_endpoint,
            revocation_endpoint=login.server.revocation_endpoint, resource=login.server.resource,
            access_token=tokens.access_token, refresh_token=tokens.refresh_token,
            expires_at=now + timedelta(seconds=tokens.expires_in), scope=tokens.scope,
            connected_at=now, used_at=now)
        self.store.save(account)
        logger.info(f"Connected Uniloom for Discord user {login.user_id}")
        if login.on_done is not None:
            task = asyncio.create_task(self._after_login(login.on_done, account))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        shown = tokens.name or "ur account"
        return LoginOutcome(True, "connected", f"connected to Uniloom as {shown}. u can close this and go back to Discord")

    @staticmethod
    async def _after_login(on_done: Callable[[Account], Any], account: Account) -> None:
        try:
            await on_done(account)
        except Exception as e:
            logger.warning(f"Could not finish up after a Uniloom sign-in: {e!r}")

    # ── the sign-in each call uses ───────────────────────────────────────────

    def is_connected(self, user_id: Optional[str]) -> bool:
        return bool(user_id) and self.store.exists(str(user_id))

    def account(self, user_id: str) -> Optional[Account]:
        return self.store.get(str(user_id))

    async def access_token(self, user_id: str, rejected: Optional[str] = None) -> str:
        """
        A working access token for this person. rejected is a token Uniloom just refused, so a
        fresh-looking one is refreshed anyway, unless another call already did.
        """
        user_id = str(user_id)
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            account = self.store.get(user_id)
            if account is None:
                raise NotConnected()
            now = self._now()
            stale = rejected is not None and account.access_token == rejected
            if not stale and account.expires_at - REFRESH_EARLY > now:
                return account.access_token
            try:
                tokens = await oauth.refresh(self.http, account.token_endpoint, account.client_id,
                                             account.refresh_token, account.resource)
            except oauth.SignInExpired as e:
                self.store.delete(user_id)
                logger.info(f"Uniloom sign-in for Discord user {user_id} ended ({e.code})")
                raise NotConnected(expired=True) from e
            except oauth.OAuthError as e:
                raise UniloomUnavailable(str(e)) from e
            self.store.update_tokens(user_id, tokens.access_token, tokens.refresh_token,
                                     now + timedelta(seconds=tokens.expires_in), tokens.scope or account.scope)
            return tokens.access_token

    # ── calling Uniloom ──────────────────────────────────────────────────────

    @asynccontextmanager
    async def _sdk_client(self, token: str) -> AsyncIterator[Any]:
        """An MCP client for Uniloom that sends this token, with Uniloom's 401 and 403 turned into errors."""
        # Imported here: the SDK takes about 28 MB, which only people who use Uniloom need
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp_types import Implementation

        refused: Dict[str, Any] = {}

        async def note_refusal(response: httpx2.Response) -> None:
            if response.status_code in (401, 403) and "status" not in refused:
                await response.aread()
                refused.update(status=response.status_code, message=_refusal(response))

        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False,
                                      headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
                                      event_hooks={"response": [note_refusal]}) as http:
            try:
                transport = streamable_http_client(self.mcp_url, http_client=http)
                async with Client(transport, client_info=Implementation(name="ChronoChunk", version=__version__),
                                  read_timeout_seconds=30) as client:
                    yield client
            except Exception as e:
                if refused.get("status") == 401:
                    raise Unauthorized(refused.get("message") or "Invalid or expired token") from e
                if refused.get("status") == 403:
                    raise Forbidden(refused.get("message") or "Uniloom refused this account") from e
                raise

    async def _with_client(self, user_id: str, use: Callable[[Any], Any]) -> Any:
        """Run use(client) with this person's token, refreshing once if Uniloom refuses a fresh-looking one."""
        rejected: Optional[str] = None
        for attempt in range(2):
            token = await self.access_token(user_id, rejected=rejected)
            try:
                async with self._client_factory(token) as client:
                    return await use(client)
            except Unauthorized:
                if attempt:
                    raise
                rejected = token

    async def tools(self, user_id: str) -> Tuple[List[ToolSpec], str]:
        """Uniloom's tools and its instructions for using them, fetched once in a while for everyone."""
        loop = asyncio.get_running_loop()
        if self._tools and loop.time() - self._tools[2] < TOOLS_SECONDS:
            return self._tools[0], self._tools[1]

        async def fetch(client) -> Tuple[List[ToolSpec], str]:
            specs: List[ToolSpec] = []
            cursor = None
            for _ in range(10):
                page = await client.list_tools(cursor=cursor)
                specs += [ToolSpec(tool.name, tool.description or "", tool.input_schema,
                                   is_read_only(tool.name, tool.annotations)) for tool in page.tools]
                cursor = page.next_cursor
                if not cursor:
                    break
            return specs, client.instructions or ""

        specs, instructions = await self._wrapped(user_id, fetch)
        self._tools = (specs, instructions, loop.time())
        return specs, instructions

    def forget_tools(self) -> None:
        """Fetch the list again next time, for when the model called a tool that is gone."""
        self._tools = None

    async def _wrapped(self, user_id: str, use: Callable[[Any], Any]) -> Any:
        try:
            return await self._with_client(user_id, use)
        except (NotConnected, Unauthorized, Forbidden, UniloomUnavailable):
            raise
        except Exception as e:
            logger.warning(f"Uniloom call failed: {e!r}")
            raise UniloomUnavailable("could not reach Uniloom") from e

    async def call(self, user_id: str, name: str, arguments: dict) -> str:
        """Call one Uniloom tool as this person. Returns what the model reads, errors included."""
        async def use(client) -> str:
            return _result_text(await client.call_tool(name, arguments))

        try:
            return await self._wrapped(user_id, use)
        except Unauthorized as e:
            return f"error: Uniloom refused this account: {e}"
        except Forbidden as e:
            return f"error: Uniloom refused: {e}"
        except UniloomUnavailable:
            return "error: couldnt reach Uniloom right now, try again in a bit"

    # ── signing out ──────────────────────────────────────────────────────────

    def take_account(self, user_id: str) -> Optional[Account]:
        """Forget a sign-in now and hand it back, so revoke can cancel it at uniAuth after."""
        return self.store.delete(str(user_id))

    async def revoke(self, account: Optional[Account]) -> None:
        if account is None:
            return
        if not await oauth.revoke(self.http, account.revocation_endpoint, account.client_id, account.refresh_token):
            # It is gone here either way. The refresh token was only ever on this bot, and it
            # stops working after 30 days unused
            logger.warning(f"Could not revoke the Uniloom sign-in of Discord user {account.user_id} at uniAuth")

    async def disconnect(self, user_id: str) -> bool:
        """Sign out: revoke at uniAuth and forget it here. False when they were not connected."""
        account = self.take_account(user_id)
        await self.revoke(account)
        return account is not None


_shared: Optional[UniloomService] = None


def get_uniloom() -> UniloomService:
    global _shared
    if _shared is None:
        _shared = UniloomService()
    return _shared


def set_uniloom(service: Optional[UniloomService]) -> None:
    global _shared
    _shared = service


__all__ = ["Forbidden", "LoginOutcome", "NotConnected", "PendingWrite", "ToolSpec", "Unauthorized",
           "UniloomService", "UniloomState", "UniloomUnavailable", "get_uniloom", "set_uniloom"]
