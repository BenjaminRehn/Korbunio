"""MCP-Server: Korbuino-Angebote für LLMs abfragbar machen (nur Lesen).

Ein KI-Programm fragt zum Beispiel „Wo ist Kaffee im Angebot?“ und bekommt Händler, den
Preis ohne Bonusprogramm und, wenn es einen öffentlich ausgewiesenen gibt, den Preis mit
Bonusprogramm, dazu Bilder. Der Server bindet ihn unter ``/mcp`` ein (Streamable HTTP);
``python -m supermarkt.mcp_server`` startet ihn für lokale Programme über stdio.

Die Wartezeit ist der schwierige Teil: Für eine neue Postleitzahl lädt der Server alle
Händler, das dauert meist 10 bis 20 Sekunden, bei einer langsamen Quelle länger. Deshalb

* wird die Standard-Postleitzahl (und zuletzt gefragte) im Hintergrund frisch gehalten,
* läuft ein Laden im Hintergrund weiter, auch wenn die Frage abgebrochen wird,
* melden lange Abfragen ihren Fortschritt, und
* bekommt das Programm nach der Frist eine freundliche „gleich noch einmal fragen“-Antwort
  statt eines Zeitüberschreitungsfehlers.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import time
from typing import Any, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from . import runtime
from .common import validate_postal_code
from .images import ImageServiceError
from .loyalty import PROGRAMS
from .models import RETAILER_SPECS, ToolError, resolve_retailer_names

log = logging.getLogger(__name__)

# So lange wartet eine Frage auf das Laden, bevor sie um Geduld bittet.
LOAD_DEADLINE_SECONDS = float(os.environ.get("SUPERMARKT_MCP_DEADLINE_SECONDS", "45"))
MAX_IMAGES = 3
# Zwischenspeicher: Ergebnisse dieser PLZ werden alle 25 Minuten frisch gehalten, solange gefragt wird.
WARM_INTERVAL_SECONDS = 25 * 60
WARM_WHILE_USED_SECONDS = 6 * 3600
MAX_WARM_POSTAL_CODES = 3

INSTRUCTIONS = (
    "Aktuelle Supermarkt-Angebote in Deutschland. Mit find_offers fragst du, wo ein Produkt gerade "
    "im Angebot ist: Die Antwort nennt Händler und Preis ohne Bonusprogramm (Kundenkarte oder App) und, wo "
    "es einen öffentlich ausgewiesenen Vorteil gibt, den Preis mit Bonusprogramm. Bei manchen Händlern "
    "(zum Beispiel EDEKA, Globus, PAYBACK, Rossmann, Müller) gibt es keinen berechenbaren Bonuspreis; das ist "
    "dann keine Aussage, dass es keinen Vorteil gibt. Preise gelten für die genannte Postleitzahl. Suche mit "
    "dem Produktnamen und, wenn nötig, Synonymen in also_search; die Suche ist eine Textsuche."
)


class Offer(BaseModel):
    retailer: str = Field(description="Händler, z. B. Kaufland")
    product: str
    pack: str = Field(default="", description="Packungsgröße")
    unit_price: str = Field(default="", description="Grundpreis, z. B. 8,95 €/kg")
    price_without_bonus: str = Field(description="Preis ohne Bonusprogramm")
    price_with_bonus: Optional[str] = Field(default=None, description="Preis mit Bonusprogramm, nur wenn es einen gibt")
    bonus_program: Optional[str] = Field(default=None, description="Welches Programm dafür nötig ist, z. B. Kaufland Card XTRA")
    valid: str = Field(default="", description="Gültigkeit")
    image_url: Optional[str] = Field(default=None, description="Bild des Angebots (Adresse beim Händler)")


class OfferResult(BaseModel):
    postal_code: str
    query: str
    found: int = Field(description="Anzahl Treffer insgesamt")
    offers: list[Offer] = Field(description="Die günstigsten Treffer zuerst")


class StillLoading(Exception):
    """Die Angebote dieser Postleitzahl werden noch geladen."""


mcp = MCPServer("korbuino", instructions=INSTRUCTIONS)

_inflight: dict[tuple, asyncio.Task] = {}
_last_used: dict[str, float] = {}
_warm_started = False


# ---- laden -------------------------------------------------------------------------------


def _postal_code(value: str) -> str:
    plz = validate_postal_code((value or os.environ.get("SUPERMARKT_DEFAULT_POSTAL_CODE", "")).strip())
    if not plz:
        raise ValueError("Bitte eine gültige deutsche Postleitzahl (fünf Ziffern) angeben.")
    return plz


def _retailers(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    resolved, unknown = resolve_retailer_names(values)
    if unknown:
        raise ValueError("Unbekannte Händler: " + ", ".join(unknown) + ". Gültig: " + ", ".join(spec.name for spec in RETAILER_SPECS))
    return tuple(resolved)


def _load_snapshot(plz: str, retailers: tuple[str, ...], refresh: bool = False) -> dict[str, Any]:
    snapshot, _from_cache = runtime.get_engine().snapshot(plz, "auto", refresh, retailers=retailers)
    return snapshot


async def _snapshot(plz: str, retailers: tuple[str, ...], ctx: Context | None = None) -> dict[str, Any]:
    """Angebote laden, mit Frist. Ein begonnenes Laden läuft weiter und wird gemeinsam genutzt."""
    _last_used[plz] = time.time()
    _start_warmup()
    key = (plz, retailers)
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(asyncio.to_thread(_load_snapshot, plz, retailers))
        _inflight[key] = task
        task.add_done_callback(lambda finished, key=key: _inflight.pop(key, None) if _inflight.get(key) is finished else None)
    waited = 0.0
    step = 3.0
    while not task.done():
        if waited >= LOAD_DEADLINE_SECONDS:
            raise StillLoading
        await asyncio.wait({task}, timeout=step)
        waited += step
        if ctx is not None and not task.done():
            with contextlib.suppress(Exception):
                await ctx.report_progress(min(waited, LOAD_DEADLINE_SECONDS), LOAD_DEADLINE_SECONDS, "Angebote werden geladen …")
    try:
        return task.result()
    except ToolError as exc:
        raise ValueError(f"Die Angebote konnten nicht geladen werden: {exc}") from exc


def _start_warmup() -> None:
    """Hält die zuletzt gefragten Postleitzahlen frisch, solange der MCP benutzt wird."""
    global _warm_started
    if _warm_started or os.environ.get("SUPERMARKT_MCP_WARMUP", "1") == "0":
        return
    with contextlib.suppress(RuntimeError):
        asyncio.get_running_loop().create_task(_warm_loop())
        _warm_started = True


async def _warm_loop() -> None:
    default = validate_postal_code(os.environ.get("SUPERMARKT_DEFAULT_POSTAL_CODE", "")) or ""
    if default:
        _last_used.setdefault(default, time.time())
    while True:
        await asyncio.sleep(WARM_INTERVAL_SECONDS)
        now = time.time()
        recent = sorted((p for p, used in _last_used.items() if now - used < WARM_WHILE_USED_SECONDS), key=lambda p: -_last_used[p])
        for plz in recent[:MAX_WARM_POSTAL_CODES]:
            try:
                await asyncio.to_thread(_load_snapshot, plz, (), True)
            except Exception:  # noqa: BLE001 - Vorwärmen darf nie stören
                log.warning("Vorwärmen für %s fehlgeschlagen", plz, exc_info=True)


# ---- Werkzeuge ---------------------------------------------------------------------------


def _euro(text: str) -> float:
    try:
        return float(text.replace("€", "").replace(".", "").replace(",", ".").strip())
    except ValueError:
        return float("inf")


def _offers_from(snapshot: dict[str, Any], product: str, also_search: list[str] | None, retailers: tuple[str, ...]) -> list[Offer]:
    engine = runtime.get_engine()
    common = {"filter_text": product, "keywords": tuple(also_search or ()), "page": 1, "page_size": 100, "view": "all", "sort": "price", "include_image_urls": True}
    plain = engine.page(snapshot, loyalty_programs=(), **common)
    programs = tuple(p["id"] for p in plain.get("available_loyalty_programs", []) if p.get("priced_offer_count"))
    with_bonus = engine.page(snapshot, loyalty_programs=programs, **common) if programs else plain
    bonus_by_id = {offer["offer_id"]: offer for offer in with_bonus.get("offers", [])}
    result: list[Offer] = []
    for item in plain.get("offers", []):
        other = bonus_by_id.get(item["offer_id"], item)
        cheaper = (
            other.get("effective_price") is not None
            and item.get("effective_price") is not None
            and other["effective_price"] < item["effective_price"]
        )
        result.append(Offer(
            retailer=item["retailer"], product=item["product"], pack=item.get("pack", ""), unit_price=item.get("unit_price", ""),
            price_without_bonus=item["regular_price_text"],
            price_with_bonus=other["effective_price_text"] if cheaper else None,
            bonus_program=(other.get("loyalty_benefit") or None) if cheaper else None,
            valid=item.get("validity", ""), image_url=item.get("image_url") or None,
        ))
    result.sort(key=lambda offer: _euro(offer.price_with_bonus or offer.price_without_bonus))
    return result


def _image_block(offer: Offer) -> ImageContent | None:
    """Bild des Angebots über den Bilddienst des Servers (Adressprüfung, Zwischenspeicher, Größenlimit)."""
    if not offer.image_url:
        return None
    try:
        image = runtime.get_image_service().get(source_url=offer.image_url, product=offer.product, retailer=offer.retailer)
    except (ImageServiceError, ToolError, OSError, ValueError):
        return None
    return ImageContent(type="image", data=base64.b64encode(image.data).decode("ascii"), mime_type=image.content_type)


def _valid_text(offer: Offer) -> str:
    """Gültigkeit lesbar: deutsche Daten, ohne vorangestellten Händlernamen."""
    text = re.sub(r"(\d{4})-(\d{2})-(\d{2})", r"\3.\2.\1", offer.valid)
    prefix = offer.retailer + ", "
    return text[len(prefix):] if text.startswith(prefix) else text


def _summary(result: OfferResult) -> str:
    lines = [f"{result.found} Treffer für „{result.query}“ (PLZ {result.postal_code}), günstigste zuerst:"]
    for offer in result.offers:
        line = f"- {offer.retailer}: {offer.product} {offer.pack}".rstrip() + f" – {offer.price_without_bonus} ohne Bonus"
        if offer.price_with_bonus:
            line += f", {offer.price_with_bonus} mit {offer.bonus_program or 'Bonusprogramm'}"
        if offer.unit_price:
            line += f" ({offer.unit_price})"
        valid = _valid_text(offer)
        if valid:
            # Manche Quellen schreiben schon "gültig ..." in die Angabe.
            line += ", " + (valid if "gültig" in valid.casefold() else f"gültig {valid}")
        lines.append(line)
    return "\n".join(lines)


def _waiting_result(plz: str, query: str) -> CallToolResult:
    text = (
        f"Die Angebote für die Postleitzahl {plz} werden gerade zum ersten Mal geladen. Das läuft im Hintergrund weiter "
        "und dauert höchstens eine Minute. Bitte frage in etwa 30 Sekunden noch einmal genau dasselbe, dann kommt die Antwort sofort."
    )
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content={"status": "loading", "postal_code": plz, "query": query})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def find_offers(
    product: str,
    postal_code: str = "",
    retailers: list[str] | None = None,
    also_search: list[str] | None = None,
    limit: int = 8,
    with_images: bool = True,
    ctx: Context | None = None,
) -> CallToolResult:
    """Wo ist ein Produkt gerade im Angebot? Liefert Händler und Preis ohne und, falls vorhanden, mit Bonusprogramm.

    Args:
        product: Suchbegriff, z. B. "Kaffee" oder "Hochland Schmelzkäse".
        postal_code: Deutsche Postleitzahl (fünf Ziffern). Leer = Standard des Servers.
        retailers: Nur diese Händler, z. B. ["Kaufland", "REWE"]. Leer = alle. Gültige Namen liefert list_retailers.
        also_search: Weitere Suchbegriffe für dieselbe Frage (ODER), z. B. Synonyme oder Schreibweisen.
        limit: Höchstens so viele Treffer, die günstigsten zuerst (1 bis 25).
        with_images: Bilder der ersten drei Treffer mitschicken.
    """
    if not product.strip():
        raise ValueError("Bitte sage, welches Produkt gesucht wird.")
    plz = _postal_code(postal_code)
    wanted = _retailers(retailers)
    limit = max(1, min(int(limit), 25))
    try:
        snapshot = await _snapshot(plz, wanted, ctx)
    except StillLoading:
        return _waiting_result(plz, product)
    offers = await asyncio.to_thread(_offers_from, snapshot, product.strip(), also_search, wanted)
    result = OfferResult(postal_code=plz, query=product.strip(), found=len(offers), offers=offers[:limit])
    if not result.offers:
        text = f"Keine Angebote für „{result.query}“ bei der Postleitzahl {plz} gefunden. Versuche einen anderen Begriff oder Synonyme (also_search)."
        return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=result.model_dump())
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=_summary(result))]
    if with_images:
        shown = [offer for offer in result.offers if offer.image_url][:MAX_IMAGES]
        blocks = await asyncio.gather(*(asyncio.to_thread(_image_block, offer) for offer in shown))
        for offer, block in zip(shown, blocks, strict=True):
            if block is not None:
                content.append(TextContent(type="text", text=f"Bild: {offer.retailer} – {offer.product}"))
                content.append(block)
    return CallToolResult(content=content, structured_content=result.model_dump())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_retailers() -> list[dict[str, Any]]:
    """Welche Händler kennt Korbuino, und welches Bonusprogramm gehört zu welchem?"""
    programs: dict[str, list[str]] = {}
    for program in PROGRAMS:
        for retailer in program.retailers:
            programs.setdefault(retailer, []).append(program.label)
    return [{"name": spec.name, "bonus_programs": programs.get(spec.name, [])} for spec in RETAILER_SPECS]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def list_bonus_programs(postal_code: str = "", ctx: Context | None = None) -> CallToolResult:
    """Welche Bonusprogramme (Kundenkarten oder Apps) berücksichtigt Korbuino, und für wie viele Angebote gibt es einen Preis?"""
    plz = _postal_code(postal_code)
    try:
        snapshot = await _snapshot(plz, (), ctx)
    except StillLoading:
        return _waiting_result(plz, "")
    page = await asyncio.to_thread(
        runtime.get_engine().page, snapshot, page=1, page_size=1, view="best_only", sort="price", loyalty_programs=(),
    )
    programs = [
        {"id": p["id"], "name": p["label"], "retailers": p["retailers"], "offers_with_price": p["priced_offer_count"], "note": p.get("note", "")}
        for p in page.get("available_loyalty_programs", [])
    ]
    text = "\n".join(f"- {p['name']} ({', '.join(p['retailers'])}): {p['offers_with_price']} Angebote mit Preis" for p in programs)
    return CallToolResult(content=[TextContent(type="text", text=text or "Keine Bonusprogramme.")], structured_content={"programs": programs})


def main() -> None:  # pragma: no cover - Einstieg für stdio
    mcp.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
