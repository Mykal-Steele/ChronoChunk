"""
The saved university sign-in: one Chromium profile that every sync step shares.

The profile is signed in once by hand (python -m sync.session). After that the
steps run without a window. Nothing here reads or stores a password, token or cookie:
the browser keeps them and sends them itself.
"""
from __future__ import annotations

import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Callable, Iterator, List, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page, Response

from sync.settings import HOME, SHAREPOINT

PROFILE = HOME / "profile"
CHROMIUM = os.getenv("CHRONOCHUNK_CHROMIUM", "/usr/bin/chromium")

TEAMS_URL = "https://teams.microsoft.com/v2/"
LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com")

# How long Teams may take to show its app bar once the page is signed in
TEAMS_READY_SECONDS = 120
# How long a SharePoint site may take to open
SITE_READY_SECONDS = 60
# A page that stays on the login screen this long is waiting for a person
LOGIN_PAGE_SECONDS = 20
# How long a person gets to type their password and approve the sign-in
SIGN_IN_SECONDS = 600
# SharePoint answers 429 or 503 when it wants a pause. This many tries, then the call fails.
REQUEST_TRIES = 5


class SignInNeeded(Exception):
    """The saved sign-in has expired. Run `python -m sync.session` and sign in again."""


@contextmanager
def browser(headless: bool = True) -> Iterator[BrowserContext]:
    """The shared profile, open in Chromium. Only one process can have it open at a time."""
    # Imported here, so the parts of the sync that need no browser load without the browser package
    from playwright.sync_api import sync_playwright

    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE), headless=headless, executable_path=CHROMIUM,
            viewport={"width": 1300, "height": 850})
        try:
            yield context
        finally:
            context.close()


def first_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else context.new_page()


def _on_login_page(page: Page) -> bool:
    return (urlsplit(page.url).hostname or "") in LOGIN_HOSTS


def open_teams(context: BrowserContext, wait_for_sign_in: bool = False) -> Page:
    """
    Teams, loaded and ready to click. Raises SignInNeeded when the page ends up on the
    login screen, unless wait_for_sign_in is set, in which case it waits for a person.
    """
    page = first_page(context)
    page.goto(TEAMS_URL)
    app_bar = page.get_by_role("button", name=re.compile(r"^Assignments"))
    deadline = time.monotonic() + (SIGN_IN_SECONDS if wait_for_sign_in else TEAMS_READY_SECONDS)
    # The login page shows for a moment even when the sign-in is good, so it has to stay there to count
    on_login_since: Optional[float] = None
    while time.monotonic() < deadline:
        if app_bar.first.is_visible():
            return page
        if _on_login_page(page):
            on_login_since = on_login_since or time.monotonic()
            if not wait_for_sign_in and time.monotonic() - on_login_since > LOGIN_PAGE_SECONDS:
                raise SignInNeeded("Teams is asking for a sign-in")
        else:
            on_login_since = None
        page.wait_for_timeout(500)
    raise SignInNeeded("Teams did not finish loading")


def open_site(context: BrowserContext, site_url: str) -> Page:
    """
    A SharePoint site's home page in a new tab. A site has to be opened like this once
    before its API answers: going straight to an /_api/ address gives 403.
    """
    page = context.new_page()
    page.goto(site_url, wait_until="domcontentloaded")
    deadline = time.monotonic() + SITE_READY_SECONDS
    # Single sign-on passes through the login page on its way back, so it has to stay there to count
    on_login_since: Optional[float] = None
    while time.monotonic() < deadline:
        if page.url.startswith(SHAREPOINT):
            page.wait_for_load_state("domcontentloaded")
            return page
        if _on_login_page(page):
            on_login_since = on_login_since or time.monotonic()
            if time.monotonic() - on_login_since > LOGIN_PAGE_SECONDS:
                raise SignInNeeded("SharePoint is asking for a sign-in")
        else:
            on_login_since = None
        page.wait_for_timeout(500)
    raise SignInNeeded("SharePoint did not finish loading")


def retry_after(header: Optional[str], default: float) -> float:
    """Seconds to wait from a Retry-After header, which is a number of seconds or a date. default when it is neither."""
    if not header:
        return default
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    try:
        return max((parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return default


def get(context: BrowserContext, url: str, accept: str = "application/json"):
    """One GET with the profile's sign-in, retried when the server asks for a pause."""
    wait = 2.0
    for attempt in range(REQUEST_TRIES):
        reply = context.request.get(url, headers={"accept": accept}, timeout=120_000)
        if reply.status not in (429, 503) or attempt == REQUEST_TRIES - 1:
            return reply
        time.sleep(min(retry_after(reply.headers.get("retry-after"), wait), 120))
        wait *= 2
    raise AssertionError("unreachable")


def get_json(context: BrowserContext, url: str) -> Any:
    reply = get(context, url)
    if not reply.ok:
        raise RuntimeError(f"GET {urlsplit(url).path} answered {reply.status}: {reply.text()[:200]}")
    return reply.json()


def get_all(context: BrowserContext, url: str) -> List[dict]:
    """Every item of a paged listing, following the next links to the end."""
    items: List[dict] = []
    next_url: Optional[str] = url
    while next_url:
        page = get_json(context, next_url)
        items.extend(page.get("value") or [])
        next_url = page.get("@odata.nextLink")
    return items


class Recorder:
    """
    Keeps the JSON answers a page loads from one host, for the parts of Teams that have
    no address a script may call directly. keep decides which paths are worth keeping.
    """

    def __init__(self, context: BrowserContext, host: str, keep: Optional[Callable[[str], bool]] = None):
        self.host = host
        self.keep = keep or (lambda path: True)
        self.answers: List[dict] = []
        context.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        parts = urlsplit(response.url)
        if not (parts.hostname or "").endswith(self.host) or not self.keep(parts.path):
            return
        try:
            body = response.json()
        except Exception:
            return
        self.answers.append({"method": response.request.method, "host": parts.hostname, "path": parts.path,
                             "query": parts.query, "status": response.status, "body": body})


def main() -> None:
    """Open a window and wait for a person to sign in. The profile remembers it."""
    with browser(headless=False) as context:
        print("Sign in to Teams in the window that opened.", flush=True)
        open_teams(context, wait_for_sign_in=True)
        open_site(context, SHAREPOINT)
        print(f"Signed in. The profile is at {PROFILE}")


if __name__ == "__main__":
    try:
        main()
    except SignInNeeded as error:
        sys.exit(f"Not signed in: {error}")
