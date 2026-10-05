"""Unit tests for WebServer HTTP endpoints."""
import json
import pytest
from aiohttp.test_utils import TestClient, TestServer
from src.web_server import WebServer


@pytest.fixture
async def web_client():
    server_obj = WebServer(host="127.0.0.1", port=0)  # port=0 → OS picks free port
    async with TestClient(TestServer(server_obj.app)) as client:
        yield client


async def test_root_endpoint_returns_200(web_client):
    resp = await web_client.get("/")
    assert resp.status == 200


async def test_root_endpoint_body(web_client):
    resp = await web_client.get("/")
    text = await resp.text()
    assert "ChronoChunk" in text


async def test_health_endpoint_returns_200(web_client):
    resp = await web_client.get("/health")
    assert resp.status == 200


async def test_health_endpoint_body(web_client):
    resp = await web_client.get("/health")
    text = await resp.text()
    assert text.strip() == "OK"


async def test_status_endpoint_returns_200(web_client):
    resp = await web_client.get("/status")
    assert resp.status == 200


async def test_status_endpoint_is_json(web_client):
    resp = await web_client.get("/status")
    data = await resp.json()
    assert isinstance(data, dict)


async def test_status_endpoint_has_required_fields(web_client):
    resp = await web_client.get("/status")
    data = await resp.json()
    assert "status" in data
    assert "uptime_seconds" in data


async def test_status_endpoint_online(web_client):
    resp = await web_client.get("/status")
    data = await resp.json()
    assert data["status"] == "online"


async def test_status_uptime_is_non_negative(web_client):
    resp = await web_client.get("/status")
    data = await resp.json()
    assert data["uptime_seconds"] >= 0


async def test_server_starts_and_stops():
    """WebServer.start() / stop() should not raise."""
    ws = WebServer(host="127.0.0.1", port=0)
    await ws.start()
    await ws.stop()
