"""Small synchronous Microsoft Graph client for reading one channel thread.

App-only token (client credentials, cached until expiry), ``@odata.nextLink`` paging, at most one
request per second per channel (N1), ``Retry-After`` on 429, one retry with a fresh token on 401,
5 s per request. ``@odata.nextLink`` must stay on the Graph host (the bearer token is sent to it).
Synchronous on purpose:
callers run it via ``asyncio.to_thread``, so no event-loop-bound state is shared between the gateway
loop and the worker loop Hermes uses for async tools. All failures surface as ``GraphError`` (F8).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from .thread_context import parse_time

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
PAGE_SIZE = 50                 # $top maximum for /replies
MAX_PAGES = 60                 # hard stop: 3000 replies (oldest beyond it are dropped, WARNING logged)
DEFAULT_RETRY_AFTER = 2.0
MAX_RETRY_AFTER = 10.0         # longer waits: give up and answer without history
MAX_ATTEMPTS = 3

# http(method, url, headers, body, timeout) -> (status, headers, body_bytes)
HttpFn = Callable[[str, str, dict, Optional[bytes], float], tuple]


class GraphError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message if status is None else f"{message} (HTTP {status})")
        self.status = status


def urllib_http(method: str, url: str, headers: dict, body: Optional[bytes], timeout: float) -> tuple:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read() or b""


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, *, http: HttpFn = urllib_http,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 min_interval: float = 1.0, timeout: float = 5.0):
        self._tenant_id, self._client_id, self._client_secret = tenant_id, client_id, client_secret
        self._http, self._sleep, self._clock = http, sleep, clock
        self._min_interval, self._timeout = min_interval, timeout
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_expiry = 0.0
        self._last_call: dict[str, float] = {}     # channel id -> clock() of the last Graph request

    # ------------------------------------------------------------ token
    def _access_token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._token_expiry:
                return self._token
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": self._client_id,
            "client_secret": self._client_secret, "scope": SCOPE}).encode()
        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        status, _headers, raw = self._call("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            raise GraphError("token request failed", status)
        try:
            payload = json.loads(raw)
            token, expires_in = payload["access_token"], float(payload.get("expires_in", 3599))
        except Exception as exc:
            raise GraphError(f"token response unreadable: {type(exc).__name__}") from None
        with self._lock:
            self._token, self._token_expiry = token, self._clock() + max(0.0, expires_in - 60)
        return token

    # ------------------------------------------------------------ transport
    def _call(self, method: str, url: str, headers: dict, body: Optional[bytes] = None) -> tuple:
        try:
            return self._http(method, url, headers, body, self._timeout)
        except GraphError:
            raise
        except Exception as exc:  # timeout, DNS, TLS, ...
            raise GraphError(f"request failed: {type(exc).__name__}") from None

    def _pace(self, channel_id: str) -> None:
        with self._lock:
            last = self._last_call.get(channel_id)
            now = self._clock()
            wait = 0.0 if last is None else max(0.0, last + self._min_interval - now)
            self._last_call[channel_id] = now + wait   # reserve the slot before sleeping
        if wait > 0:
            self._sleep(wait)

    def _get(self, url: str, channel_id: str) -> dict:
        refreshed = False
        for _attempt in range(MAX_ATTEMPTS):
            token = self._access_token()
            self._pace(channel_id)
            status, headers, raw = self._call("GET", url, {"Authorization": f"Bearer {token}",
                                                           "Accept": "application/json"})
            if status == 429:
                retry_after = _retry_after(headers)
                if retry_after > MAX_RETRY_AFTER:
                    raise GraphError("throttled, Retry-After too long", 429)
                self._sleep(retry_after)
                continue
            if status == 401:
                with self._lock:
                    self._token = None
                if not refreshed:      # revoked/rotated token: one retry with a fresh one
                    refreshed = True
                    continue
            if not 200 <= status < 300:
                raise GraphError("Graph request failed", status)
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                raise GraphError("Graph response is not JSON", status) from None
        raise GraphError("retries exhausted", status)

    # ------------------------------------------------------------ API
    def fetch_thread(self, team_id: str, channel_id: str, root_id: str, *,
                     after: Optional[datetime] = None) -> list[dict]:
        """Root + replies of one thread (unordered). With ``after`` (delta mode) the root is skipped and
        paging stops at the first page whose messages are all at or before ``after`` (replies come
        newest first)."""
        base = (f"{GRAPH}/teams/{urllib.parse.quote(team_id, safe='')}/channels/"
                f"{urllib.parse.quote(channel_id, safe=':@')}/messages/{urllib.parse.quote(root_id, safe='')}")
        messages: list[dict] = []
        if after is None:
            messages.append(self._get(base, channel_id))
        url: Optional[str] = f"{base}/replies?$top={PAGE_SIZE}"
        for _page in range(MAX_PAGES):
            payload = self._get(url, channel_id)
            page = [m for m in payload.get("value") or [] if isinstance(m, dict)]
            messages.extend(page)
            url = _next_link(payload)
            if not url or (after is not None and page and all(
                    (t := parse_time(m.get("createdDateTime"))) is not None and t <= after for m in page)):
                return messages
        logger.warning("[teams-threads] thread %s has more than %d replies; older ones not read",
                       root_id, MAX_PAGES * PAGE_SIZE)
        return messages


def _next_link(payload: dict) -> Optional[str]:
    """``@odata.nextLink``, only when it points back at Graph; the bearer token must not leave Graph."""
    link = payload.get("@odata.nextLink")
    if not link:
        return None
    if not (isinstance(link, str) and link.startswith(GRAPH + "/")):
        raise GraphError("nextLink outside Microsoft Graph")
    return link


def _retry_after(headers: dict) -> float:
    for name, value in (headers or {}).items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                break
    return DEFAULT_RETRY_AFTER
