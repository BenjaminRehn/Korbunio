import contextlib
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse
from starlette.routing import Route

from .security import api_auth_configured, valid_api_key
from .ui import STATIC_DIR
from .version import __version__
from .web import router

log = logging.getLogger(__name__)


def _mcp_app():
    """ASGI-App des MCP-Servers (Streamable HTTP) oder None, wenn abgeschaltet (SUPERMARKT_MCP=0)."""
    if os.environ.get("SUPERMARKT_MCP", "1") == "0":
        return None
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        from .mcp_server import mcp
    except ImportError:  # pragma: no cover - mcp nicht installiert
        log.warning("MCP-Paket fehlt, /mcp ist aus")
        return None
    # Der Server steht hinter beliebigen Adressen (auch hinter einem Reverse-Proxy); der Schutz gegen
    # DNS-Rebinding gilt Programmen, die nur auf localhost lauschen. Der Zugriff selbst hängt am API-Schlüssel.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, transport_security=security)


_mcp = _mcp_app()


class _McpEndpoint:
    """Reicht /mcp an den MCP-Server weiter, mit dem API-Schlüssel des Servers, falls einer gesetzt ist."""

    async def __call__(self, scope, receive, send) -> None:
        if api_auth_configured():
            headers = {key.decode("latin-1").casefold(): value.decode("latin-1") for key, value in scope["headers"]}
            scheme, _, credential = headers.get("authorization", "").partition(" ")
            if scheme.casefold() != "bearer" or not valid_api_key(credential):
                await JSONResponse({"detail": "Ungültiger Bearer-Token"}, status_code=401)(scope, receive, send)
                return
        await _mcp(scope, receive, send)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    if _mcp is None:
        yield
        return
    from .mcp_server import start_background
    start_background()
    async with _mcp.router.lifespan_context(_mcp):
        yield


app = FastAPI(title="Korbuino", version=__version__, lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)
if _mcp is not None:
    app.router.routes.append(Route("/mcp", _McpEndpoint(), methods=["GET", "POST", "DELETE"]))
