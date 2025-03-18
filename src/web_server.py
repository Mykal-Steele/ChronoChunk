import logging
import asyncio
from aiohttp import web

# Setup logging
logger = logging.getLogger(__name__)

class WebServer:
    """Simple web server for handling health checks and uptime monitoring"""
    
    def __init__(self, host="0.0.0.0", port=8080):
        """Initialize the web server"""
        self.host = host
        self.port = port
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_get)
        self._app.router.add_get("/health", self._handle_get)
        self._runner = None
        self._site = None
    
    async def _handle_get(self, request: web.Request) -> web.Response:
        """Simple endpoint for health checks"""
        return web.Response(text="OK")
    
    async def start(self):
        """Start the web server"""
        try:
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()
            logger.info(f"Web server started on port {self.port}")
        except Exception as e:
            logger.error(f"Error starting web server: {e}")
            raise
    
    async def stop(self):
        """Stop the web server"""
        try:
            if self._site:
                await self._site.stop()
            if self._runner:
                await self._runner.cleanup()
            logger.info("Web server stopped")
        except Exception as e:
            logger.error(f"Error stopping web server: {e}") 