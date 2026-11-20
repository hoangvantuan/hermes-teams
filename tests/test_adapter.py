"""Adapter glue: registration, hydration flow (F3-F8), watermark persistence (F6), gates, tool (F12a)."""
import asyncio
import json
import logging
import sys
import types
from datetime import datetime, timezone

import pytest

import teams_threads as plugin
from teams_threads.graph_client import GraphError

TEAM, CHAN, ROOT = "gid", "19:c@thread.tacv2", "1000"
CONV = f"{CHAN};messageid={ROOT}"


def gm(mid, minute, text="hi", app=None):
    sender = ({"user": None, "application": {"id": app, "displayName": "Conan"}} if app
              else {"user": {"displayName": "Tuấn"}, "application": None})
    return {"id": mid, "messageType": "message", "createdDateTime": f"2026-11-20T03:{minute:02d}:00Z",
            "deletedDateTime": None, "from": sender, "body": {"contentType": "html", "content": text},
            "attachments": []}


class FakeGraph:
    def __init__(self, messages=None, error=None):
        self.messages = messages or []
        self.error = error
        self.calls = []

    def fetch_thread(self, team_id, channel_id, root_id, *, after=None):
        self.calls.append((team_id, channel_id, root_id, after))
        if self.error:
            raise self.error
        return [m for m in self.messages if after is None or
                datetime.fromisoformat(m["createdDateTime"].replace("Z", "+00:00")) > after]


class FakeStore:
    def __init__(self, existing=(), prompt_tokens=None):
        import threading
        self.entries = {k: {} for k in existing}
        self._lock = threading.RLock()
        self._entries = {k: types.SimpleNamespace(last_prompt_tokens=v) for k, v in (prompt_tokens or {}).items()}

    def get_session_metadata(self, key, name, default=None):
        return self.entries.get(key, {}).get(name, default)

    def set_session_metadata(self, key, name, value):
        if key not in self.entries:
            return False
        self.entries[key][name] = value
        return True


def activity(mid, *, conv=CONV, conv_type="channel", reply_chain=ROOT, minute=9):
    return types.SimpleNamespace(
        id=mid, conversation=types.SimpleNamespace(id=conv, conversation_type=conv_type),
        channel_data=types.SimpleNamespace(
            model_extra={"replyChain": {"id": reply_chain}} if reply_chain else {},
            team=types.SimpleNamespace(aad_group_id=TEAM), channel=types.SimpleNamespace(id=CHAN)),
        timestamp=datetime(2026, 11, 20, 3, minute, tzinfo=timezone.utc))


def event(text="hỏi gì đó", conv=CONV, chat_type="group"):
    return types.SimpleNamespace(text=text, channel_context=None, media_urls=[],
                                 source=types.SimpleNamespace(chat_id=conv, chat_type=chat_type, user_id="u1"))


def make_adapter(graph, store=None):
    adapter = plugin.ThreadAwareTeamsAdapter(types.SimpleNamespace(extra={}))
    adapter._tt_graph = graph
    adapter._session_store = store if store is not None else FakeStore()
    adapter._tt_flush_delays = (0, 0)            # deferred watermark flush retries, no real waiting

    async def summarize(_messages):
        return "SUMMARY"
    adapter._tt_summarize = summarize
    return adapter


async def deliver(adapter, act, ev):
    await adapter._on_message(types.SimpleNamespace(activity=act, event=ev))
    await adapter._tt_drain()
    return ev


def key():
    return "agent:main:teams:channel:" + CONV


# ---------- registration ----------

def test_register_swaps_factory_keeps_other_args_and_adds_tool():
    calls = {}

    class Ctx:
        def register_platform(self, **kw):
            calls["platform"] = kw

        def register_tool(self, **kw):
            calls["tool"] = kw

    plugin.register(Ctx())
    assert isinstance(calls["platform"]["adapter_factory"](None), plugin.ThreadAwareTeamsAdapter)
    assert calls["platform"]["check_fn"] == "base-check"
    assert calls["platform"]["ensure_deps_fn"] == "base-ensure"
    tool = calls["tool"]
    assert tool["name"] == "teams_thread_read" and tool["toolset"] == "teams"
    assert tool["schema"]["name"] == "teams_thread_read"


# ---------- hydration ----------

