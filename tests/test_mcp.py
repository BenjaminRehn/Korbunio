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
     "regular_price_text": "1,99 €", "effective_price": 1.99, "effective_price_text": "1,99 €", "validity": "Kaufland, gültig 2026-09-17 bis 2026-09-23", "image_url": "https://img.example/a.jpg"},
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
    mcp_server._last_used.clear()
    mcp_server._new_postal_codes.clear()


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
    assert "gültig 17.09.2026 bis 23.09.2026" in text and "Kaufland, gültig" not in text
    assert [block.type for block in result.content].count("image") == 1


def test_only_one_image_by_default_and_up_to_three_on_request():
    assert [b.type for b in call("find_offers", {"product": "x", "postal_code": "01067"}).content].count("image") == 1
    result = call("find_offers", {"product": "x", "postal_code": "01067", "max_images": 0})
    assert all(block.type == "text" for block in result.content)


def test_search_widens_to_single_words_when_nothing_matches(monkeypatch):
    class Picky(FakeEngine):
        def page(self, snapshot, loyalty_programs=(), keywords=(), **kwargs):
            if not keywords:
                return {"offers": [], "available_loyalty_programs": []}
            return super().page(snapshot, loyalty_programs=loyalty_programs, **kwargs)
    monkeypatch.setattr(runtime, "get_engine", lambda: Picky())
    result = call("find_offers", {"product": "Schmelzkäses", "postal_code": "01067", "with_images": False})
    assert result.structured_content["found"] == 2 and "ähnliche Treffer" in result.content[0].text


def test_too_many_new_postal_codes_are_refused(monkeypatch):
    monkeypatch.setattr(mcp_server, "NEW_POSTAL_CODE_LIMIT", 2)
    assert not call("find_offers", {"product": "x", "postal_code": "01067", "with_images": False}).is_error
    assert not call("find_offers", {"product": "x", "postal_code": "01069", "with_images": False}).is_error
    assert call("find_offers", {"product": "x", "postal_code": "01097", "with_images": False}).is_error
    assert not call("find_offers", {"product": "x", "postal_code": "01067", "with_images": False}).is_error


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


class _FakeKitchenOwl:
    """Kleiner KitchenOwl-Ersatz auf localhost, der Anfragen mitschreibt."""

    def __init__(self):
        import json
        from http.server import BaseHTTPRequestHandler, HTTPServer
        outer = self
        self.items: list[dict] = [{"id": 1, "name": "Milch"}]
        self.requests: list[tuple] = []

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path, self.headers.get("Authorization")))
                self._reply(outer.items)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path, self.headers.get("Authorization"), body))
                outer.items.append({"id": len(outer.items) + 1, **body})
                self._reply({"id": len(outer.items)})

            def log_message(self, *_args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def kitchenowl(monkeypatch):
    fake = _FakeKitchenOwl()
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_URL", fake.url)
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_TOKEN", "test-token")
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_LIST_ID", "7")
    assert mcp_server.register_shopping_tool()
    yield fake
    fake.close()
    monkeypatch.delenv("SUPERMARKT_KITCHENOWL_URL")
    mcp_server.register_shopping_tool()


def test_shopping_tool_is_hidden_without_kitchenowl(monkeypatch):
    monkeypatch.delenv("SUPERMARKT_KITCHENOWL_URL", raising=False)
    assert not mcp_server.register_shopping_tool()

    async def names():
        async with Client(mcp_server.mcp) as client:
            return [tool.name for tool in (await client.list_tools()).tools]
    assert "add_to_shopping_list" not in asyncio.run(names())


def test_shopping_tool_adds_with_note_and_skips_duplicates(kitchenowl):
    result = call("add_to_shopping_list", {"item": "Hochland Schmelzkäse", "retailer": "Kaufland", "price": "1,59 €"})
    assert result.structured_content == {"item": "Hochland Schmelzkäse", "added": True, "note": "bei Kaufland · 1,59 €"}
    post = [r for r in kitchenowl.requests if r[0] == "POST"][0]
    assert post[1] == "/api/shoppinglist/7/add-item-by-name" and post[2] == "Bearer test-token"
    assert post[3] == {"name": "Hochland Schmelzkäse", "description": "bei Kaufland · 1,59 €"}
    again = call("add_to_shopping_list", {"item": "hochland schmelzkäse"})
    assert again.structured_content["added"] is False and "schon" in again.content[0].text
    assert len([r for r in kitchenowl.requests if r[0] == "POST"]) == 1


def test_shopping_tool_refuses_plain_http_to_other_hosts(monkeypatch):
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_URL", "http://kitchenowl.example.test")
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_TOKEN", "t")
    monkeypatch.setenv("SUPERMARKT_KITCHENOWL_LIST_ID", "1")
    assert not mcp_server.register_shopping_tool()
    monkeypatch.delenv("SUPERMARKT_KITCHENOWL_URL")
