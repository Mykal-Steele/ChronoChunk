"""/setup: where the bot runs and the settings it runs with."""
import pytest
from aiohttp import web

from types import SimpleNamespace

from config.config import Config
from src import setup_info
from src.usage_guard import get_usage_guard
from src.version import __version__


@pytest.fixture(autouse=True)
def no_host(monkeypatch):
    monkeypatch.setattr(setup_info, "_host", None)
    monkeypatch.setattr(setup_info, "_azure", {})
    monkeypatch.setattr(setup_info, "_population", None)


async def _serve(monkeypatch, handler):
    """Stand in for Azure's metadata address with a server on this machine."""
    app = web.Application()
    app.router.add_get("/metadata", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(setup_info, "AZURE_METADATA_URL", f"http://127.0.0.1:{port}/metadata")
    return runner


def test_the_summary_names_the_version_the_models_and_the_budget(monkeypatch):
    monkeypatch.setattr(Config, "AI_BACKGROUND_DEPLOYMENT", "gpt-5-nano")
    monkeypatch.setattr(Config, "AI_CHAT_REASONING_EFFORT", "none")
    text = setup_info.setup_summary("gpt-6-luna")
    assert f"version     {__version__}" in text
    assert "chat model  gpt-6-luna, reasoning none" in text
    assert "background  gpt-5-nano" in text
    assert "bot budget  $20 a month, $2.50 a day, new month on day 14" in text
    assert "per person  $0.50 a day, $1.50 over 7 days" in text
    assert "chat 50 per 30 min" in text and "file 30 a day" in text
    assert text.endswith("#where-it-runs>")
    assert "`/stats` has the full count" in text


def test_the_summary_fits_in_one_discord_message():
    assert len(setup_info.setup_summary("gpt-6-luna")) < 1900


def test_the_summary_leaves_out_every_secret(monkeypatch):
    monkeypatch.setattr(Config, "DISCORD_TOKEN", "discord-secret-token")
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "tavily-secret-key")
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"424242424242"})
    monkeypatch.setenv("AZURE_OPENAI_KEY", "azure-secret-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://secret-resource.openai.azure.com/openai/v1")
    text = setup_info.setup_summary("gpt-6-luna")
    for secret in ("discord-secret-token", "tavily-secret-key", "424242424242", "azure-secret-key", "secret-resource"):
        assert secret not in text
    assert "web search  on" in text


def test_web_search_reads_as_off_without_a_key(monkeypatch):
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "")
    assert "web search  off, no search key" in setup_info.setup_summary("gpt-6-luna")


def test_off_azure_the_host_line_says_so():
    assert "host        not an Azure VM" in setup_info.setup_summary("gpt-6-luna")


async def test_on_azure_the_host_line_names_the_vm(monkeypatch):
    async def metadata(request):
        assert request.headers["Metadata"] == "true"
        return web.json_response({"name": "chronochunk-vm", "vmSize": "Standard_B2ats_v2", "location": "southeastasia"})

    runner = await _serve(monkeypatch, metadata)
    try:
        await setup_info.find_host()
    finally:
        await runner.cleanup()
    assert "host        Azure VM chronochunk-vm, Standard_B2ats_v2, southeastasia" in setup_info.setup_summary("gpt-6-luna")


async def test_an_address_that_answers_something_else_is_not_taken_for_azure(monkeypatch):
    async def not_azure(request):
        return web.Response(text="<html>router login</html>", content_type="text/html")

    runner = await _serve(monkeypatch, not_azure)
    try:
        await setup_info.find_host()
    finally:
        await runner.cleanup()
    assert setup_info._host is None


async def test_no_answer_from_the_metadata_address_is_not_an_error(monkeypatch):
    # Nothing listens on port 9 of this machine
    monkeypatch.setattr(setup_info, "AZURE_METADATA_URL", "http://127.0.0.1:9/metadata")
    await setup_info.find_host()
    assert setup_info._host is None


def test_a_new_bot_says_it_has_used_nothing():
    assert "used so far nothing yet" in setup_info.setup_summary("gpt-6-luna")


def test_the_summary_adds_up_everything_used_so_far(database):
    database.execute(
        "INSERT INTO usage_daily(day, user_id, feature, model, requests, input_tokens, output_tokens, cost) "
        "VALUES ('2025-01-20', '1', 'chat', 'gpt-5-mini', 1200, 4000000, 60000, 1.5)")
    database.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES ('1', 'Kruskal', 'x', 'x')")
    database.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES ('2', 'Prim', 'x', 'x')")
    # One call today: 1,000 tokens in and 500 out at gpt-6-luna prices is $0.00035
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500, prompt_tokens_details=None)
    get_usage_guard().record(usage, "gpt-6-luna")

    text = setup_info.setup_summary("gpt-6-luna")

    assert "used so far since Jan 20, 2025" in text
    assert "ai calls    1,201" in text
    assert "tokens      4.0M in, 60.5K out" in text
    assert "ai cost     $1.50 in all, $0.00 of $20 this month" in text
    assert "people met  2" in text