def test_first_mention_fetches_full_thread_into_channel_context():
    graph = FakeGraph([gm(ROOT, 0, "có thấy tin này không?"), gm("1001", 1, "chào anh bằng 3 thứ tiếng"),
                       gm("1002", 2, "<at>Conan</at>")])
    adapter = make_adapter(graph, FakeStore(existing=[key()]))
    ev = asyncio.run(deliver(adapter, activity("1002", minute=2), event("")))
    assert graph.calls == [(TEAM, CHAN, ROOT, None)]
    assert adapter.handled == [ev]
    assert "chào anh bằng 3 thứ tiếng" in ev.channel_context
    assert "<at>" not in ev.channel_context and "1002" not in ev.channel_context
    assert ev.text == "(được gọi trong thread, không kèm nội dung)"          # F3
    stored = adapter._session_store.entries[key()][plugin.WATERMARK_KEY]
    assert stored.startswith("2026-11-20T03:02")


def test_second_mention_reads_only_delta_after_watermark():
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a"), gm("1003", 3, "b mới")])
    store = FakeStore(existing=[key()])
    store.entries[key()][plugin.WATERMARK_KEY] = "2026-11-20T03:02:00+00:00"
    adapter = make_adapter(graph, store)
    ev = asyncio.run(deliver(adapter, activity("1004", minute=4), event()))
    assert graph.calls[0][3] == datetime(2026, 11, 20, 3, 2, tzinfo=timezone.utc)
    assert "b mới" in ev.channel_context and "root" not in ev.channel_context
    assert store.entries[key()][plugin.WATERMARK_KEY].startswith("2026-11-20T03:04")   # covers trigger


def test_watermark_written_after_session_appears_and_used_next_time():
    """First turn: the runner creates the session entry only after dispatch -> deferred flush."""
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a")])
    store = FakeStore()
    adapter = make_adapter(graph, store)

    async def runner_creates_entry(ev):
        store.entries.setdefault(key(), {})
        await plugin.base.TeamsAdapter.handle_message(adapter, ev)

    adapter._tt_dispatch = runner_creates_entry
    asyncio.run(deliver(adapter, activity("1001", minute=1), event()))
    assert store.entries[key()][plugin.WATERMARK_KEY].startswith("2026-11-20T03:01")
    assert adapter._tt_pending == {}


def test_pending_watermark_used_when_store_write_failed():
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a")])
    adapter = make_adapter(graph, FakeStore())          # entry never appears
    asyncio.run(deliver(adapter, activity("1001", minute=1), event()))
    graph.messages.append(gm("1002", 2, "new"))
    ev = asyncio.run(deliver(adapter, activity("1003", minute=3), event()))
    assert graph.calls[1][3] is not None                # delta, not a second full fetch
    assert "new" in ev.channel_context and "root" not in ev.channel_context


def test_reset_session_without_watermark_refetches_full_thread():
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a")])
    adapter = make_adapter(graph, FakeStore(existing=[key()]))   # fresh entry, empty metadata (/new)
    ev = asyncio.run(deliver(adapter, activity("1002", minute=2), event()))
    assert graph.calls[0][3] is None and "root" in ev.channel_context


def test_graph_error_still_dispatches_without_context(caplog):
    adapter = make_adapter(FakeGraph(error=GraphError("forbidden", status=403)))
    with caplog.at_level(logging.WARNING):
        ev = asyncio.run(deliver(adapter, activity("1002"), event()))
    assert adapter.handled == [ev] and ev.channel_context is None
    assert any(r.levelno == logging.WARNING and "403" in r.getMessage() for r in caplog.records)
    assert "hỏi gì đó" not in caplog.text                            # N3: no message text in logs


@pytest.mark.parametrize("conv_type,chat_type", [("personal", "dm"), ("groupChat", "group")])
def test_dm_and_group_chat_untouched(conv_type, chat_type):
    graph = FakeGraph([gm(ROOT, 0)])
    adapter = make_adapter(graph)
    ev = asyncio.run(deliver(adapter, activity("1", conv="a:1", conv_type=conv_type), event("", "a:1", chat_type)))
    assert graph.calls == [] and adapter.handled == [ev]
    assert ev.channel_context is None and ev.text == ""


def test_unauthorized_sender_skips_graph():
    graph = FakeGraph([gm(ROOT, 0)])
    adapter = make_adapter(graph)
    adapter.authorized = None
    ev = asyncio.run(deliver(adapter, activity("1002"), event()))
    assert graph.calls == [] and adapter.handled == [ev]


def test_slash_command_skips_graph():
    graph = FakeGraph([gm(ROOT, 0)])
    adapter = make_adapter(graph)
    ev = asyncio.run(deliver(adapter, activity("1002"), event("/new")))
    assert graph.calls == [] and adapter.handled == [ev] and ev.text == "/new"


