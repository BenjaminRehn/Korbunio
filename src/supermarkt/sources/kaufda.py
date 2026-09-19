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


class KaufdaRetailerSource:
    """KaufDA's public offer page of one retailer.

    Used as a fallback for a retailer whose own site cannot be read without a
    browser (Müller answers a plain request with a client challenge). The page
    lists the current highlighted offers as structured data; it is a partial
    view of the range, so it never replaces a first-party catalogue that worked.
    """

    BASE = "https://www.kaufda.de/Geschaefte/{slug}"
    MAX_RESPONSE = 3_000_000

    def __init__(self, http: HttpClient, retailer: str, publisher_name: str, slug: str) -> None:
        self.http = http
        self.retailer = retailer
        self.publisher_name = publisher_name
        self.slug = slug

    @property
    def url(self) -> str:
        return self.BASE.format(slug=quote(self.slug, safe="-"))

    def load(self, target: Optional[date] = None) -> list[Offer]:
        payload = self.http.get_bytes(self.url, {"Accept": "text/html", "Accept-Language": "de-DE,de;q=0.9"})
        if len(payload) > self.MAX_RESPONSE:
            raise ToolError(f"KaufDA-{self.retailer}-Seite überschreitet das Größenlimit")
        return self.parse(payload.decode("utf-8", errors="replace"), target)

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
