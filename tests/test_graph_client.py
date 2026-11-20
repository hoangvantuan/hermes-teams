"""Graph client: token cache, paging, delta early stop, per-channel pacing (N1), 429 Retry-After, errors (F8)."""
import json
from datetime import datetime, timezone

import pytest

from teams_threads.graph_client import GraphClient, GraphError

TEAM, CHAN, ROOT = "gid", "19:c@thread.tacv2", "1000"
BASE = "https://graph.microsoft.com/v1.0"


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.now += s


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes          # url-prefix -> list of (status, headers, body-dict)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, timeout))
        if "login.microsoftonline.com" in url:
            return 200, {}, json.dumps({"access_token": "tok", "expires_in": 3600}).encode()
        for prefix, responses in self.routes.items():
            if url == prefix:
                status, hdrs, payload = responses.pop(0) if len(responses) > 1 else responses[0]
                return status, hdrs, json.dumps(payload).encode()
        raise AssertionError(f"unexpected url {url}")


def msg(mid, minute):
    return {"id": mid, "createdDateTime": f"2026-11-20T03:{minute:02d}:00Z"}


def root_url():
    return f"{BASE}/teams/{TEAM}/channels/{CHAN}/messages/{ROOT}"


def replies_url():
    return f"{root_url()}/replies?$top=50"


def make(routes, **kw):
    clock = FakeClock()
    http = FakeHttp(routes)
    client = GraphClient("tenant", "cid", "csecret", http=http, sleep=clock.sleep, clock=clock.clock, **kw)
    return client, http, clock


def test_full_fetch_root_plus_all_reply_pages():
    page2 = "https://graph.microsoft.com/v1.0/next-page-token"
    client, http, _ = make({
        root_url(): [(200, {}, msg(ROOT, 0))],
        replies_url(): [(200, {}, {"value": [msg("1003", 3), msg("1002", 2)], "@odata.nextLink": page2})],
        page2: [(200, {}, {"value": [msg("1001", 1)]})],
    })
    got = client.fetch_thread(TEAM, CHAN, ROOT)
    assert sorted(m["id"] for m in got) == [ROOT, "1001", "1002", "1003"]
    graph_calls = [c for c in http.calls if "graph.microsoft.com" in c[1]]
    assert all(c[2]["Authorization"] == "Bearer tok" for c in graph_calls)
    assert all(c[3] == 5.0 for c in http.calls)                    # per-request 5 s timeout


def test_token_is_cached_across_calls():
    client, http, _ = make({root_url(): [(200, {}, msg(ROOT, 0))],
                            replies_url(): [(200, {}, {"value": []})]})
    client.fetch_thread(TEAM, CHAN, ROOT)
    client.fetch_thread(TEAM, CHAN, ROOT)
    assert sum("login.microsoftonline.com" in c[1] for c in http.calls) == 1


def test_token_request_uses_client_credentials_scope():
    client, http, _ = make({root_url(): [(200, {}, msg(ROOT, 0))],
                            replies_url(): [(200, {}, {"value": []})]})
    client.fetch_thread(TEAM, CHAN, ROOT)
    method, url, _h, _t = http.calls[0]
    assert method == "POST" and url == "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"


def test_delta_skips_root_and_stops_paging_at_watermark():
    page2 = "https://graph.microsoft.com/v1.0/next"
    client, http, _ = make({
        replies_url(): [(200, {}, {"value": [msg("1005", 5), msg("1004", 4)], "@odata.nextLink": page2})],
        page2: [(200, {}, {"value": [msg("1002", 2), msg("1001", 1)], "@odata.nextLink": page2 + "x"})],
    })
    after = datetime(2026, 11, 20, 3, 3, tzinfo=timezone.utc)
    got = client.fetch_thread(TEAM, CHAN, ROOT, after=after)
    assert [m["id"] for m in got] == ["1005", "1004", "1002", "1001"]
    urls = [c[1] for c in http.calls if "graph" in c[1]]
    assert root_url() not in urls and page2 + "x" not in urls


def test_pacing_one_request_per_second_per_channel():
    page2 = "https://graph.microsoft.com/v1.0/next"
    client, _, clock = make({
        root_url(): [(200, {}, msg(ROOT, 0))],
        replies_url(): [(200, {}, {"value": [msg("1002", 2)], "@odata.nextLink": page2})],
        page2: [(200, {}, {"value": [msg("1001", 1)]})],
    })
    client.fetch_thread(TEAM, CHAN, ROOT)
    assert clock.sleeps == [1.0, 1.0]                 # 3 Graph GETs on one channel -> 2 waits


def test_429_waits_retry_after_then_succeeds():
    client, _, clock = make({
        root_url(): [(429, {"Retry-After": "3"}, {}), (200, {}, msg(ROOT, 0))],
        replies_url(): [(200, {}, {"value": []})],
    })
    got = client.fetch_thread(TEAM, CHAN, ROOT)
    assert [m["id"] for m in got] == [ROOT]
    assert 3.0 in clock.sleeps


def test_429_without_retry_after_uses_default_wait():
    client, _, clock = make({
        root_url(): [(429, {}, {}), (200, {}, msg(ROOT, 0))],
        replies_url(): [(200, {}, {"value": []})],
    })
    client.fetch_thread(TEAM, CHAN, ROOT)
    assert 2.0 in clock.sleeps


def test_429_with_too_long_retry_after_gives_up():
    client, _, _ = make({root_url(): [(429, {"Retry-After": "120"}, {})]})
    with pytest.raises(GraphError):
        client.fetch_thread(TEAM, CHAN, ROOT)


def test_403_raises_graph_error_with_status():
    client, _, _ = make({root_url(): [(403, {}, {"error": {"code": "Forbidden"}})]})
    with pytest.raises(GraphError) as exc:
        client.fetch_thread(TEAM, CHAN, ROOT)
    assert exc.value.status == 403


def test_transport_exception_becomes_graph_error():
    def broken(*_a, **_k):
        raise TimeoutError("timed out")

    client = GraphClient("t", "c", "s", http=broken, sleep=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(GraphError):
        client.fetch_thread(TEAM, CHAN, ROOT)
