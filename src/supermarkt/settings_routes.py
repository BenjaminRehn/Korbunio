from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import kitchenowl
from .security import api_key
from .access import require_admin_auth
from .ui import static_text

router = APIRouter()


def require_settings_access(request: Request) -> None:
    """Mit gesetztem Admin-Schlüssel nur damit; ohne Schlüssel ist der Server offen wie der Rest."""
    if api_key():
        require_admin_auth(request)


class ListsRequest(BaseModel):
    url: str = Field(max_length=300)
    token: str = Field(default="", max_length=4000)


class SaveRequest(ListsRequest):
    list_id: str = Field(pattern=r"^\d{1,12}$")


def _refresh_mcp() -> None:
    try:
        from .mcp_server import register_shopping_tool
    except ImportError:  # MCP nicht installiert oder abgeschaltet
        return
    register_shopping_tool()


def _status() -> dict:
    settings = kitchenowl.load()
    return {
        "configured": settings is not None,
        "url": settings.url if settings else "",
        "list_id": settings.list_id if settings else "",
        "list_label": settings.list_label if settings else "",
        "auth_required": bool(api_key()),
    }


@router.get("/settings", include_in_schema=False, response_class=HTMLResponse)
def settings_page() -> HTMLResponse:
    return HTMLResponse(static_text("settings.html"), headers={"Cache-Control": "no-store"})


@router.get("/api/v1/kitchenowl", include_in_schema=False, dependencies=[Depends(require_settings_access)])
def kitchenowl_status() -> dict:
    return _status()


@router.post("/api/v1/kitchenowl/lists", include_in_schema=False, dependencies=[Depends(require_settings_access)])
def kitchenowl_lists(payload: ListsRequest) -> dict:
    token = payload.token.strip() or (kitchenowl.load().token if kitchenowl.load() else "")
    if not token:
        raise HTTPException(status_code=422, detail="Bitte den KitchenOwl-Token eintragen.")
    try:
        return {"lists": kitchenowl.fetch_lists(payload.url, token)}
    except kitchenowl.KitchenOwlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/api/v1/kitchenowl", include_in_schema=False, dependencies=[Depends(require_settings_access)])
def kitchenowl_save(payload: SaveRequest) -> dict:
    current = kitchenowl.load()
    token = payload.token.strip() or (current.token if current else "")
    if not token:
        raise HTTPException(status_code=422, detail="Bitte den KitchenOwl-Token eintragen.")
    try:
        url = kitchenowl.normalize_url(payload.url)
        lists = {entry["id"]: entry["label"] for entry in kitchenowl.fetch_lists(url, token)}
    except kitchenowl.KitchenOwlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if payload.list_id not in lists:
        raise HTTPException(status_code=422, detail="Diese Liste gibt es bei KitchenOwl nicht.")
    kitchenowl.save(kitchenowl.Settings(url, token, payload.list_id, lists[payload.list_id]))
    _refresh_mcp()
    return _status()


@router.delete("/api/v1/kitchenowl", include_in_schema=False, dependencies=[Depends(require_settings_access)])
def kitchenowl_delete() -> dict:
    kitchenowl.clear()
    _refresh_mcp()
    return _status()