def test_root_id_from_conversation_when_reply_chain_missing():
    graph = FakeGraph([gm(ROOT, 0, "root")])
    adapter = make_adapter(graph)
    asyncio.run(deliver(adapter, activity("1002", reply_chain=None), event()))
    assert graph.calls[0][2] == ROOT


def test_summarizer_used_when_over_budget(monkeypatch):
    msgs = [gm(ROOT, 0, "root")] + [gm(str(1100 + i), i % 60, "y" * 400) for i in range(1, 59)]
    adapter = make_adapter(FakeGraph(msgs), FakeStore(existing=[key()]))
    monkeypatch.setattr(plugin, "_compression_settings", lambda: (0.20, 0.50))
    adapter._message_handler = FakeRunner(ctx_len=10_000).handle      # limit 2000, tail 1000 -> budget 1000
    ev = asyncio.run(deliver(adapter, activity("1999", minute=59), event()))
    assert "SUMMARY" in ev.channel_context


# ---------- F12a tool ----------

def _session(monkeypatch, platform="teams", chat_id=CONV):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", platform)
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", chat_id)


def test_tool_reads_current_thread_only(monkeypatch):
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "chi tiết 42"), gm("1002", 2, "khác", app="app-id")])
    adapter = make_adapter(graph, FakeStore(existing=[key()]))
    asyncio.run(deliver(adapter, activity("1003", minute=3), event()))   # learns team id for this thread
    _session(monkeypatch)
    out = json.loads(asyncio.run(plugin.teams_thread_read({"query": "chi tiết", "team_id": "evil"})))
    assert out["count"] == 1 and "chi tiết 42" in out["messages"]
    assert graph.calls[-1][:3] == (TEAM, CHAN, ROOT)


def test_tool_includes_bot_messages_and_time_range(monkeypatch):
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a"), gm("1002", 2, "bot nói", app="app-id")])
    adapter = make_adapter(graph, FakeStore(existing=[key()]))
    asyncio.run(deliver(adapter, activity("1003", minute=3), event()))
    _session(monkeypatch)
    out = json.loads(asyncio.run(plugin.teams_thread_read({"since": "2026-11-20T03:01:30Z"})))
    assert out["count"] == 1 and "bot nói" in out["messages"]


def test_tool_refuses_outside_teams_thread(monkeypatch):
    _session(monkeypatch, platform="slack")
    assert "error" in json.loads(asyncio.run(plugin.teams_thread_read({})))
    _session(monkeypatch, chat_id="a:1")
    assert "error" in json.loads(asyncio.run(plugin.teams_thread_read({})))


def test_slash_command_in_thread_drops_unpersisted_watermark():
    """/new after a first turn whose watermark never persisted: next mention must re-read the full thread."""
    graph = FakeGraph([gm(ROOT, 0, "root"), gm("1001", 1, "a")])
    adapter = make_adapter(graph, FakeStore())
    asyncio.run(deliver(adapter, activity("1001", minute=1), event()))
    assert adapter._tt_pending
    asyncio.run(deliver(adapter, activity("1002", minute=2), event("/new")))
    assert adapter._tt_pending == {}


def test_flush_gives_up_with_warning(caplog):
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]), FakeStore())
    with caplog.at_level(logging.WARNING):
        asyncio.run(deliver(adapter, activity("1001", minute=1), event()))
    assert any("watermark not persisted" in r.getMessage() for r in caplog.records)


def test_dispatch_failure_is_logged(caplog):
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]), FakeStore(existing=[key()]))

    async def boom(_ev):
        raise RuntimeError("runner exploded")

    adapter._tt_dispatch = boom
    with caplog.at_level(logging.ERROR):
        asyncio.run(deliver(adapter, activity("1001", minute=1), event()))
    assert any("dispatch failed" in r.getMessage() for r in caplog.records)


# ---------- D1: budget follows the active model's context window ----------

class FakeRunner:
    def __init__(self, ctx_len=None, exc=None):
        self.ctx_len, self.exc, self.calls = ctx_len, exc, []

    async def _inbound_model_context_length(self, source, session_key):
        self.calls.append(session_key)
        if self.exc:
            raise self.exc
        return self.ctx_len

    async def handle(self, event):   # bound method -> adapter._message_handler.__self__ is the runner
        return None


def _budget_seen(monkeypatch, adapter):
    seen = {}
    real = plugin.tc.compose_context

    async def spy(messages, *, budget_tokens, tail_tokens=None, **kw):
        seen["budget"], seen["tail"] = budget_tokens, tail_tokens
        return await real(messages, budget_tokens=budget_tokens, tail_tokens=tail_tokens, **kw)
    monkeypatch.setattr(plugin.tc, "compose_context", spy)
    asyncio.run(deliver(adapter, activity("1999"), event()))
    return seen["budget"]


