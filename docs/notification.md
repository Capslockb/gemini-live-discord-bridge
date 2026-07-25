# Notification system — proactive delivery

The notification layer lets the agent deliver messages outside the immediate reply flow through voice, Discord, or configured webhooks.

## Delivery modes

`notification.py:deliver(text, mode, ...)` supports four delivery destinations and two selection modes:

| Mode | What happens | When to use |
|---|---|---|
| `voice` | Push `text` into Gemini via `bridge._gemini.send_text()` so the model speaks it in the next turn | A voice session is active and the user is in the channel |
| `dm` | Send a Discord DM directly to the user through the bot adapter | The user is offline or away from the voice channel |
| `channel` | Post to a Discord text channel in the same guild | A voice-log or notes channel exists |
| `webhook` | Emit an `agent.notify` event through `WebhookDispatcher` | A configured webhook destination should receive the notification |
| `auto` | Try voice → DM → channel → webhook and return the first successful result | Default when the best available destination is not known in advance |
| `all` | Attempt all four delivery destinations | Only when duplicate delivery is intentional |

## Gemini tools

### `local_notify`

```json
{
  "text": "Hey, codex is back online",
  "mode": "auto",
  "title": "Tool online",
  "source": "health_watcher"
}
```

Returns a delivery result such as:

```json
{
  "status": "ok",
  "channel": "voice",
  "queued": true
}
```

The exact result shape depends on the selected delivery path.

### `local_notify_schedule`

```json
{
  "text": "Reminder: standup in 10",
  "delay_seconds": 600,
  "mode": "auto"
}
```

Returns `{scheduled: <id>, fire_at_epoch: <ts>}`. The schedule persists to `~/.hermes/voice-scheduled-notifications.jsonl` and is polled by a background thread.

Other actions on the same tool:

- `{"list": true}` lists scheduled notifications.
- `{"cancel_id": "abc123"}` removes one scheduled notification.

## Sidecar HTTP endpoint status

`/notify` is intended for local callers that are outside a Gemini session, such as automation or helper processes. It is not currently safe to document as operational:

- Current `main` cannot complete authentication for `/notify` because of [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5).
- The built-in `notification.sidecar_notify()` client does not attach the required `X-API-Secret` header. After the server-side authentication crash is fixed, that helper would still receive `401 Unauthorized` until the client path is updated and tested.

Do not use `/notify` from cron jobs, subagents, proxies, browser code, or remote callers until both the server authentication path and the built-in client have exact-head regression coverage. Keep the sidecar bound to loopback.

## AFK ping from the opencode watcher

The opencode watcher polls long-running `local_delegate_execute` tmux sessions and calls `notification.deliver(mode="auto")` after a session finishes. Delivery remains best-effort and depends on the configured voice, Discord, and webhook destinations.

## Webhook fanout

The notification dispatcher can emit the `agent.notify` event class through `WebhookDispatcher`, configured with:

```text
DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY=https://discord.com/api/webhooks/...
```

When unset, voice, DM, and channel delivery can still be attempted. A configured webhook adds another outbound destination. Treat webhook URLs as credentials and avoid sending private transcript or task content to destinations that are not explicitly trusted.

## Notification sound effect

When a notification is delivered, the bridge can play the `notification` sound-effect slot. See [`sfx-library.md`](sfx-library.md). Disable it with `DISCORD_VOICE_LIVE_SFX_ENABLED=false`.

## When not to use

- For task results already returned to the model, prefer the tool result so the model can narrate it in the existing turn.
- For urgent alerts that must interrupt the user, use only a reviewed and trusted destination with explicit delivery policy.
- For requested reminders, use `local_notify_schedule` so the scheduled action has a persistent audit record.
