"""Unit tests for the web search and page reader. Nothing here touches the network."""
import pytest
from unittest.mock import AsyncMock

from config.config import Config
from src import web_tools
from src.web_tools import NotPublicError, WebError


@pytest.fixture(autouse=True)
def clear_page_cache():
    web_tools._page_cache.clear()


@pytest.fixture
def search_key(monkeypatch):
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "tvly-test")


@pytest.fixture
def no_search_key(monkeypatch):
    monkeypatch.setattr(Config, "TAVILY_API_KEY", "")


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert web_tools._is_public(address)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.4", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "not an address",
])
def test_private_and_local_addresses_are_refused(address):
    assert not web_tools._is_public(address)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/health", "http://169.254.169.254/metadata/instance", "http://[::1]/",
    "http://localhost/", "http://db.internal/", "http://10.0.0.4/",
    "http://2130706433/", "http://0x7f.1/", "http://127.1/", "http://017700000001/", "http://[::ffff:127.0.0.1]/",
])
def test_links_into_the_machine_or_its_network_are_refused(url):
    with pytest.raises(NotPublicError):
        web_tools._check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "https://",
                                 "https://user:pass@example.com/"])
def test_links_that_are_not_plain_web_links_are_refused(url):
    with pytest.raises(WebError):
        web_tools._check_url(url)


def test_ordinary_links_pass_the_check():
    web_tools._check_url("https://example.com/a?b=1")
    web_tools._check_url("http://8.8.8.8/")


async def test_resolver_drops_private_addresses():
    resolver = web_tools._PublicOnlyResolver()
    resolver._inner.resolve = AsyncMock(return_value=[{"host": "10.0.0.4"}, {"host": "93.184.216.34"}])
    assert [entry["host"] for entry in await resolver.resolve("example.com")] == ["93.184.216.34"]


async def test_resolver_fails_a_host_that_only_points_inside():
    resolver = web_tools._PublicOnlyResolver()
    resolver._inner.resolve = AsyncMock(return_value=[{"host": "169.254.169.254"}])
    with pytest.raises(OSError):
        await resolver.resolve("sneaky.example.com")


def test_html_is_reduced_to_its_title_and_readable_text():
    html = """<html><head><title> The  Title </title><style>p{color:red}</style></head>
    <body><nav>Home | About</nav><script>var hidden = 1;</script>
    <h1>Heading</h1><p>First &amp; second.</p><ul><li>one</li><li>two</li></ul>
    <footer>copyright</footer></body></html>"""
    title, text = web_tools.html_to_text(html)
    assert title == "The Title"
    assert text == "Heading\n\nFirst & second.\n\none\n\ntwo"


async def test_read_page_never_downloads_a_private_address(monkeypatch, search_key):
    download = AsyncMock()
    extract = AsyncMock()
    monkeypatch.setattr(web_tools, "_download", download)
    monkeypatch.setattr(web_tools, "_tavily_extract", extract)
    with pytest.raises(NotPublicError):
        await web_tools.read_page("http://169.254.169.254/metadata/instance")
    download.assert_not_called()
    extract.assert_not_called()


async def test_a_redirect_to_a_private_address_is_not_handed_to_tavily(monkeypatch, search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(side_effect=NotPublicError("nope")))
    extract = AsyncMock()
    monkeypatch.setattr(web_tools, "_tavily_extract", extract)
    with pytest.raises(NotPublicError):
        await web_tools.read_page("https://example.com/redirects-inside")
    extract.assert_not_called()


async def test_read_page_wraps_the_text_in_a_block(monkeypatch, no_search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(return_value=("Example", "x" * 500)))
    block = await web_tools.read_page("example.com/page")
    assert block.startswith("=== WEB PAGE https://example.com/page ===\ntitle: Example\n")
    assert block.endswith("=== END ===")
    assert "cut off" not in block


async def test_read_page_returns_a_long_page_in_parts(monkeypatch, no_search_key):
    download = AsyncMock(return_value=("", "z" * (web_tools.MAX_PAGE_CHARS + 50)))
    monkeypatch.setattr(web_tools, "_download", download)
    block = await web_tools.read_page("https://example.com/")
    assert block.count("z") == web_tools.MAX_PAGE_CHARS
    assert f"part: characters 0-{web_tools.MAX_PAGE_CHARS} of {web_tools.MAX_PAGE_CHARS + 50}" in block
    assert f"page continues at character {web_tools.MAX_PAGE_CHARS}" in block

    rest = await web_tools.read_page("https://example.com/", offset=web_tools.MAX_PAGE_CHARS)
    assert rest.count("z") == 50
    assert f"part: characters {web_tools.MAX_PAGE_CHARS}-{web_tools.MAX_PAGE_CHARS + 50} of {web_tools.MAX_PAGE_CHARS + 50}" in rest
    assert "page continues" not in rest
    download.assert_awaited_once()


