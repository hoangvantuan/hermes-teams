"""Smoke test against the REAL Hermes modules (not collected by pytest; no network, no writes).

Run on the Hermes host from a scratch dir, with the gateway's venv python:
    S=/tmp/tt-$RANDOM; mkdir -p $S/home $S/plugin; cp plugin/teams-threads/* $S/plugin/; cp tests/smoke_hermes.py $S/
    cd $S && HOME=$S/home HERMES_HOME=$S/home PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=~/.hermes/hermes-agent \
        <gateway-venv>/bin/python -B smoke_hermes.py $S/plugin; rm -rf $S
"""
import asyncio
import dataclasses
import importlib.util
import inspect
import json
import sys
import types
from datetime import datetime, timezone

plugin_dir = sys.argv[1]
spec = importlib.util.spec_from_file_location("teams_threads", plugin_dir + "/__init__.py",
                                              submodule_search_locations=[plugin_dir])
plugin = importlib.util.module_from_spec(spec)
sys.modules["teams_threads"] = plugin
spec.loader.exec_module(plugin)
from plugins.platforms.teams import adapter as base  # noqa: E402


def ok(name, cond):
    print(("PASS " if cond else "FAIL ") + name)


ok("subclass of real TeamsAdapter", issubclass(plugin.ThreadAwareTeamsAdapter, base.TeamsAdapter))
for m in plugin.REQUIRED_BASE_METHODS:
    ok(f"base has {m}", hasattr(base.TeamsAdapter, m))
ok("startup compatibility check passes", plugin._missing_base_methods() == [])
calls = {}


class Ctx:
    def register_platform(self, **kw):
        calls["platform"] = kw

    def register_tool(self, **kw):
        calls["tool"] = kw


plugin.register(Ctx())
kw = calls["platform"]
ok("check_fn from same module", kw["check_fn"] is base.check_requirements)
ok("ensure_deps_fn from same module", kw["ensure_deps_fn"] is base.check_teams_requirements)
ok("standalone_sender_fn kept", kw.get("standalone_sender_fn") is base._standalone_send)
ok("env_enablement_fn kept", kw.get("env_enablement_fn") is base._env_enablement)
ok("tool registered", calls["tool"]["name"] == "teams_thread_read" and calls["tool"]["toolset"] == "teams")
from gateway.platforms.event import MessageEvent  # noqa: E402
ok("MessageEvent.channel_context", "channel_context" in {f.name for f in dataclasses.fields(MessageEvent)})
from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning  # noqa: E402
ok("async_call_llm(task, messages, timeout)",
   {"task", "messages", "timeout"} <= set(inspect.signature(async_call_llm).parameters))
ok("extract_content_or_reasoning(max_reasoning_chars)",
   "max_reasoning_chars" in inspect.signature(extract_content_or_reasoning).parameters)
from gateway.session_context import clear_session_vars, set_session_vars  # noqa: E402
from gateway.session import SessionStore  # noqa: E402
ok("SessionStore get/set_session_metadata",
   hasattr(SessionStore, "get_session_metadata") and hasattr(SessionStore, "set_session_metadata"))
from tools.registry import registry  # noqa: E402
ok("registry.register accepts is_async", "is_async" in inspect.signature(registry.register).parameters)

# End-to-end through the REAL _on_message -> build_source -> MessageEvent -> our handle_message.
from gateway.config import PlatformConfig  # noqa: E402
cfg = PlatformConfig(enabled=True, extra={"client_id": "app-id", "client_secret": "s", "tenant_id": "t",
                                          "require_mention": True})
adapter = kw["adapter_factory"](cfg)
ok("factory builds ThreadAwareTeamsAdapter", type(adapter).__name__ == "ThreadAwareTeamsAdapter")
adapter._app = None
adapter.set_authorization_check(lambda uid, ct, cid, **_: True)
ROOT, CHAN, TEAM = "1000", "19:c@thread.tacv2", "gid"
CONV = f"{CHAN};messageid={ROOT}"


