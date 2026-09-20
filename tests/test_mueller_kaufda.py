import json
from datetime import date
from types import SimpleNamespace

from supermarkt.models import ToolError
from supermarkt.service import SourceLoader
from supermarkt.sources.kaufda import KaufdaRetailerSource


def _item(title, price, *, publisher="Müller", start="2026-09-14T00:00:00.000+0200", end="2026-09-19T23:59:00.000+0200", brand=""):
    return {
        "type": "OFFER", "id": f"id-{title}", "publisherName": publisher, "title": title, "brand": brand,
        "description": "je 100 g", "prices": {"mainPrice": price}, "validFrom": start, "validUntil": end,
        "offerImages": {"url": {"normal": "https://content-media.bonial.biz/x/main.jpg?impolicy=SEO-offer-normal"}},
    }


def _page(items):
    data = {"props": {"pageProps": {"pageInformation": {"offers": {"main": {"items": items}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def _source():
    return KaufdaRetailerSource(None, "Müller", "Müller", "Mueller")


def test_kaufda_page_becomes_offers_of_the_retailer():
    offers = _source().parse(_page([_item("Oral-B Aufsteckbürsten", 17.95), _item("Haribo Balla Stixx", 1.29, brand="HARIBO")]), date(2026, 9, 17))

    assert [(o.retailer, o.price) for o in offers] == [("Müller", 17.95), ("Müller", 1.29)]
    assert offers[0].source_url == "https://www.kaufda.de/Geschaefte/Mueller"
    assert offers[0].image_url.startswith("https://content-media.bonial.biz/")
    assert offers[0].validity_label
    assert "KaufDA" in offers[0].coverage_note


def test_kaufda_page_keeps_only_this_retailer_valid_offers_with_a_price():
    offers = _source().parse(
        _page([
            _item("Gültig", 1.0),
            _item("Anderer Händler", 1.0, publisher="dm"),
            _item("Abgelaufen", 1.0, start="2026-09-07T00:00:00.000+0200", end="2026-09-12T23:59:00.000+0200"),
            _item("Ohne Preis", 0),
        ]),
        date(2026, 9, 17),
    )
    assert [o.name for o in offers] == ["Gültig"]


def test_kaufda_page_without_structured_data_is_an_error():
    import pytest

    with pytest.raises(ToolError):
        _source().parse("<html>nichts</html>")


class _Fail:
    last_market_url = ""
    last_market_label = ""

    def load(self, *_args, **_kwargs):
        raise ToolError("Müller verlangt eine Browser-Prüfung (Client Challenge)")


def _loader(kaufda, marktguru_offers=()):
    from supermarkt.compare import OfferMapper

    class Marktguru:
        def load_offers(self, _postal):
            return list(marktguru_offers), []

        def load_retailer_queries(self, _postal, _names):
            return list(marktguru_offers), []

    loader = SourceLoader.__new__(SourceLoader)
    loader.aldi_region = SimpleNamespace(last_error="")
    loader.marktguru = Marktguru()
    loader.mapper = OfferMapper()
    loader.official_mueller = _Fail()
    loader.kaufda_mueller = kaufda
    return loader


def test_mueller_falls_back_to_kaufda_when_its_own_site_needs_a_browser(monkeypatch):
    monkeypatch.setattr("supermarkt.common.today_berlin", lambda: date(2026, 9, 17))
    kaufda = _source()
    kaufda.load = lambda target=None: kaufda.parse(_page([_item("Oral-B Aufsteckbürsten", 17.95)]), target)

    result = _loader(kaufda).load("12345", "auto", retailers=("Müller",))

    assert [offer["retailer"] for offer in result["offers"]] == ["Müller"]
    assert result["source_states"]["Müller"] == "KaufDA-Fallback"
    assert any("KaufDA" in warning for warning in result["store_warnings"])
    assert not result.get("challenge_urls")


def test_mueller_without_kaufda_offers_still_asks_marktguru(monkeypatch):
    monkeypatch.setattr("supermarkt.common.today_berlin", lambda: date(2026, 9, 17))
    kaufda = _source()

    def broken(target=None):
        raise ToolError("KaufDA nicht erreichbar")

    kaufda.load = broken
    marktguru = [{
        "id": "m1", "advertisers": [{"name": "Müller", "uniqueName": "mueller"}],
        "validityDates": [{"from": "2026-09-14", "to": "2026-09-19"}],
        "product": {"name": "Testprodukt", "description": "500 g"}, "categories": [{"name": "Drogerie"}], "price": 2.49,
    }]

    result = _loader(kaufda, marktguru).load("12345", "auto", retailers=("Müller",))

    assert result["source_states"]["Müller"] == "Marktguru-Fallback"
    assert any("Müller KaufDA" in error for error in result["request_errors"])


def _viewer_offer(identifier, name, deals, *, brand="", start="2026-09-13T22:00:00.000+0000", end="2026-09-20T21:59:59.000+0000"):
    return {"content": {
        "id": identifier, "type": "offer", "image": "https://content-media.bonial.biz/x/main.jpg",
        "products": [{"name": name, "brandName": brand, "description": [{"paragraph": "versch. Sorten, 175 g"}]}],
        "deals": deals, "publicationProfiles": [{"validity": {"startDate": start, "endDate": end}}],
    }}


def _deal(kind, price, condition=None):
    return {"type": kind, "min": price, "max": price, "conditions": [{"other": condition}] if condition else []}


def test_viewer_uses_the_price_without_app_and_notes_the_app_price():
    data = {"contents": [{"number": 1, "offers": [
        _viewer_offer("a", "Balla Stixx", [_deal("SPECIAL_PRICE", 1.29, "mit der Müller App"), _deal("SALES_PRICE", 1.65, "Ohne App")], brand="HARIBO"),
        _viewer_offer("b", "Zahnbürste", [_deal("SALES_PRICE", 3.99)]),
        _viewer_offer("c", "Nur App-Preis", [_deal("SPECIAL_PRICE", 0.99, "mit der Müller App")]),
        _viewer_offer("d", "Abgelaufen", [_deal("SALES_PRICE", 2.0)], end="2026-09-01T00:00:00.000+0000"),
        _viewer_offer("a", "Doppelt", [_deal("SALES_PRICE", 9.0)]),
    ]}]}
    offers = _source().parse_viewer(data, "1", date(2026, 9, 17))
    assert [(o.name, o.price) for o in offers] == [("HARIBO Balla Stixx", 1.65), ("Zahnbürste", 3.99)]
    assert "mit der Müller App 1,29 €" in offers[0].description and offers[1].image_url.startswith("https://content-media.bonial.biz/")
    assert offers[0].valid_until == "2026-09-20"


def test_viewer_rejects_an_unexpected_answer():
    try:
        _source().parse_viewer({"nope": []}, "1")
    except ToolError:
        return
    raise AssertionError("ToolError erwartet")


def test_load_prefers_the_whole_brochure_and_falls_back_to_the_page(monkeypatch):
    page = _page([_item("Seitenangebot", 1.0)]).replace(
        "</script>", "</script>", 1)
    data = json.loads(page.split('type="application/json">')[1].split("</script>")[0])
    data["props"]["pageProps"]["pageInformation"]["brochures"] = {"viewer": [{"id": 2501262877, "publisher": {"id": "DE-1030", "name": "Müller"}}]}
    page = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'
    viewer = json.dumps({"contents": [{"offers": [_viewer_offer("a", "Prospektangebot", [_deal("SALES_PRICE", 2.5)])]}]})
    requested = []

    class Http:
        def get_bytes(self, url, headers=None):
            requested.append(url)
            if "content-viewer-be" in url:
                return viewer.encode()
            return page.encode()
    source = KaufdaRetailerSource(Http(), "Müller", "Müller", "Mueller", use_viewer=True)
    assert [o.name for o in source.load(date(2026, 9, 17))] == ["Prospektangebot"]
    assert any("/brochures/2501262877/pages?partner=kaufda_web" in url for url in requested)
    viewer = "kaputt"
    assert [o.name for o in source.load(date(2026, 9, 17))] == ["Seitenangebot"]
