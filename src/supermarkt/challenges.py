"""Short-lived, explicit browser-session handoffs for protected retailers."""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit

_CURL_URL = re.compile(r"""curl\s+(?:-\S+\s+)*(['"])(https?://[^'"]+)\1""", re.IGNORECASE)
_CURL_HEADER = re.compile(r"""(?:-H|--header)\s+(['"])\s*cookie\s*:\s*(.*?)\1""", re.IGNORECASE)
_CURL_COOKIE = re.compile(r"""(?:-b|--cookie)\s+(['"])(.*?)\1""", re.IGNORECASE)
_HEADER_LINE = re.compile(r"^\s*cookie\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def extract_cookie_header(text: str) -> str:
    """Return the cookie header out of what the user pasted.

    Accepts the bare header value, a "Cookie: ..." line, a block of request
    headers, or a request copied as cURL from the browser's network tab.
    Nothing but the cookie is taken over, so other headers of a copied request
    (authorization, user agent, ...) are dropped here. A copied cURL request
    must have gone to mueller.de, otherwise a cookie of another site could end
    up being sent to Müller.
    """
    raw = str(text or "")
    if "\n" not in raw.strip() and "curl" not in raw[:8].lower():
        return raw.strip()
    url = _CURL_URL.search(raw)
    if url:
        host = (urlsplit(url.group(2)).hostname or "").lower()
        if host != "mueller.de" and not host.endswith(".mueller.de"):
            raise ValueError("Die kopierte Anfrage ging nicht an mueller.de")
    for pattern in (_CURL_HEADER, _CURL_COOKIE):
        found = pattern.search(raw)
        if found:
            return found.group(2).strip()
    found = _HEADER_LINE.search(raw)
    if found:
        return found.group(1).strip()
    if url or "\n" in raw.strip():
        raise ValueError("In der eingefügten Anfrage steht kein Cookie")
    return raw.strip()


class MuellerSessionStore:
    """Keep one operator-provided Müller cookie only in process memory.

    The value is deliberately never persisted, logged, or returned. A
    self-hosted single-process deployment is the intended use; expiry keeps a
    copied browser session from remaining active indefinitely.
    """

    MAX_COOKIE_LENGTH = 8_192

    def __init__(self, ttl_seconds: int = 3_600) -> None:
        self.ttl_seconds = max(300, int(ttl_seconds))
        self._cookie = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def set(self, cookie: str) -> None:
        value = extract_cookie_header(cookie)
        if not value or len(value) > self.MAX_COOKIE_LENGTH or any(char in value for char in "\r\n"):
            raise ValueError("Ungültige Müller-Session")
        # Browsers show the header as "cookie: a=b"; a copied name is not part of the value.
        if value[:7].lower() == "cookie:":
            value = value[7:].strip()
        # A Cookie header consists of semicolon-separated name/value pairs.
        # Reject arbitrary pasted text so the value cannot become a header
        # injection or an accidental password/token submission.
        parts = [part.strip() for part in value.split(";") if part.strip()]
        if not parts or any("=" not in part or not part.split("=", 1)[0].strip() for part in parts):
            raise ValueError("Bitte den Cookie-Header aus der Müller-Domain einfügen")
        with self._lock:
            self._cookie = "; ".join(parts)
            self._expires_at = time.monotonic() + self.ttl_seconds

    def get(self) -> str:
        with self._lock:
            if not self._cookie or time.monotonic() >= self._expires_at:
                self._cookie = ""
                self._expires_at = 0.0
                return ""
            return self._cookie

    def clear(self) -> None:
        with self._lock:
            self._cookie = ""
            self._expires_at = 0.0


_MUELLER_SESSION = MuellerSessionStore()


def get_mueller_cookie() -> str:
    return _MUELLER_SESSION.get()


def set_mueller_cookie(value: str) -> None:
    _MUELLER_SESSION.set(value)


def clear_mueller_cookie() -> None:
    _MUELLER_SESSION.clear()
