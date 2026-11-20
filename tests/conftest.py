"""Test harness: fake the few Hermes modules the plugin imports, then load the plugin
directory (hyphenated name) as package ``teams_threads``. No Hermes install needed."""
import importlib.util
import pathlib
import sys
import types

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "plugin" / "teams-threads"


class FakeTeamsAdapter:
    """Minimal stand-in for plugins.platforms.teams.adapter.TeamsAdapter."""

    def __init__(self, config=None):
        self.config = config or types.SimpleNamespace(extra={})
        self._client_id = "app-id"
        self._client_secret = "secret"
        self._tenant_id = "tenant"
        self._session_store = None
        self.handled = []
        self.authorized = True
        self.on_message_activity_seen = None

    async def _on_message(self, ctx):
        # Real adapter builds a MessageEvent and calls self.handle_message within the same task.
        self.on_message_activity_seen = ctx.activity
        await self.handle_message(ctx.event)

    async def handle_message(self, event):
        self.handled.append(event)

    def _event_session_key(self, event):
        return "agent:main:teams:channel:" + event.source.chat_id

    def _is_sender_authorized(self, user_id, chat_type, chat_id):
        return self.authorized


def _fake_register(ctx):
    ctx.register_platform(name="teams", label="Microsoft Teams",
                          adapter_factory=lambda cfg: FakeTeamsAdapter(cfg),
                          check_fn="base-check", ensure_deps_fn="base-ensure")


def _install_fakes():
    adapter_mod = types.ModuleType("plugins.platforms.teams.adapter")
    adapter_mod.TeamsAdapter = FakeTeamsAdapter
    adapter_mod.register = _fake_register
    names = ["plugins", "plugins.platforms", "plugins.platforms.teams"]
    for name in names:
        mod = sys.modules.setdefault(name, types.ModuleType(name))
        mod.__path__ = []
    sys.modules["plugins.platforms.teams"].adapter = adapter_mod
    sys.modules["plugins.platforms.teams.adapter"] = adapter_mod


def _load_plugin():
    if "teams_threads" in sys.modules:
        return sys.modules["teams_threads"]
    spec = importlib.util.spec_from_file_location(
        "teams_threads", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["teams_threads"] = module
    spec.loader.exec_module(module)
    return module


_install_fakes()
_load_plugin()
