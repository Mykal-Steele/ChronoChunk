import html
import logging
from aiohttp import web
import time
from typing import Any, Optional

from src.version import __version__

logger = logging.getLogger(__name__)

class WebServer:
    """Web server for monitoring and health checks"""
    
    def __init__(self, host="0.0.0.0", port=8080, uniloom: Optional[Any] = None):
        """Initialize the web server"""
        self.host = host
        self.port = port
        # The Uniloom sign-in (docs/uniloom-plan.md). Only /oauth/ is reachable from the internet,
        # through the Cloudflare Tunnel; the rest is for the health check inside the container
        self._uniloom = uniloom
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.start_time = time.time()
        
        # Register routes
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/status", self.handle_status)
        self.app.router.add_get("/oauth/start", self.handle_oauth_start)
        self.app.router.add_get("/oauth/callback", self.handle_oauth_callback)
        self.app.router.add_get("/oauth/client.json", self.handle_client_metadata)
        
        logger.info(f"Web server initialized on {host}:{port}")
    
    @property
    def uniloom(self) -> Any:
        if self._uniloom is None:
            from src.uniloom.service import get_uniloom
            self._uniloom = get_uniloom()
        return self._uniloom

    async def handle_oauth_start(self, request):
        """
        Where a sign-in link opens: which Discord user the Uniloom account will be tied to,
        before going on to uniAuth. Someone sent another person's link can see it isn't theirs.
        """
        found = self.uniloom.start_page(request.query.get("state", ""))
        if found is None:
            return _page("that link didnt work", "it ran out or was already used. run /uniloom connect in Discord "
                         "for a new one", 400)
        name, authorize_url = found
        return _page("connect Uniloom", f"this connects ur Uniloom account to the Discord user {name}, so "
                     "ChronoChunk can use it when they ask. only go on if that's u. if someone sent u this "
                     "link, close this page", 200, link=(authorize_url, "sign in to Uniloom"))

    async def handle_oauth_callback(self, request):
        """Where uniAuth sends people back after they sign in to Uniloom."""
        params = {key: request.query[key] for key in ("code", "state", "iss", "error") if key in request.query}
        outcome = await self.uniloom.finish_login(params)
        return _page(outcome.title, outcome.message, 200 if outcome.ok else 400)

    async def handle_client_metadata(self, request):
        """The bot's Client ID Metadata Document: uniAuth reads it to know who ChronoChunk is."""
        from src.uniloom.oauth import client_metadata
        if not self.uniloom.public_url:
            raise web.HTTPNotFound()
        response = web.json_response(client_metadata(self.uniloom.public_url))
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    async def handle_root(self, request):
        """Root endpoint"""
        return web.Response(text="ChronoChunk Discord Bot")
    
    async def handle_health(self, request):
        """Health check endpoint"""
        return web.Response(text="OK")
    
    async def handle_status(self, request):
        """Status endpoint with basic metrics"""
        uptime = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        status = {
            "status": "online",
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "uptime_seconds": uptime,
            "version": __version__
        }
        
        return web.json_response(status)
        
    async def start(self):
        """Start the web server"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(f"Web server running on http://{self.host}:{self.port}")
        
    async def stop(self):
        """Stop the web server"""
        if self.runner:
            logger.info("Shutting down web server")
            await self.runner.cleanup()
            logger.info("Web server stopped")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{title} · ChronoChunk</title>
<style>
  :root {{ color-scheme: light dark; --bg: #f6f6f7; --card: #fff; --ink: #1d1d22; --soft: #5b5b66; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #17171b; --card: #222228; --ink: #ececf1; --soft: #a3a3ad; }} }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
         color: var(--ink); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ background: var(--card); padding: 28px 32px; border-radius: 12px; max-width: 26rem; margin: 16px; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 8px; }}
  p {{ margin: 0; color: var(--soft); }}
  a {{ display: inline-block; margin-top: 18px; padding: 10px 16px; border-radius: 8px; background: #5865f2;
       color: #fff; text-decoration: none; font-weight: 600; }}
</style>
</head>
<body><main><h1>{title}</h1><p>{message}</p>{link}</main></body>
</html>
"""


def _page(title: str, message: str, status: int, link: Optional[tuple] = None) -> web.Response:
    """
    A small page for the end of a sign-in. The address bar still holds the code, so the
    page loads nothing, sends no referrer and is never cached.
    """
    button = f'<a href="{html.escape(link[0])}">{html.escape(link[1])}</a>' if link else ""
    response = web.Response(text=_PAGE.format(title=html.escape(title), message=html.escape(message), link=button),
                            content_type="text/html", status=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
