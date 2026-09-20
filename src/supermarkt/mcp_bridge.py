"""Stdio-Adapter für KI-Programme, die MCP nur über stdio sprechen, zu einem entfernten Korbuino-Server.

    python -m supermarkt.mcp_bridge https://dein-server/mcp

Ein Schlüssel (falls der Server einen verlangt) kommt aus der Umgebung: KORBUINO_MCP_KEY.
Der Server arbeitet ohne Sitzung, deshalb genügt es, jede Zeile einzeln weiterzureichen.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

Post = Callable[[str, bytes], tuple[str, bytes]]


def _post(url: str, body: bytes) -> tuple[str, bytes]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key := os.environ.get("KORBUINO_MCP_KEY", "").strip():
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    # Nur http(s): main() prüft die Adresse.
    with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
        return response.headers.get("Content-Type", ""), response.read()


def messages(content_type: str, payload: bytes) -> list[str]:
    """JSON-RPC-Nachrichten aus einer JSON- oder Event-Stream-Antwort."""
    text = payload.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        return [line[5:].strip() for line in text.splitlines() if line.startswith("data:") and line[5:].strip()]
    return [text.strip()] if text.strip() else []


def forward(url: str, line: str, post: Post = _post) -> list[str]:
    try:
        return messages(*post(url, line.encode("utf-8")))
    except urllib.error.HTTPError as exc:
        request_id = None
        with_id = False
        try:
            parsed = json.loads(line)
            request_id, with_id = parsed.get("id"), "id" in parsed
        except (ValueError, AttributeError):
            pass
        if not with_id:
            return []
        return [json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": f"Server antwortete mit HTTP {exc.code}"}})]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - Einstieg für stdio
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].startswith(("https://", "http://")):
        print("Aufruf: python -m supermarkt.mcp_bridge https://dein-server/mcp", file=sys.stderr)
        return 2
    for line in sys.stdin:
        if not line.strip():
            continue
        for message in forward(args[0], line):
            sys.stdout.write(message + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
