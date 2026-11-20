# teams-threads 0.1.0

Hermes plugin: a Microsoft Teams channel thread is one context. When the bot is @mentioned in a
channel thread, the plugin reads the thread from Microsoft Graph and gives it to the agent as
quoted context. The first turn gets the whole thread; later turns get only the new messages.
Long threads are compacted. The agent can re-read the thread verbatim with `teams_thread_read`.
DMs and group chats behave exactly as without the plugin. Spec: `docs/PRD.md`.

## Requirements

- Hermes at commit `ae2a81ccc0` (tested). The plugin subclasses the bundled Teams adapter.
- `TEAMS_REQUIRE_MENTION=true`.
- The Teams app has RSC permission `ChannelMessage.Read.Group` (Application) and is installed in the team.
- Standard channels only (no private or shared channels).

## Install

1. Copy the folder: `scp -r plugin/teams-threads solution:~/.hermes/plugins/` (files: `plugin.yaml`, `__init__.py`, `thread_context.py`, `graph_client.py`).
2. Make sure `config.yaml` has `plugins.enabled: [..., teams-threads]`.
3. Restart the gateway: `systemctl --user restart hermes-gateway.service`.
4. Check `gateway.log` for `[teams-threads] registered ThreadAwareTeamsAdapter v0.1.0` and `✓ teams connected`.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `TEAMS_THREAD_BUDGET_RATIO` (in `~/.hermes/.env`) | `0.10` | Share of the active model's context window for thread history added in one turn (clamped 0.01–0.40). Above it: root + newest messages verbatim, middle summarized. |
| `TEAMS_THREAD_BUDGET_TOKENS` (in `~/.hermes/.env`) | unset | Fixed budget instead of the ratio (min 1000). If the context window cannot be resolved, 12000 is used. |
| `auxiliary.compression` (Hermes) | current config | Model used for the summary (same route as session compression). |
| `TEAMS_CLIENT_ID/SECRET/TENANT_ID` | existing | Reused for the Graph app-only token. |

## Logs

Lines start with `[teams-threads]` and contain only ids and counts, never message text or tokens:
`root=<id> mode=full|delta fetched=N added=N tokens=N`. Graph failures log a WARNING and the bot
answers without history.

## Rollback

```
hermes plugins disable teams-threads   # or remove it from plugins.enabled
systemctl --user restart hermes-gateway.service
```

Hermes falls back to the bundled Teams adapter. The watermark stored in session metadata
(`teams_thread_watermark`) is ignored by Hermes and can stay.

## Tests

```
uv venv ~/.venv/hermes-teams
uv pip install --python ~/.venv/hermes-teams/bin/python pytest pytest-asyncio
~/.venv/hermes-teams/bin/python -B -m pytest        # from the repo root; no Hermes needed
```

`tests/smoke_hermes.py` checks the plugin against the real Hermes modules on the host (instructions in the file).
