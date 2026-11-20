"""
The bot's side of Uniloom: one sign-in per person, refreshed one at a time, and MCP calls
made only with the token of the person being answered (docs/uniloom-plan.md).
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from mcp import Client
from mcp.server import MCPServer

from src.uniloom import service as service_module
from src.uniloom.accounts import AccountStore
from src.uniloom.service import Forbidden, NotConnected, Unauthorized, UniloomService
from tests.unit.test_uniloom_accounts import NOW, _account
from tests.unit.test_uniloom_oauth import ISSUER, MCP_URL, PUBLIC_URL, FakeAuth


def _server() -> MCPServer:
    server = MCPServer("uniloom", instructions="Keys, not ids.")

    @server.tool(description="The projects you belong to")
    def list_projects() -> str:
        return "TG: Tracker (Guided, Owner)"

    @server.tool(description="Create or update an item")
    def save_item(title: str, parent: str | None = None) -> str:
        return f"Created TG-13 {title}"

    @server.tool(description="A long document")
    def get_document(id: str) -> str:
        return "x" * 20000

    @server.tool(description="Fails")
    def delete_item(key: str) -> str:
        raise ValueError("forbidden")

    return server


class Clients:
    """Hands out MCP clients over an in-memory Uniloom, and records the token each one used."""

    def __init__(self):
        self.server = _server()
        self.tokens = []
        self.fail_with = []

    @asynccontextmanager
    async def __call__(self, token):
        self.tokens.append(token)
        if self.fail_with:
            raise self.fail_with.pop(0)
        async with Client(self.server) as client:
            yield client


@pytest.fixture
def auth():
    return FakeAuth()


@pytest.fixture
def clock():
    return {"now": NOW}


@pytest.fixture
def clients():
    return Clients()


@pytest.fixture
def uniloom(database, tmp_path, auth, clock, clients):
    store = AccountStore(database, key_path=str(tmp_path / "uniloom.key"))
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(auth.handle))
    return UniloomService(store=store, http=http, mcp_url=MCP_URL, public_url=PUBLIC_URL,
                          client_factory=clients, now=lambda: clock["now"])


def test_it_is_off_without_a_public_address(database, tmp_path):
    store = AccountStore(database, key_path=str(tmp_path / "k"))
    assert not UniloomService(store=store, mcp_url=MCP_URL, public_url="").enabled
    assert UniloomService(store=store, mcp_url=MCP_URL, public_url=PUBLIC_URL).enabled


# ── signing in ───────────────────────────────────────────────────────────────

async def _authorize_query(uniloom, user_id="760", on_done=None):
    url = await uniloom.start_login(user_id, on_done)
    _, authorize = uniloom.start_page(parse_qs(urlparse(url).query)["state"][0])
    return {k: v[0] for k, v in parse_qs(urlparse(authorize).query).items()}


async def _sign_in(uniloom, auth, user_id="760", on_done=None):
    query = await _authorize_query(uniloom, user_id, on_done)
    auth.nonce = query["nonce"]
    return await uniloom.finish_login({"code": "c", "state": query["state"], "iss": ISSUER}), query


async def test_signing_in_saves_the_account_and_tells_whoever_asked(uniloom, auth):
    done = []

    async def on_done(account):
        done.append(account.name)

    outcome, _ = await _sign_in(uniloom, auth, on_done=on_done)
    await asyncio.sleep(0)
    assert outcome.ok and "Oakar" in outcome.message
    assert uniloom.is_connected("760")
    assert done == ["Oakar"]


async def test_a_redirect_from_another_issuer_is_refused(uniloom, auth):
    url = await uniloom.start_login("760")
    state = parse_qs(urlparse(url).query)["state"][0]
    outcome = await uniloom.finish_login({"code": "c", "state": state, "iss": "https://evil.example"})
    assert not outcome.ok and not uniloom.is_connected("760")


async def test_a_redirect_without_the_issuer_is_refused(uniloom, auth):
    # uniAuth says it always sends iss (RFC 9207), so a redirect without it did not come from uniAuth
    url = await uniloom.start_login("760")
    state = parse_qs(urlparse(url).query)["state"][0]
    outcome = await uniloom.finish_login({"code": "c", "state": state})
    assert not outcome.ok and not uniloom.is_connected("760")


async def test_saying_no_on_the_consent_screen_changes_nothing(uniloom, auth):
    url = await uniloom.start_login("760")
    state = parse_qs(urlparse(url).query)["state"][0]
    outcome = await uniloom.finish_login({"error": "access_denied", "state": state, "iss": ISSUER})
    assert not outcome.ok and "nothing changed" in outcome.message
    assert not uniloom.is_connected("760")


async def test_an_old_or_made_up_link_is_refused(uniloom):
    outcome = await uniloom.finish_login({"code": "c", "state": "made-up", "iss": ISSUER})
    assert not outcome.ok and "/uniloom connect" in outcome.message


# ── the token ────────────────────────────────────────────────────────────────

async def test_a_fresh_token_is_used_as_it_is(uniloom, auth):
    uniloom.store.save(_account())
    assert await uniloom.access_token("760") == "access-secret"
    assert not [r for r in auth.requests if r.url.path.endswith("/token")]


async def test_a_token_about_to_expire_is_refreshed_and_the_new_refresh_token_kept(uniloom, auth, clock):
    uniloom.store.save(_account())
    clock["now"] = NOW + timedelta(minutes=14, seconds=30)
    auth.token_body = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 900,
                       "refresh_token": "refresh-2"}
    assert await uniloom.access_token("760") == "access-2"
    account = uniloom.store.get("760")
    assert account.refresh_token == "refresh-2"
    assert account.expires_at == clock["now"] + timedelta(seconds=900)


async def test_two_replies_at_once_refresh_only_once(uniloom, auth, clock):
    # uniAuth rotates refresh tokens and cancels the whole sign-in when an old one is used again
    uniloom.store.save(_account())
    clock["now"] = NOW + timedelta(hours=1)
    auth.token_body = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 900,
                       "refresh_token": "refresh-2"}
    tokens = await asyncio.gather(*(uniloom.access_token("760") for _ in range(5)))
    assert set(tokens) == {"access-2"}
    assert len([r for r in auth.requests if r.url.path.endswith("/token")]) == 1


async def test_a_lost_sign_in_removes_the_account(uniloom, auth, clock):
    uniloom.store.save(_account())
    clock["now"] = NOW + timedelta(hours=1)
    auth.token_status, auth.token_body = 400, {"error": "invalid_grant"}
    with pytest.raises(NotConnected) as error:
        await uniloom.access_token("760")
    assert error.value.expired
    assert not uniloom.is_connected("760")


async def test_uniauth_being_down_keeps_the_account(uniloom, auth, clock):
    uniloom.store.save(_account())
    clock["now"] = NOW + timedelta(hours=1)
    auth.token_status, auth.token_body = 503, {}
    with pytest.raises(service_module.UniloomUnavailable):
        await uniloom.access_token("760")
    assert uniloom.is_connected("760")


async def test_someone_who_never_connected_is_not_connected(uniloom):
    with pytest.raises(NotConnected) as error:
        await uniloom.access_token("222")
    assert not error.value.expired


# ── calling Uniloom ──────────────────────────────────────────────────────────

async def test_a_call_uses_the_token_of_the_person_being_answered(uniloom, clients):
    uniloom.store.save(_account("760", access="token-760"))
    uniloom.store.save(_account("222", access="token-222"))
    assert "TG: Tracker" in await uniloom.call("222", "list_projects", {})
    assert clients.tokens == ["token-222"]


async def test_a_tool_error_comes_back_as_an_error(uniloom):
    uniloom.store.save(_account())
    assert (await uniloom.call("760", "delete_item", {"key": "TG-1"})).startswith("error:")


async def test_a_long_result_is_cut(uniloom):
    uniloom.store.save(_account())
    text = await uniloom.call("760", "get_document", {"id": "d1"})
    assert len(text) <= service_module.RESULT_CHARS + 100 and "cut" in text


async def test_a_rejected_token_is_refreshed_once_and_the_call_tried_again(uniloom, clients, auth):
    uniloom.store.save(_account())
    clients.fail_with = [Unauthorized("Invalid or expired token")]
    auth.token_body = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 900,
                       "refresh_token": "refresh-2"}
    assert "TG: Tracker" in await uniloom.call("760", "list_projects", {})
    assert clients.tokens == ["access-secret", "access-2"]


async def test_uniloom_refusing_the_person_passes_its_reason_on(uniloom, clients):
    uniloom.store.save(_account())
    clients.fail_with = [Forbidden("Complete the shared Unicorp terms step through UniAuth")]
    assert "Unicorp terms" in await uniloom.call("760", "list_projects", {})


async def test_the_tool_list_is_fetched_once_and_marks_what_changes_things(uniloom, clients):
    uniloom.store.save(_account())
    tools, instructions = await uniloom.tools("760")
    await uniloom.tools("760")
    assert len(clients.tokens) == 1
    assert instructions == "Keys, not ids."
    kinds = {tool.name: tool.read_only for tool in tools}
    assert kinds == {"list_projects": True, "save_item": False, "get_document": True, "delete_item": False}


# ── disconnecting ────────────────────────────────────────────────────────────

async def test_disconnecting_revokes_the_refresh_token_and_forgets_it(uniloom, auth):
    uniloom.store.save(_account())
    assert await uniloom.disconnect("760")
    revoke = [r for r in auth.requests if r.url.path.endswith("/revoke")]
    assert len(revoke) == 1 and b"refresh-secret" in revoke[0].content
    assert not uniloom.is_connected("760")


async def test_disconnecting_forgets_the_token_even_when_revoking_fails(uniloom, auth):
    uniloom.store.save(_account())
    auth.handle = lambda request: httpx2.Response(503)
    uniloom.http = httpx2.AsyncClient(transport=httpx2.MockTransport(auth.handle))
    await uniloom.disconnect("760")
    assert not uniloom.is_connected("760")


async def test_the_link_goes_to_the_bots_own_page_first(uniloom):
    url = await uniloom.start_login("760", name="Kruskal (@kruskal_oakar)")
    assert url.startswith(f"{PUBLIC_URL}/oauth/start?state=")
    state = parse_qs(urlparse(url).query)["state"][0]
    name, authorize = uniloom.start_page(state)
    assert name == "Kruskal (@kruskal_oakar)" and authorize.startswith(f"{ISSUER}/oauth2/authorize?")
    assert uniloom.start_page("made-up") is None


async def test_signing_out_cancels_a_sign_in_still_open(uniloom, auth):
    query = await _authorize_query(uniloom)
    auth.nonce = query["nonce"]
    uniloom.take_account("760")
    outcome = await uniloom.finish_login({"code": "c", "state": query["state"], "iss": ISSUER})
    assert not outcome.ok and not uniloom.is_connected("760")
