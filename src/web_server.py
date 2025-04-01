import asyncio
import logging
from aiohttp import web
import threading
import time

logger = logging.getLogger(__name__)

class WebServer:
    """Web server for monitoring and health checks"""
    
    def __init__(self, host="0.0.0.0", port=8080):
        """Initialize the web server"""
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.start_time = time.time()
        
        # Register routes
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/status", self.handle_status)
        
        logger.info(f"Web server initialized on {host}:{port}")
    
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
            "version": "1.0.0"  # You might want to store this elsewhere
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