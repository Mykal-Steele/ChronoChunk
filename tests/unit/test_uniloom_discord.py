"""
The Discord side of Uniloom: the Connect button, the do it / cancel buttons, the sign-in
pages uniAuth sends people back to, /uniloom and /forget (docs/uniloom-plan.md).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.uniloom.service import LoginOutcome, PendingWrite
from src.uniloom.views import ConfirmView, ConnectView


def _interaction(user_id=760):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id, display_name="Oakar")
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.message = MagicMock()
    return interaction


@pytest.fixture
def uniloom():
    service = MagicMock()
    service.start_login = AsyncMock(return_value="https://auth.example/authorize?state=s")
    service.call = AsyncMock(return_value="Created TG-13")
    return service


# ── the Connect button ───────────────────────────────────────────────────────

async def test_only_the_asker_can_press_connect(uniloom):
    view = ConnectView("760", uniloom, on_connected=AsyncMock())
    other = _interaction(222)
    assert await view.interaction_check(other) is False
    other.response.send_message.assert_awaited_once()
    assert other.response.send_message.call_args[1]["ephemeral"] is True


async def test_connect_sends_a_private_sign_in_link(uniloom):
    view = ConnectView("760", uniloom, on_connected=AsyncMock())
    interaction = _interaction()
    assert await view.interaction_check(interaction)
    await view.connect.callback(interaction)
    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.call_args[1]["ephemeral"] is True
    kwargs = interaction.followup.send.call_args[1]
    assert kwargs["ephemeral"] is True
    link = kwargs["view"].children[0]
    assert link.url == "https://auth.example/authorize?state=s"
    uniloom.start_login.assert_awaited_once()
    assert uniloom.start_login.call_args[0][0] == "760"


async def test_after_signing_in_the_person_hears_so_and_the_request_goes_on(uniloom):
    on_connected = AsyncMock()
    view = ConnectView("760", uniloom, on_connected=on_connected)
    interaction = _interaction()
    await view.connect.callback(interaction)
    finished = uniloom.start_login.call_args[0][1]
    await finished(SimpleNamespace(name="Oakar"))
    assert "Oakar" in interaction.followup.send.call_args[0][0]
    on_connected.assert_awaited_once()


async def test_uniauth_being_down_is_said_and_nothing_breaks(uniloom):
    uniloom.start_login.side_effect = RuntimeError("down")
    view = ConnectView("760", uniloom, on_connected=AsyncMock())
    interaction = _interaction()
    await view.connect.callback(interaction)
    assert "couldnt" in interaction.followup.send.call_args[0][0]


# ── do it / cancel ───────────────────────────────────────────────────────────

WRITES = [PendingWrite("save_item", {"title": "Login page"}, "save item: title Login page")]


async def test_only_the_asker_can_confirm(uniloom):
    view = ConfirmView("760", WRITES, run=AsyncMock())
    assert await view.interaction_check(_interaction(222)) is False


async def test_do_it_runs_the_changes_once(uniloom):
    run = AsyncMock()
    view = ConfirmView("760", WRITES, run=run)
    first, second = _interaction(), _interaction()
    await view.do_it.callback(first)
    await view.do_it.callback(second)
    run.assert_awaited_once()
    assert view.do_it.disabled and view.cancel.disabled
    first.response.edit_message.assert_awaited_once()


async def test_cancel_runs_nothing(uniloom):
    run = AsyncMock()
    view = ConfirmView("760", WRITES, run=run)
    interaction = _interaction()
    await view.cancel.callback(interaction)
    await view.do_it.callback(_interaction())
    run.assert_not_awaited()
    assert "nothing changed" in interaction.response.edit_message.call_args[1]["content"]


async def test_the_buttons_stop_after_ten_minutes(uniloom):
    run = AsyncMock()
    view = ConfirmView("760", WRITES, run=run)
    assert view.timeout == 600
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    await view.on_timeout()
    assert view.do_it.disabled
    await view.do_it.callback(_interaction())
    run.assert_not_awaited()


def test_the_question_lists_each_change():
    view = ConfirmView("760", WRITES + [PendingWrite("delete_item", {"key": "TG-9"}, "delete item: key TG-9")],
                       run=AsyncMock())
    text = view.question()
    assert "save item: title Login page" in text and "delete item: key TG-9" in text


# ── the pages uniAuth sends people back to ───────────────────────────────────

@pytest.fixture
async def web(uniloom):
    from src.web_server import WebServer
    uniloom.finish_login = AsyncMock(return_value=LoginOutcome(True, "connected", "connected to Uniloom as Oakar"))
    uniloom.public_url = "https://chrono.example"
    server = WebServer(uniloom=uniloom)
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield client, uniloom
    await client.close()


async def test_the_callback_finishes_the_sign_in_and_hides_the_code(web):
    client, uniloom = web
    response = await client.get("/oauth/callback?code=c&state=s&iss=https%3A%2F%2Fauth.example")
    assert response.status == 200
    assert "Oakar" in await response.text()
    uniloom.finish_login.assert_awaited_once()
    assert dict(uniloom.finish_login.call_args[0][0]) == {"code": "c", "state": "s", "iss": "https://auth.example"}
    # The code is in the address bar, so nothing may keep it or pass it on
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


async def test_a_failed_sign_in_page_says_so(web):
    client, uniloom = web
    uniloom.finish_login.return_value = LoginOutcome(False, "that didnt work", "run /uniloom connect again")
    response = await client.get("/oauth/callback?state=x")
    assert response.status == 400 and "/uniloom connect" in await response.text()


async def test_the_page_escapes_what_it_shows(web):
    client, uniloom = web
    uniloom.finish_login.return_value = LoginOutcome(True, "connected", "connected as <script>x</script>")
    text = await (await client.get("/oauth/callback?code=c&state=s")).text()
    assert "<script>x" not in text


async def test_the_client_metadata_document_is_served_as_json(web):
    client, _ = web
    response = await client.get("/oauth/client.json")
    assert response.status == 200 and response.content_type == "application/json"
    assert (await response.json())["client_id"] == "https://chrono.example/oauth/client.json"


# ── /uniloom and /forget ─────────────────────────────────────────────────────

@pytest.fixture
async def commands(mock_bot_user, uniloom, monkeypatch):
    from src import slash_commands
    from src.slash_commands import SlashCommandManager
    monkeypatch.setattr(slash_commands, "get_uniloom", lambda: uniloom)
    registered = {}

    def command(**kwargs):
        def register(func):
            registered[kwargs["name"]] = func
            return func
        return register

    bot = MagicMock()
    bot.user = mock_bot_user
    bot.tree.command = command
    manager = SlashCommandManager(bot=bot, game_manager=MagicMock(), user_data_manager=MagicMock(),
                                  ai_handler=MagicMock(), message_handler=MagicMock(), music_manager=MagicMock())
    await manager._register_uniloom_command()
    await manager._register_forget_command()
    return registered


async def test_uniloom_status_says_who_it_is_connected_as(commands, uniloom):
    uniloom.enabled = True
    uniloom.account = MagicMock(return_value=SimpleNamespace(name="Oakar"))
    interaction = _interaction()
    await commands["uniloom"](interaction, "status")
    args, kwargs = interaction.response.send_message.call_args
    assert "Oakar" in args[0] and kwargs["ephemeral"] is True


async def test_uniloom_disconnect_revokes(commands, uniloom):
    uniloom.enabled = True
    uniloom.disconnect = AsyncMock(return_value=True)
    interaction = _interaction()
    await commands["uniloom"](interaction, "disconnect")
    uniloom.disconnect.assert_awaited_once_with("760")


async def test_uniloom_connect_gives_the_private_link(commands, uniloom):
    uniloom.enabled = True
    interaction = _interaction()
    await commands["uniloom"](interaction, "connect")
    assert interaction.followup.send.call_args[1]["ephemeral"] is True


async def test_uniloom_says_when_it_is_not_set_up(commands, uniloom):
    uniloom.enabled = False
    interaction = _interaction()
    await commands["uniloom"](interaction, "connect")
    assert "isnt set up" in interaction.response.send_message.call_args[0][0]


async def test_forget_also_signs_out_of_uniloom(commands, uniloom):
    account = SimpleNamespace(name="Oakar")
    uniloom.take_account = MagicMock(return_value=account)
    uniloom.revoke = AsyncMock()
    interaction = _interaction()
    await commands["forget"](interaction)
    uniloom.take_account.assert_called_once_with("760")
    uniloom.revoke.assert_awaited_once_with(account)


# ── on the message path ──────────────────────────────────────────────────────

def _processor(reply):
    from src.message_handler import MessageHandler
    from src.message_processor import MessageProcessor
    ai = MagicMock()
    ai.generate_reply = AsyncMock(return_value=reply)
    processor = MessageProcessor(bot=MagicMock(guilds=[]), message_handler=MessageHandler(),
                                 ai_response_handler=ai, user_data_manager=MagicMock())
    message = MagicMock()
    message.guild = None
    message.reply = AsyncMock(return_value=MagicMock())
    message.channel.send = AsyncMock()
    message.channel.typing = MagicMock()
    return processor, message, ai


async def test_a_reply_that_needs_connecting_carries_the_button():
    from src.ai_response_handler import Reply
    from src.uniloom.state import UniloomState
    processor, message, ai = _processor(Reply("press connect below", uniloom=UniloomState("760", connect=True)))
    await processor._handle_ai_response(message, "760", "Oakar", "c", "whats on my uniloom", "")
    view = message.reply.call_args[1]["view"]
    assert isinstance(view, ConnectView) and view.owner_id == "760"
    assert ai.generate_reply.call_args[1]["uniloom"] is True


async def test_queued_changes_get_their_own_message_with_do_it(monkeypatch, uniloom):
    from src import message_processor
    from src.ai_response_handler import Reply
    from src.uniloom.state import UniloomState
    monkeypatch.setattr(message_processor, "get_uniloom", lambda: uniloom)
    state = UniloomState("760", writes=list(WRITES))
    processor, message, ai = _processor(Reply("ill add it once u press do it", uniloom=state))
    await processor._handle_ai_response(message, "760", "Oakar", "c", "add a login slice", "")
    question, kwargs = message.reply.call_args_list[-1][0][0], message.reply.call_args_list[-1][1]
    view = kwargs["view"]
    assert isinstance(view, ConfirmView) and "save item: title Login page" in question

    # Pressing do it makes the change as the asker, then answers the message again with what happened
    processor._respond = AsyncMock()
    await view.do_it.callback(_interaction())
    uniloom.call.assert_awaited_once_with("760", "save_item", {"title": "Login page"})
    note = processor._respond.call_args[1]["note"]
    assert "pressed do it" in note and "Created TG-13" in note


async def test_a_failed_change_stops_the_ones_after_it(monkeypatch, uniloom):
    from src import message_processor
    from src.ai_response_handler import Reply
    from src.uniloom.state import UniloomState
    monkeypatch.setattr(message_processor, "get_uniloom", lambda: uniloom)
    uniloom.call.side_effect = ["error: forbidden", "Created TG-14"]
    writes = [PendingWrite("save_item", {"title": "a"}, "save item: title a"),
              PendingWrite("save_item", {"title": "b"}, "save item: title b")]
    processor, message, ai = _processor(Reply("ok", uniloom=UniloomState("760", writes=writes)))
    await processor._handle_ai_response(message, "760", "Oakar", "c", "add two", "")
    view = message.reply.call_args_list[-1][1]["view"]
    processor._respond = AsyncMock()
    await view.do_it.callback(_interaction())
    assert uniloom.call.await_count == 1
    assert "not tried" in processor._respond.call_args[1]["note"]


async def test_buttons_for_someone_else_are_never_shown():
    # A reply only carries buttons for the person it answered
    from src.ai_response_handler import Reply
    from src.uniloom.state import UniloomState
    processor, message, ai = _processor(Reply("hi", uniloom=UniloomState("222", connect=True,
                                                                          writes=list(WRITES))))
    await processor._handle_ai_response(message, "760", "Oakar", "c", "hi", "")
    assert message.reply.await_count == 1 and "view" not in message.reply.call_args[1]
