import asyncio
import threading
import types

import pytest
from mcp.client import Client
from starlette.testclient import TestClient

from supermarkt import mcp_server, runtime
from supermarkt.asgi import app

OFFERS = [
    {"offer_id": "a", "retailer": "Kaufland", "product": "Hochland Schmelzkäse", "pack": "200 g", "unit_price": "7,95 €/kg",
     "regular_price_text": "1,99 €", "effective_price": 1.99, "effective_price_text": "1,99 €", "validity": "bis Samstag", "image_url": "https://img.example/a.jpg"},
    {"offer_id": "b", "retailer": "REWE", "product": "Schmelzkäse", "pack": "", "unit_price": "",
     "regular_price_text": "1,49 €", "effective_price": 1.49, "effective_price_text": "1,49 €", "validity": "gültig bis 20.09.", "image_url": None},
]


class FakeEngine:
    def page(self, snapshot, loyalty_programs=(), **_kwargs):
        offers = [dict(o) for o in OFFERS]
        if loyalty_programs:
            offers[0].update(effective_price=1.59, effective_price_text="1,59 €", loyalty_benefit="Kaufland Card")
        return {"offers": offers, "available_loyalty_programs": [{"id": "kaufland_xtra", "label": "Kaufland Card", "retailers": ["Kaufland"], "priced_offer_count": 1}]}


class FakeImages:
    def get(self, **_kwargs):
        return types.SimpleNamespace(data=b"\x89PNGfake", content_type="image/png", origin="test")


@pytest.fixture(autouse=True)
def fake_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(runtime, "get_image_service", lambda: FakeImages())
    monkeypatch.setattr(mcp_server, "_load_snapshot", lambda plz, retailers, refresh=False: {"plz": plz})
    monkeypatch.setenv("SUPERMARKT_MCP_WARMUP", "0")
    mcp_server._inflight.clear()


def call(name, arguments):
    async def run():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(run())


def test_find_offers_lists_cheapest_first_with_bonus_price_and_image():
    result = call("find_offers", {"product": "Schmelzkäse", "postal_code": "01067"})
    offers = result.structured_content["offers"]
    assert [o["retailer"] for o in offers] == ["REWE", "Kaufland"]
    assert offers[1]["price_without_bonus"] == "1,99 €" and offers[1]["price_with_bonus"] == "1,59 €"
    assert offers[1]["bonus_program"] == "Kaufland Card" and offers[0]["price_with_bonus"] is None
    text = result.content[0].text
    assert "1,59 € mit Kaufland Card" in text and "gültig gültig" not in text
    assert [block.type for block in result.content].count("image") == 1


def test_find_offers_without_images_and_with_limit():
    result = call("find_offers", {"product": "x", "postal_code": "01067", "with_images": False, "limit": 1})
    assert len(result.structured_content["offers"]) == 1
    assert all(block.type == "text" for block in result.content)


def test_find_offers_rejects_bad_input():
    assert call("find_offers", {"product": "x", "postal_code": "abc"}).is_error
    assert call("find_offers", {"product": " ", "postal_code": "01067"}).is_error
    assert call("find_offers", {"product": "x", "postal_code": "01067", "retailers": ["Nirgendwo"]}).is_error


def test_slow_load_answers_politely_and_keeps_loading(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(mcp_server, "_load_snapshot", lambda plz, retailers, refresh=False: release.wait(10) and {"plz": plz})
    monkeypatch.setattr(mcp_server, "LOAD_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr("asyncio.wait", _short_wait(asyncio.wait))

    async def run():
        async with Client(mcp_server.mcp) as client:
            first = await client.call_tool("find_offers", {"product": "x", "postal_code": "01067"})
            release.set()
            await asyncio.sleep(0.3)
            second = await client.call_tool("find_offers", {"product": "x", "postal_code": "01067"})
            return first, second
    first, second = asyncio.run(run())
    assert first.structured_content["status"] == "loading"
    assert "noch einmal" in first.content[0].text
    assert second.structured_content["found"] == 2


def _short_wait(real):
    async def wait(tasks, timeout=None, **kwargs):
        return await real(tasks, timeout=min(timeout or 0.2, 0.2), **kwargs)
    return wait


def test_list_retailers_and_programs():
    retailers = call("list_retailers", {}).structured_content["result"]
    assert any(r["name"] == "Kaufland" and r["bonus_programs"] for r in retailers)
    assert call("list_bonus_programs", {"postal_code": "01067"}).structured_content["programs"][0]["id"] == "kaufland_xtra"


def test_http_endpoint_honours_optional_api_key(monkeypatch):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/mcp", json=payload, headers=headers).status_code == 200
        monkeypatch.setenv("SUPERMARKT_API_KEY", "secret-for-test")
        assert client.post("/mcp", json=payload, headers=headers).status_code == 401
        assert client.post("/mcp", json=payload, headers={**headers, "Authorization": "Bearer secret-for-test"}).status_code == 200
