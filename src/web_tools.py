"""Lets the bot look things up: a web search through Tavily, and a reader for links people give it."""
import asyncio
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from config.config import Config
from src.exceptions import BotError

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

# A page is cut here before the model reads it. About 3,000 tokens.
MAX_PAGE_CHARS = 12000
# Stop downloading a page after this much
MAX_PAGE_BYTES = 3_000_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 15
TAVILY_TIMEOUT_SECONDS = 25
MAX_SEARCH_RESULTS = 5
MAX_SNIPPET_CHARS = 700
# A page with less text than this probably needs JavaScript to show anything, so Tavily reads it instead
THIN_PAGE_CHARS = 300

SEARCH_TOPICS = ("general", "news")
SEARCH_TIME_RANGES = ("day", "week", "month", "year")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ChronoChunkBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    "Accept-Language": "en,*;q=0.5",
}
_SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "aside", "form", "button"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class WebError(BotError):
    """A search or a page read that failed, worded so it can be passed on to the model."""


class NotPublicError(WebError):
    """The link points at this machine or a private network."""


def search_available() -> bool:
    return bool(Config.TAVILY_API_KEY)


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_global and not ip.is_multicast


def _check_url(url: str) -> None:
    """Refuse anything that is not a plain web link to a public host."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise WebError("that is not a valid web link")
    if parts.scheme not in ("http", "https") or not host:
        raise WebError("that is not a valid web link")
    if parts.username or parts.password:
        raise WebError("links with a username or password in them are not opened")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise NotPublicError("that address is not on the public internet")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Old spellings of an IPv4 address such as 2130706433, 0x7f.1 or 127.1 are connected to
        # without a lookup, so the resolver below never sees them. No real site uses them.
        try:
            socket.inet_aton(host)
        except OSError:
            return  # a hostname, checked when it is resolved
        raise NotPublicError("that address is not on the public internet")
    if not _is_public(host):
        raise NotPublicError("that address is not on the public internet")


class _PublicOnlyResolver(AbstractResolver):
    """
    Looks a hostname up and keeps only public addresses. Without it, a link could
    name a host that resolves to this machine or to Azure's metadata address.
    """

    def __init__(self):
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        found = await self._inner.resolve(host, port, family)
        public = [entry for entry in found if _is_public(entry["host"])]
        if not public:
            raise OSError(f"{host} does not resolve to a public address")
        return public

    async def close(self):
        await self._inner.close()


class _TextExtractor(HTMLParser):
    """Pulls the readable text and the title out of an HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._parts: List[str] = []
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "title" and not self._skipping:
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skipping:
            self._parts.append(data)

    def text(self) -> str:
        return _tidy("".join(self._parts))


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(html: str) -> Tuple[str, str]:
    """Return (title, readable text) for an HTML page."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return _tidy(extractor.title), extractor.text()


async def _download(url: str) -> Tuple[str, str]:
    """Fetch a page from this machine and return (title, text). Every redirect is checked like the first link."""
    connector = aiohttp.TCPConnector(resolver=_PublicOnlyResolver(), use_dns_cache=False)
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=_HEADERS) as session:
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(url)
            async with session.get(url, allow_redirects=False) as response:
                location = response.headers.get("Location")
                if response.status in (301, 302, 303, 307, 308) and location:
                    url = urljoin(url, location)
                    continue
                if response.status >= 400:
                    raise WebError(f"the site answered with error {response.status}")
                kind = response.content_type or ""
                if not (kind.startswith("text/") or kind in ("application/xhtml+xml", "application/json")):
                    raise WebError(f"the link is a {kind or 'file'}, not a page of text")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_PAGE_BYTES:
                        break
                try:
                    body = b"".join(chunks).decode(response.charset or "utf-8", errors="replace")
                except LookupError:
                    body = b"".join(chunks).decode("utf-8", errors="replace")
                if kind in ("text/html", "application/xhtml+xml"):
                    return html_to_text(body)
                return "", _tidy(body)
        raise WebError("the link redirected too many times")


async def _tavily(url: str, payload: dict) -> dict:
    """POST to Tavily and return the JSON answer."""
    if not Config.TAVILY_API_KEY:
        raise WebError("web search is not set up on this bot")
    headers = {"Authorization": f"Bearer {Config.TAVILY_API_KEY}"}
    timeout = aiohttp.ClientTimeout(total=TAVILY_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 429:
                    raise WebError("the search service is rate limiting the bot, try again in a minute")
                if response.status in (432, 433):
                    raise WebError("the bot has used up its web searches for this month")
                if response.status >= 400:
                    logger.error(f"Tavily answered {response.status}: {(await response.text())[:300]}")
                    raise WebError(f"the search service answered with error {response.status}")
                return await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning(f"Tavily request failed: {e!r}")
        raise WebError("the search service did not answer")


async def _tavily_extract(url: str) -> Tuple[str, str]:
    """Have Tavily read a page. It gets through pages that need JavaScript or block plain downloads."""
    data = await _tavily(TAVILY_EXTRACT_URL, {"urls": [url]})
    results = data.get("results") or []
    if not results:
        raise WebError("the page could not be read")
    first = results[0]
    return str(first.get("title") or ""), _tidy(str(first.get("raw_content") or ""))


async def read_page(url: str) -> str:
    """
    Open a web link and return its text in a === block for the model. The page is
    downloaded from this machine first. When that fails or comes back nearly empty,
    Tavily reads it instead.
    """
    url = (url or "").strip().strip("<>")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "https://" + url
    _check_url(url)

    title, text, problem = "", "", ""
    try:
        title, text = await _download(url)
    except NotPublicError:
        raise
    except WebError as e:
        problem = str(e)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        logger.info(f"Could not download {url}: {e!r}")
        problem = "the site did not answer"

    if len(text) < THIN_PAGE_CHARS and search_available():
        try:
            other_title, other_text = await _tavily_extract(url)
            if len(other_text) > len(text):
                title, text = other_title or title, other_text
        except WebError as e:
            logger.info(f"Tavily could not read {url} either: {e}")

    if not text:
        raise WebError(problem or "the page has no readable text")

    lines = [f"=== WEB PAGE {url} ==="]
    if title:
        lines.append(f"title: {title}")
    lines.append(text[:MAX_PAGE_CHARS])
    if len(text) > MAX_PAGE_CHARS:
        lines.append("(the page is longer than this, the rest was cut off)")
    lines.append("=== END ===")
    return "\n".join(lines)


async def search_web(query: str, topic: Optional[str] = None, time_range: Optional[str] = None) -> str:
    """Search the web and return the top results in a === block for the model."""
    payload = {
        "query": query[:400],
        "max_results": MAX_SEARCH_RESULTS,
        "search_depth": "basic",
        "topic": topic if topic in SEARCH_TOPICS else "general",
    }
    if time_range in SEARCH_TIME_RANGES:
        payload["time_range"] = time_range
    data = await _tavily(TAVILY_SEARCH_URL, payload)

    results = [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")]
    if not results:
        return f'=== WEB SEARCH RESULTS for "{query}" ===\nnothing was found\n=== END ==='

    lines = [f'=== WEB SEARCH RESULTS for "{query}" ===']
    for number, result in enumerate(results, start=1):
        lines.append(f"{number}. {_tidy(str(result.get('title') or 'untitled'))}")
        lines.append(f"   {result['url']}")
        if result.get("published_date"):
            lines.append(f"   published: {result['published_date']}")
        snippet = _tidy(str(result.get("content") or "")).replace("\n", " ")
        if snippet:
            lines.append(f"   {snippet[:MAX_SNIPPET_CHARS]}")
    lines.append("=== END ===")
    return "\n".join(lines)