def _hermes_config(monkeypatch, cfg):
    mod = types.ModuleType("hermes_cli.config")
    mod.load_config = lambda: cfg
    monkeypatch.setitem(sys.modules, "hermes_cli", sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)


def _spy_tail(monkeypatch, adapter):
    seen = {}
    real = plugin.tc.compose_context

    async def spy(messages, *, budget_tokens, tail_tokens=None, **kw):
        seen["budget"], seen["tail"] = budget_tokens, tail_tokens
        return await real(messages, budget_tokens=budget_tokens, tail_tokens=tail_tokens, **kw)
    monkeypatch.setattr(plugin.tc, "compose_context", spy)
    asyncio.run(deliver(adapter, activity("1999"), event()))
    return seen


def test_new_session_may_fill_up_to_compression_threshold(monkeypatch):
    # Like Hermes: a session grows verbatim until threshold x window; only then is it compressed.
    _hermes_config(monkeypatch, {"compression": {"threshold": 0.50, "target_ratio": 0.20}})
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]))
    runner = FakeRunner(ctx_len=200_000)
    adapter._message_handler = runner.handle
    seen = _spy_tail(monkeypatch, adapter)
    # headroom = 0.50*200k - reserve (unknown usage -> Hermes tail 20k as reserve for prompt/tools)
    assert seen == {"budget": 80_000, "tail": 20_000}
    assert runner.calls == [key()]


def test_headroom_shrinks_with_tokens_already_used(monkeypatch):
    _hermes_config(monkeypatch, {"compression": {"threshold": 0.50, "target_ratio": 0.20}})
    store = FakeStore(existing=[key()], prompt_tokens={key(): 70_000})
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]), store)
    adapter._message_handler = FakeRunner(ctx_len=200_000).handle
    assert _spy_tail(monkeypatch, adapter) == {"budget": 30_000, "tail": 20_000}


def test_session_already_at_threshold_gets_hermes_tail_only(monkeypatch):
    _hermes_config(monkeypatch, {"compression": {"threshold": 0.50, "target_ratio": 0.20}})
    store = FakeStore(existing=[key()], prompt_tokens={key(): 99_000})
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]), store)
    adapter._message_handler = FakeRunner(ctx_len=200_000).handle
    assert _spy_tail(monkeypatch, adapter) == {"budget": 20_000, "tail": 20_000}


def test_budget_follows_user_compression_settings(monkeypatch):
    _hermes_config(monkeypatch, {"compression": {"threshold": 0.60, "target_ratio": 0.30}})
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]))
    adapter._message_handler = FakeRunner(ctx_len=1_000_000).handle
    assert _spy_tail(monkeypatch, adapter) == {"budget": 420_000, "tail": 180_000}


def test_budget_uses_hermes_defaults_when_config_unreadable(monkeypatch):
    mod = types.ModuleType("hermes_cli.config")
    def boom():
        raise OSError("no config")
    mod.load_config = boom
    monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]))
    adapter._message_handler = FakeRunner(ctx_len=200_000).handle
    assert _spy_tail(monkeypatch, adapter) == {"budget": 80_000, "tail": 20_000}


@pytest.mark.parametrize("handler", [None, "no-runner", "error"])
def test_budget_falls_back_when_context_length_unknown(monkeypatch, handler):
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]))
    if handler == "no-runner":
        async def plain(_e):
            return None
        adapter._message_handler = plain
    elif handler == "error":
        adapter._message_handler = FakeRunner(exc=RuntimeError("boom")).handle
    else:
        adapter._message_handler = None
    assert _budget_seen(monkeypatch, adapter) == plugin.DEFAULT_BUDGET_TOKENS


def test_prompt_tokens_read_via_hermes_entry_lookup(monkeypatch):
    _hermes_config(monkeypatch, {"compression": {"threshold": 0.50, "target_ratio": 0.20}})
    store = FakeStore(existing=[key()])
    loaded = {key(): types.SimpleNamespace(last_prompt_tokens=60_000)}
    store._entry_locked = lambda k: loaded.get(k)        # Hermes loads the index lazily
    adapter = make_adapter(FakeGraph([gm(ROOT, 0, "root")]), store)
    adapter._message_handler = FakeRunner(ctx_len=200_000).handle
    assert _spy_tail(monkeypatch, adapter) == {"budget": 40_000, "tail": 20_000}