def test_no_file_path_is_shown():
    text = setup_info.setup_summary("gpt-6-luna")
    assert "chronochunk.db" not in text and "update.sh" not in text and "deployment/" not in text


def test_the_summary_says_whether_the_test_bot_is_answered(monkeypatch):
    assert "test bot    off" in setup_info.setup_summary("gpt-6-luna")
    monkeypatch.setattr(Config, "TEST_BOT_ID", "777000777000777000")
    monkeypatch.setattr(Config, "TEST_CHANNEL_ID", "999000999000999000")
    text = setup_info.setup_summary("gpt-6-luna")
    assert "test bot    answered in one test channel" in text
    assert "777000777000777000" not in text and "999000999000999000" not in text


def test_the_self_prompt_names_the_version_and_the_models(monkeypatch):
    monkeypatch.setattr(Config, "AI_BACKGROUND_DEPLOYMENT", "gpt-5-nano")
    monkeypatch.setattr(Config, "AI_CHAT_REASONING_EFFORT", "low")
    text = setup_info.self_prompt("gpt-6-luna")
    assert f"ChronoChunk v{__version__}" in text
    assert "chat model is gpt-6-luna (reasoning low)" in text
    assert "gpt-5-nano picks facts out of messages" in text
    assert "a discord bot" in text


def test_off_azure_the_self_prompt_says_it_is_a_dev_machine():
    assert "a dev machine (not azure)" in setup_info.self_prompt("gpt-6-luna")


async def test_the_self_prompt_names_azure_but_not_the_account(monkeypatch):
    async def metadata(request):
        return web.json_response({
            "name": "chronochunk-vm",
            "vmSize": "Standard_B2ats_v2",
            "location": "southeastasia",
            "zone": "1",
            "osType": "Linux",
            "publisher": "Canonical",
            "offer": "ubuntu-24_04-lts",
            "sku": "server",
            "subscriptionId": "sub-secret",
            "resourceGroupName": "rg-secret",
            "resourceId": "/subscriptions/sub-secret/resourceGroups/rg-secret/providers/x",
            "vmId": "vm-secret",
            "tags": "owner=secret",
        })

    runner = await _serve(monkeypatch, metadata)
    try:
        await setup_info.find_host()
    finally:
        await runner.cleanup()

    text = setup_info.self_prompt("gpt-6-luna")
    assert "azure vm, Standard_B2ats_v2, southeastasia, zone 1" in text
    assert "Linux Canonical ubuntu-24_04-lts server" in text
    for secret in ("chronochunk-vm", "sub-secret", "rg-secret", "vm-secret", "owner=secret"):
        assert secret not in text


def test_the_self_prompt_leaves_out_every_secret(monkeypatch):
    monkeypatch.setattr(Config, "DISCORD_TOKEN", "discord-secret-token")
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "tavily-secret-key")
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"424242424242"})
    monkeypatch.setenv("AZURE_OPENAI_KEY", "azure-secret-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://secret-resource.openai.azure.com/openai/v1")
    text = setup_info.self_prompt("gpt-6-luna")
    for secret in ("discord-secret-token", "tavily-secret-key", "424242424242", "azure-secret-key", "secret-resource"):
        assert secret not in text
    assert "chronochunk.db" not in text and "reply_log.key" not in text


def test_the_self_prompt_stays_short():
    assert len(setup_info.self_prompt("gpt-6-luna")) < 1600


def test_the_self_prompt_counts_servers_and_people(monkeypatch):
    monkeypatch.setattr(setup_info, "_population", lambda: (3, 120))
    text = setup_info.self_prompt("gpt-6-luna")
    assert "u are in 3 servers with 120 people in them, counts only" in text


def test_a_broken_population_provider_is_ignored(monkeypatch):
    def boom():
        raise RuntimeError("no discord yet")

    monkeypatch.setattr(setup_info, "_population", boom)
    assert "servers" not in setup_info.self_prompt("gpt-6-luna")


def test_memory_reads_the_container_cap(monkeypatch):
    def fake(path):
        return {
            "/proc/self/status": "VmRSS: 98304 kB",
            "/sys/fs/cgroup/memory.max": str(640 * 1024 * 1024),
        }.get(path, "")

    monkeypatch.setattr(setup_info, "_read", fake)
    assert setup_info._memory() == "96 MB of 640 MB"


def test_memory_falls_back_to_the_machine_total(monkeypatch):
    def fake(path):
        return {
            "/proc/self/status": "VmRSS: 98304 kB",
            "/proc/meminfo": "MemTotal:       4194304 kB",
        }.get(path, "")

    monkeypatch.setattr(setup_info, "_read", fake)
    assert setup_info._memory() == "96 MB of 4 GB"
