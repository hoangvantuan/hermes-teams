"""teams-threads v0.0.1 — skeleton for PRD gates C2/C3.

Behaviour is identical to the bundled Teams adapter. The only addition is one INFO log
line per inbound activity with ids (no message text), so we can compare activity ids
with Graph message ids. Rollback: remove ``teams-threads`` from ``plugins.enabled``.
"""
import logging

from plugins.platforms.teams import adapter as base

logger = logging.getLogger(__name__)


def _ids(activity) -> dict:
    conv = getattr(activity, "conversation", None)
    cd = getattr(activity, "channel_data", None)
    extra = getattr(cd, "model_extra", None) or {}
    rc = extra.get("replyChain")
    team = getattr(cd, "team", None)
    return {
        "activity_id": getattr(activity, "id", None),
        "reply_to_id": getattr(activity, "reply_to_id", None),
        "conversation_id": getattr(conv, "id", None),
        "conversation_type": getattr(conv, "conversation_type", None),
        "reply_chain_id": rc.get("id") if isinstance(rc, dict) else None,
        "team_aad_group_id": getattr(team, "aad_group_id", None),
        "text_len": len(getattr(activity, "text", None) or ""),
    }


class ThreadAwareTeamsAdapter(base.TeamsAdapter):
    async def _on_message(self, ctx) -> None:
        try:
            logger.info("[teams-threads] inbound %s", _ids(ctx.activity))
        except Exception:  # logging must never break message handling
            logger.debug("[teams-threads] id logging failed", exc_info=True)
        await super()._on_message(ctx)


class _Ctx:
    """Proxy that forwards everything to the real plugin ctx but swaps the adapter factory,
    so all other register_platform() arguments stay in sync with the bundled adapter."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, name):
        return getattr(self._ctx, name)

    def register_platform(self, **kwargs):
        kwargs["adapter_factory"] = lambda cfg: ThreadAwareTeamsAdapter(cfg)
        return self._ctx.register_platform(**kwargs)


def register(ctx) -> None:
    base.register(_Ctx(ctx))
    logger.info("[teams-threads] registered ThreadAwareTeamsAdapter v0.0.1")
