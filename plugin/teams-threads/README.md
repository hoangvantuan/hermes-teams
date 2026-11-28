# teams-threads

Version: see `plugin.yaml` (the code's `VERSION` must match; a test checks it).

Hermes plugin: a Microsoft Teams channel thread is one context. When the bot is @mentioned in a
channel thread, the plugin reads the thread from Microsoft Graph and gives it to the agent as
quoted context. The first turn gets the whole thread; later turns get only the new messages.
Long threads are compacted. The agent can re-read the thread verbatim with `teams_thread_read`.
DMs and group chats behave exactly as without the plugin. Spec: `docs/PRD.md`.

## Requirements

- Hermes at commit `ae2a81ccc0` (tested). The plugin subclasses the bundled Teams adapter. At startup it checks
  that the adapter still has the methods it relies on; if not, it logs an ERROR and registers the plain Teams adapter.
- `TEAMS_REQUIRE_MENTION=true`.
- The Teams app has RSC permission `ChannelMessage.Read.Group` (Application) and is installed in the team.
- Standard channels only (no private or shared channels).

## Install

The plugin lives in a subdirectory of this repo. Install it from Git on the Hermes host:

1. Install, either way:
   - Dashboard: **Plugins → Install from GitHub / Git URL**, enter `hoangvantuan/hermes-teams/plugin/teams-threads`, click **Install**.
   - CLI: `hermes plugins install hoangvantuan/hermes-teams/plugin/teams-threads`.

   If an older copy exists (e.g. copied by hand), tick **Force reinstall** (CLI: add `--force`).
2. Make sure the plugin is enabled (`plugins.enabled` in `config.yaml` contains `teams-threads`).
3. Restart the gateway (dashboard **Restart Gateway**, or restart the gateway service).
4. Check `gateway.log` for `[teams-threads] registered ThreadAwareTeamsAdapter v<version>` and `✓ teams connected`.

## Update

- CLI: `hermes plugins update teams-threads`, then restart the gateway. Hermes re-installs from the recorded Git source.
- Dashboard: the **Git pull** button is not shown for subdirectory installs (no `.git` in the plugin folder).
  Repeat the Install step with **Force reinstall**, then restart the gateway.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| Thread history budget | from Hermes `compression.*` | Handled like Hermes's own context: the thread may fill the session up to the compression line (threshold x model context window, minus the tokens the session already used; 40% of the window for a new session by default). Above it: root + newest messages verbatim up to Hermes's tail budget (target_ratio x threshold x window, 10% by default), middle summarized. No plugin setting. |
| `auxiliary.compression` (Hermes) | current config | Model used for the summary (same route as session compression). |
| `TEAMS_CLIENT_ID/SECRET/TENANT_ID` | existing | Reused for the Graph app-only token. |

## Logs

Lines start with `[teams-threads]` and contain only ids and counts, never message text or tokens:
`root=<id> mode=full|delta fetched=N added=N tokens=N budget=N tail=N`. Graph failures log a WARNING and the bot
answers without history. A thread with more than 3000 replies logs a WARNING; the oldest replies beyond that are not read.

## Rollback

```
hermes plugins disable teams-threads   # or remove it from plugins.enabled
```

Then restart the gateway.

Hermes falls back to the bundled Teams adapter. The watermark stored in session metadata
(`teams_thread_watermark`) is ignored by Hermes and can stay.

## Tests

```
uv venv ~/.venv/hermes-teams
uv pip install --python ~/.venv/hermes-teams/bin/python pytest
~/.venv/hermes-teams/bin/python -B -m pytest        # from the repo root; no Hermes needed
```

`tests/smoke_hermes.py` checks the plugin against the real Hermes modules on the host (instructions in the file).
