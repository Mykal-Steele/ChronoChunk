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
    monkeypatch.setattr(setup_info, "_google", {})
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


def test_off_any_cloud_the_host_line_says_it_is_unknown():
    # Changed 2026-11-17: it used to say "not an Azure VM (a dev machine?)", which the model
    # repeated as fact after the move to Google Cloud.
    assert "host        unknown, no cloud metadata answered" in setup_info.setup_summary("gpt-6-luna")


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


def test_off_any_cloud_the_self_prompt_says_it_cannot_tell_where_it_runs():
    # Changed 2026-11-17: "a dev machine (not azure)" made the bot claim it lived on a dev machine
    text = setup_info.self_prompt("gpt-6-luna")
    assert "a machine u cant identify" in text
    assert "dev machine" not in text


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
    # Raised from 1600 on 2026-11-17: the prompt now carries the last two weeks of changelog too
    assert len(setup_info.self_prompt("gpt-6-luna")) < 4200


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



# ── Google Cloud, the provider and the latest update (2026-11-17) ────────────


async def _serve_google(monkeypatch, handler):
    """Stand in for Google's metadata server, with Azure's address answering nothing."""
    app = web.Application()
    app.router.add_get("/computeMetadata/v1/instance/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(setup_info, "AZURE_METADATA_URL", "http://127.0.0.1:9/metadata")
    monkeypatch.setattr(setup_info, "GOOGLE_METADATA_URL",
                        f"http://127.0.0.1:{port}/computeMetadata/v1/instance/?recursive=true")
    return runner


GOOGLE_VM = {
    "machineType": "projects/827690319645/machineTypes/e2-micro",
    "zone": "projects/827690319645/zones/us-central1-a",
    "image": "projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105",
    "hostname": "chronochunk.us-central1-a.c.adorio.internal",
    "name": "chronochunk",
    "id": "1234567890123",
    "serviceAccounts": {"default": {"email": "chronochunk-bot@adorio.iam.gserviceaccount.com"}},
    "networkInterfaces": [{"ip": "10.128.0.2", "accessConfigs": [{"externalIp": "146.148.72.172"}]}],
}


async def test_on_google_cloud_the_bot_names_the_machine_but_not_the_project(monkeypatch):
    async def metadata(request):
        assert request.headers["Metadata-Flavor"] == "Google"
        return web.json_response(GOOGLE_VM, headers={"Metadata-Flavor": "Google"})

    runner = await _serve_google(monkeypatch, metadata)
    try:
        await setup_info.find_host()
    finally:
        await runner.cleanup()
    text = setup_info.self_prompt("gemini-3.1-flash-lite")
    assert "google cloud vm, e2-micro, us-central1-a, ubuntu-2404-noble-amd64-v20261105" in text
    assert "host        Google Cloud VM, e2-micro, us-central1-a" in setup_info.setup_summary("gemini-3.1-flash-lite")
    for secret in ("827690319645", "adorio", "chronochunk-bot", "10.128.0.2", "146.148.72.172", "1234567890123"):
        assert secret not in text


async def test_an_answer_without_googles_header_is_not_taken_for_google(monkeypatch):
    async def imposter(request):
        return web.json_response(GOOGLE_VM)

    runner = await _serve_google(monkeypatch, imposter)
    try:
        await setup_info.find_host()
    finally:
        await runner.cleanup()
    assert setup_info._host is None


def test_the_self_prompt_names_the_provider_and_the_heavy_model(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "vertex")
    monkeypatch.setenv("VERTEX_PROJECT", "adorio")
    monkeypatch.setattr(Config, "AI_HEAVY_MODEL", "gemini-3.8-flash")
    text = setup_info.self_prompt("gemini-3.1-flash-lite")
    assert "gemini on google vertex ai" in text
    assert "gemini-3.8-flash writes study sets, detailed answers, rewrites and documents" in text
    assert "adorio" not in text


CHANGELOG = """# Changelog

Intro text.

## 9.1.1 (2026-11-18)

### Fixed

- Knows where it runs.

## 9.1.0 (2026-11-17)

### Changed

- Moved to a new host. Memory starts fresh.

## 9.0.0 (2026-10-01)

### Added

- Something old that must not show.
"""


def test_the_self_prompt_carries_every_update_from_the_last_two_weeks(monkeypatch, tmp_path):
    from datetime import date
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    monkeypatch.setattr(setup_info, "CHANGELOG_PATH", str(path))
    monkeypatch.setattr(setup_info, "_today", lambda: date(2026, 11, 20))
    text = setup_info.self_prompt("gemini-3.1-flash-lite")
    assert "9.1.1 (2026-11-18)" in text and "Knows where it runs." in text
    assert "9.1.0 (2026-11-17)" in text and "Moved to a new host. Memory starts fresh." in text
    assert "Something old" not in text
    assert text.index("9.1.1") < text.index("9.1.0")


def test_with_nothing_recent_the_newest_update_still_shows(monkeypatch, tmp_path):
    from datetime import date
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG)
    monkeypatch.setattr(setup_info, "CHANGELOG_PATH", str(path))
    monkeypatch.setattr(setup_info, "_today", lambda: date(2027, 3, 1))
    text = setup_info.self_prompt("gemini-3.1-flash-lite")
    assert "9.1.1 (2026-11-18)" in text
    assert "9.1.0" not in text


def test_a_missing_changelog_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_info, "CHANGELOG_PATH", str(tmp_path / "nope.md"))
    assert "ChronoChunk v" in setup_info.self_prompt("gemini-3.1-flash-lite")


def test_the_real_changelog_gives_the_running_version():
    assert setup_info.latest_changes().startswith(f"{__version__} (")
