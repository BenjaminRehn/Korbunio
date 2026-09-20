"""Strict KaufDA single-offer images used only to enrich official Globus data."""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from ..categories import category_decision
from ..common import build_match_key, clean_brand, clean_text, format_validity, normalize_pack, parse_number
from ..config import BERLIN
from ..http import HttpClient
from ..images import is_rejected_image_url, normalize_image_url
from ..models import Offer, ToolError


class KaufdaGlobusImageSource:
    BASE = "https://www.kaufda.de/{city}/Globus/p-r37"
    MAX_RESPONSE = 2_000_000

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def load(self, locality: str) -> list[Offer]:
        city = quote(clean_text(locality), safe="-")
        if not city:
            return []
        url = self.BASE.format(city=city)
        payload = self.http.get_bytes(url, {"Accept": "text/html"})
        if len(payload) > self.MAX_RESPONSE:
            raise ToolError("KaufDA-Globus-Seite überschreitet das Größenlimit")
        return self.parse(payload.decode("utf-8", errors="replace"), url)

    @classmethod
    def parse(cls, page: str, source_url: str) -> list[Offer]:
        match = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            page,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ToolError("KaufDA lieferte keine strukturierten Globus-Bilddaten")
        try:
            payload = json.loads(html.unescape(match.group(1)))
            information = payload["props"]["pageProps"]["pageInformation"]
            items = information["offers"]["main"]["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ToolError("KaufDA-Globus-Bilddaten haben ein unerwartetes Format") from exc
        if not isinstance(items, list):
            raise ToolError("KaufDA-Globus-Angebotsliste hat ein unerwartetes Format")

        result: list[Offer] = []
        for raw in items:
            if not isinstance(raw, dict) or clean_text(raw.get("type")).upper() != "OFFER":
                continue
            if clean_text(raw.get("publisherName")).upper() != "GLOBUS":
                continue
            prices = raw.get("prices") if isinstance(raw.get("prices"), dict) else {}
            price = parse_number(prices.get("mainPrice"))
            name = clean_text(raw.get("title"))
            if not name or price is None or price <= 0:
                continue
            images = raw.get("offerImages") if isinstance(raw.get("offerImages"), dict) else {}
            urls = images.get("url") if isinstance(images.get("url"), dict) else {}
            image_url = normalize_image_url(urls.get("normal") or urls.get("large"))
            parsed = urlsplit(image_url) if image_url else None
            folded = image_url.casefold()
            if (
                not image_url
                or is_rejected_image_url(image_url)
                or not parsed
                or parsed.hostname != "content-media.bonial.biz"
                or "seo-offer" not in folded
                or "seo-brochure" in folded
            ):
                continue
            brand = clean_brand(raw.get("brand"))
            display_name = name if not brand or brand.casefold() in name.casefold() else f"{brand} {name}"
            description = clean_text(raw.get("description"))
            pack = normalize_pack(f"{display_name} {description}")
            start, end = _local_date(raw.get("validFrom")), _local_date(raw.get("validUntil"))
            identifier = clean_text(raw.get("id")) or build_match_key(brand, name, pack, image_url)
            result.append(Offer(
                offer_id=f"kaufda-globus-image:{identifier}", retailer="Globus", category="Bildabgleich",
                name=display_name, brand=brand, description=description, price=price,
                base_price=None, base_unit="", pack_signature=pack,
                validity_label=format_validity(start, end),
                match_key=build_match_key(brand, name, pack, identifier), source_url=source_url,
                image_url=image_url, source_category="KaufDA Einzelangebot",
                valid_from=start.isoformat() if start else None,
                valid_until=end.isoformat() if end else None,
            ))
        return result


def _local_date(value: Any) -> Optional[date]:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(BERLIN)
        return parsed.date()
    except ValueError:
        return None



def _viewer_prices(deals: Any) -> tuple[Optional[float], Optional[float]]:
    """(regulärer Preis, App-Preis) aus den Preisangaben eines Prospektangebots."""
    regular = app = None
    for deal in deals if isinstance(deals, list) else []:
        if not isinstance(deal, dict):
            continue
        price = parse_number(deal.get("min"))
        if price is None or price <= 0:
            continue
        conditions = " ".join(clean_text(value) for c in deal.get("conditions", []) if isinstance(c, dict) for value in c.values()).casefold()
        kind = clean_text(deal.get("type")).upper()
        if kind == "SPECIAL_PRICE" and "app" in conditions and "ohne" not in conditions:
            app = price if app is None else min(app, price)
        elif kind in {"SALES_PRICE", "SPECIAL_PRICE"} and ("app" not in conditions or "ohne app" in conditions):
            regular = price if regular is None else min(regular, price)
    return regular, app


class KaufdaRetailerSource:
    """KaufDA's public offer page of one retailer.

    Used as a fallback for a retailer whose own site cannot be read without a
    browser (Müller answers a plain request with a client challenge). The page
    lists the current highlighted offers as structured data; it is a partial
    view of the range, so it never replaces a first-party catalogue that worked.
    """

    BASE = "https://www.kaufda.de/Geschaefte/{slug}"
    VIEWER_API = "https://content-viewer-be.kaufda.de/v1/brochures/{brochure}/pages"
    # Das Prospekt ist bundesweit gleich; der Viewer verlangt nur irgendeinen Ort (hier die Mitte Deutschlands).
    VIEWER_QUERY = {"partner": "kaufda_web", "lat": "51.16", "lng": "10.45"}
    MAX_RESPONSE = 3_000_000
    MAX_VIEWER_RESPONSE = 6_000_000

    def __init__(self, http: HttpClient, retailer: str, publisher_name: str, slug: str, use_viewer: bool = False) -> None:
        self.http = http
        self.retailer = retailer
        self.publisher_name = publisher_name
        self.slug = slug
        self.use_viewer = use_viewer

    @property
    def url(self) -> str:
        return self.BASE.format(slug=quote(self.slug, safe="-"))

    def load(self, target: Optional[date] = None) -> list[Offer]:
        payload = self.http.get_bytes(self.url, {"Accept": "text/html", "Accept-Language": "de-DE,de;q=0.9"})
        if len(payload) > self.MAX_RESPONSE:
            raise ToolError(f"KaufDA-{self.retailer}-Seite überschreitet das Größenlimit")
        text = payload.decode("utf-8", errors="replace")
        if self.use_viewer:
            # Das ganze Prospekt (deutlich mehr als die Hervorhebungen der Händlerseite); ein Fehler fällt auf die Seite zurück.
            try:
                viewer = self._viewer_offers(text, target)
            except (ToolError, ValueError, KeyError, TypeError):
                viewer = []
            if viewer:
                return viewer
        return self.parse(text, target)

    def _brochure_ids(self, page: str) -> list[str]:
        match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return []
        try:
            brochures = json.loads(html.unescape(match.group(1)))["props"]["pageProps"]["pageInformation"]["brochures"]["viewer"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []
        ids = []
        for entry in brochures if isinstance(brochures, list) else []:
            publisher = entry.get("publisher") if isinstance(entry, dict) and isinstance(entry.get("publisher"), dict) else {}
            name = clean_text(publisher.get("name"))
            if isinstance(entry, dict) and re.fullmatch(r"\d{6,12}", str(entry.get("id", ""))) and (not name or name.casefold() == self.publisher_name.casefold()):
                ids.append(str(entry["id"]))
        return ids[:2]

    def _viewer_offers(self, page: str, target: Optional[date]) -> list[Offer]:
        offers: list[Offer] = []
        for brochure in self._brochure_ids(page):
            url = self.VIEWER_API.format(brochure=brochure) + "?" + "&".join(f"{k}={v}" for k, v in self.VIEWER_QUERY.items())
            payload = self.http.get_bytes(url, {"Accept": "application/json"})
            if len(payload) > self.MAX_VIEWER_RESPONSE:
                raise ToolError(f"KaufDA-Prospekt von {self.retailer} überschreitet das Größenlimit")
            offers.extend(self.parse_viewer(json.loads(payload.decode("utf-8", errors="replace")), brochure, target))
        return offers

    def parse_viewer(self, data: dict[str, Any], brochure: str, target: Optional[date] = None) -> list[Offer]:
        """Angebote aus den Seiten des Prospekts. Regulärer Preis = Preis „ohne App“; ein App-Preis steht nur in der Beschreibung."""
        pages = data.get("contents") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            raise ToolError(f"KaufDA-Prospekt von {self.retailer} hat ein unerwartetes Format")
        result: list[Offer] = []
        seen: set[str] = set()
        for page in pages:
            for entry in page.get("offers", []) if isinstance(page, dict) else []:
                content = entry.get("content") if isinstance(entry, dict) else None
                if not isinstance(content, dict) or content.get("type") != "offer" or not content.get("products"):
                    continue
                identifier = clean_text(content.get("id"))
                if not identifier or identifier in seen:
                    continue
                profile = (content.get("publicationProfiles") or [{}])[0].get("validity", {})
                start, end = _local_date(profile.get("startDate")), _local_date(profile.get("endDate"))
                if target is not None and ((start and target < start) or (end and target > end)):
                    continue
                regular, app = _viewer_prices(content.get("deals"))
                product = content["products"][0]
                title = clean_text(product.get("name"))
                if not title or regular is None or regular <= 0:
                    continue
                seen.add(identifier)
                brand = clean_brand(product.get("brandName"))
                name = title if not brand or brand.casefold() in title.casefold() else f"{brand} {title}"
                description = clean_text(" ".join(clean_text(d.get("paragraph")) for d in product.get("description", []) if isinstance(d, dict)))
                if app is not None and app < regular:
                    description = clean_text(f"{description} · mit der Müller App {format(app, '.2f').replace('.', ',')} €")
                pack = normalize_pack(f"{name} {description}")
                image_url = normalize_image_url(content.get("image"))
                if image_url and (is_rejected_image_url(image_url) or urlsplit(image_url).hostname != "content-media.bonial.biz"):
                    image_url = ""
                decision = category_decision("", self.retailer, name, description, brand)
                result.append(Offer(
                    offer_id=f"kaufda-{self.slug.casefold()}:{identifier}", retailer=self.retailer, category=decision.category,
                    name=name, brand=brand, description=description, price=regular,
                    base_price=None, base_unit="", pack_signature=pack,
                    validity_label=format_validity(start, end),
                    match_key=build_match_key(brand, title, pack, identifier), source_url=self.url,
                    image_url=image_url, source_category=decision.source_category,
                    detected_category=decision.detected_category, category_conflict=decision.category_conflict,
                    valid_from=start.isoformat() if start else None,
                    valid_until=end.isoformat() if end else None,
                    coverage_note="Angebote aus dem Prospekt über KaufDA; die Müller-Seite selbst war nicht lesbar.",
                ))
        return result

    def parse(self, page: str, target: Optional[date] = None) -> list[Offer]:
        match = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            page,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ToolError(f"KaufDA lieferte keine strukturierten {self.retailer}-Angebote")
        try:
            payload = json.loads(html.unescape(match.group(1)))
            items = payload["props"]["pageProps"]["pageInformation"]["offers"]["main"]["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ToolError(f"KaufDA-{self.retailer}-Angebote haben ein unerwartetes Format") from exc
        if not isinstance(items, list):
            raise ToolError(f"KaufDA-{self.retailer}-Angebotsliste hat ein unerwartetes Format")

        result: list[Offer] = []
        for raw in items:
            if not isinstance(raw, dict) or clean_text(raw.get("type")).upper() != "OFFER":
                continue
            if clean_text(raw.get("publisherName")).casefold() != self.publisher_name.casefold():
                continue
            start, end = _local_date(raw.get("validFrom")), _local_date(raw.get("validUntil"))
            if target is not None and ((start and target < start) or (end and target > end)):
                continue
            prices = raw.get("prices") if isinstance(raw.get("prices"), dict) else {}
            price = parse_number(prices.get("mainPrice"))
            title = clean_text(raw.get("title"))
            if not title or price is None or price <= 0:
                continue
            brand = clean_brand(raw.get("brand"))
            name = title if not brand or brand.casefold() in title.casefold() else f"{brand} {title}"
            description = clean_text(raw.get("description"))
            pack = normalize_pack(f"{name} {description}")
            images = raw.get("offerImages") if isinstance(raw.get("offerImages"), dict) else {}
            urls = images.get("url") if isinstance(images.get("url"), dict) else {}
            image_url = normalize_image_url(urls.get("normal") or urls.get("large") or urls.get("thumbnail"))
            if image_url and (is_rejected_image_url(image_url) or urlsplit(image_url).hostname != "content-media.bonial.biz"):
                image_url = ""
            identifier = clean_text(raw.get("id")) or build_match_key(brand, title, pack, "")
            decision = category_decision("", self.retailer, name, description, brand)
            result.append(Offer(
                offer_id=f"kaufda-{self.slug.casefold()}:{identifier}", retailer=self.retailer, category=decision.category,
                name=name, brand=brand, description=description, price=price,
                base_price=None, base_unit="", pack_signature=pack,
                validity_label=format_validity(start, end),
                match_key=build_match_key(brand, title, pack, identifier), source_url=self.url,
                image_url=image_url, source_category=decision.source_category,
                detected_category=decision.detected_category, category_conflict=decision.category_conflict,
                valid_from=start.isoformat() if start else None,
                valid_until=end.isoformat() if end else None,
                coverage_note="Ausschnitt der Angebote über KaufDA; die Müller-Seite selbst war nicht lesbar.",
            ))
        return result