class FakeGraph:
    calls = []

    def fetch_thread(self, team, chan, root, *, after=None):
        self.calls.append((team, chan, root, after))

        def msg(i, m, t):
            return {"id": i, "messageType": "message", "createdDateTime": f"2026-11-20T03:0{m}:00Z",
                    "from": {"user": {"displayName": "Tuan"}}, "body": {"contentType": "html", "content": t}}
        return [msg(ROOT, 0, "root q"), msg("1001", 1, "chao anh bang 3 thu tieng"), msg("1002", 2, "<at>Conan</at>")]


adapter._tt_graph = FakeGraph()
captured = []


async def cap(ev):
    captured.append(ev)


adapter._tt_dispatch = cap
mention = types.SimpleNamespace(type="mention", mentioned=types.SimpleNamespace(id="28:app-id"))
act = types.SimpleNamespace(
    id="1002", text="<at>Conan</at>", reply_to_id=None, entities=[mention], attachments=[],
    from_=types.SimpleNamespace(id="29:user", aad_object_id="aad-u", name="Tuan"),
    recipient=types.SimpleNamespace(id="28:app-id"),
    conversation=types.SimpleNamespace(id=CONV, conversation_type="channel", name=None, tenant_id="t"),
    channel_data=types.SimpleNamespace(model_extra={"replyChain": {"id": ROOT}},
                                       team=types.SimpleNamespace(aad_group_id=TEAM),
                                       channel=types.SimpleNamespace(id=CHAN)),
    timestamp=datetime(2026, 11, 20, 3, 2, tzinfo=timezone.utc))


async def main():
    await adapter._on_message(types.SimpleNamespace(activity=act, conversation_ref=None))
    await adapter._tt_drain()


asyncio.run(main())
ok("graph called with team/channel/root", FakeGraph.calls and FakeGraph.calls[0][:3] == (TEAM, CHAN, ROOT))
ev = captured[0] if captured else None
ok("event dispatched with channel_context", ev is not None and "chao anh bang 3 thu tieng" in (ev.channel_context or ""))
ok("F3 empty mention text", ev is not None and ev.text == "(được gọi trong thread, không kèm nội dung)")
key = adapter._event_session_key(ev)
ok("session key keeps ;messageid=root", f";messageid={ROOT}" in key)
tokens = set_session_vars(platform="teams", chat_id=CONV)
try:
    out = json.loads(asyncio.run(plugin.teams_thread_read({"query": "3 thu"})))
finally:
    clear_session_vars(tokens)
ok("tool reads current thread via real session vars", out.get("count") == 1)

# TC5 against the REAL mention gate: adapter reply without @mention must not reach Graph nor dispatch.
FakeGraph.calls.clear()
captured.clear()
silent = types.SimpleNamespace(**{**vars(act), "id": "1003", "text": "oke luôn", "entities": []})


async def deliver(activity):
    await adapter._on_message(types.SimpleNamespace(activity=activity, conversation_ref=None))
    await adapter._tt_drain()


asyncio.run(deliver(silent))
ok("TC5 no mention: no Graph call, no dispatch", FakeGraph.calls == [] and captured == [])

# F12a through the REAL tool registry dispatcher (arg validation, result normalisation, async bridge).
registry.register(name=plugin.TOOL_NAME, toolset="teams", schema=plugin.TOOL_SCHEMA, handler=plugin.teams_thread_read,
                  is_async=True, description="smoke")
tokens = set_session_vars(platform="teams", chat_id=CONV)
try:
    res = registry.dispatch(plugin.TOOL_NAME, {"query": "3 thu"})
    out = json.loads(res) if isinstance(res, str) else res
    ok("registry.dispatch from plain thread", out.get("count") == 1)

    async def in_loop():
        r = registry.dispatch(plugin.TOOL_NAME, {"query": "3 thu"})
        return json.loads(r) if isinstance(r, str) else r
    ok("registry.dispatch inside running loop", asyncio.run(in_loop()).get("count") == 1)
finally:
    clear_session_vars(tokens)