async def test_a_page_part_ends_at_a_paragraph(monkeypatch, no_search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(return_value=("", "z" * 11000 + "\n\n" + "y" * 5000)))
    block = await web_tools.read_page("https://example.com/")
    assert block.count("z") == 11000
    assert block.count("y") == 0
    assert "part: characters 0-11002 of 16002" in block

    rest = await web_tools.read_page("https://example.com/", offset=11002)
    assert rest.count("y") == 5000


async def test_an_offset_past_the_end_says_so(monkeypatch, no_search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(return_value=("", "short page")))
    block = await web_tools.read_page("https://example.com/", offset=500)
    assert "the page has 10 characters; offset 500 is past the end" in block


async def test_open_url_passes_the_offset(monkeypatch):
    from src.tools.web import OpenUrl
    page = AsyncMock(return_value="part")
    monkeypatch.setattr("src.tools.web.read_page", page)
    result = await OpenUrl()._look_up("https://example.com/big", {"url": "https://example.com/big", "offset": 12000})
    assert result == "part"
    page.assert_awaited_once_with("https://example.com/big", offset=12000)


async def test_a_nearly_empty_page_is_read_through_tavily(monkeypatch, search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(return_value=("", "Loading...")))
    monkeypatch.setattr(web_tools, "_tavily_extract", AsyncMock(return_value=("Real title", "the real article " * 40)))
    block = await web_tools.read_page("https://example.com/app")
    assert "title: Real title" in block
    assert "the real article" in block


async def test_a_page_that_fails_to_download_is_read_through_tavily(monkeypatch, search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(side_effect=WebError("the site answered with error 403")))
    monkeypatch.setattr(web_tools, "_tavily_extract", AsyncMock(return_value=("", "got it anyway")))
    assert "got it anyway" in await web_tools.read_page("https://example.com/blocked")


async def test_a_page_nobody_can_read_reports_the_first_problem(monkeypatch, search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(side_effect=WebError("the site answered with error 403")))
    monkeypatch.setattr(web_tools, "_tavily_extract", AsyncMock(side_effect=WebError("the page could not be read")))
    with pytest.raises(WebError, match="error 403"):
        await web_tools.read_page("https://example.com/blocked")


async def test_a_good_page_does_not_spend_a_tavily_request(monkeypatch, search_key):
    monkeypatch.setattr(web_tools, "_download", AsyncMock(return_value=("T", "plenty of text " * 50)))
    extract = AsyncMock()
    monkeypatch.setattr(web_tools, "_tavily_extract", extract)
    await web_tools.read_page("https://example.com/")
    extract.assert_not_called()


async def test_search_lists_the_results(monkeypatch, search_key):
    tavily = AsyncMock(return_value={"results": [
        {"title": "First", "url": "https://a.example/1", "content": "snippet one", "published_date": "Fri, 09 Oct 2026"},
        {"title": "Second", "url": "https://b.example/2", "content": "snippet\ntwo"},
        {"title": "No link"},
    ]})
    monkeypatch.setattr(web_tools, "_tavily", tavily)
    block = await web_tools.search_web("latest thing", "news", "week")
    assert block == (
        '=== WEB SEARCH RESULTS for "latest thing" ===\n'
        "1. First\n   https://a.example/1\n   published: Fri, 09 Oct 2026\n   snippet one\n"
        "2. Second\n   https://b.example/2\n   snippet two\n"
        "=== END ==="
    )
    payload = tavily.call_args[0][1]
    assert payload["topic"] == "news" and payload["time_range"] == "week"
    assert payload["max_results"] == web_tools.MAX_SEARCH_RESULTS


async def test_search_ignores_options_the_service_does_not_have(monkeypatch, search_key):
    tavily = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(web_tools, "_tavily", tavily)
    block = await web_tools.search_web("x", "sports", "decade")
    payload = tavily.call_args[0][1]
    assert payload["topic"] == "general" and "time_range" not in payload
    assert "nothing was found" in block


async def test_search_without_a_key_says_it_is_not_set_up(no_search_key):
    assert not web_tools.search_available()
    with pytest.raises(WebError, match="not set up"):
        await web_tools.search_web("anything")
