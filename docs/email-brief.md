# Email brief — proactive inbox digest

The voice agent can build a spoken summary of recent inbox mail and pass it to the notification dispatcher. In `auto` mode the dispatcher tries the active voice bridge first, then a Discord DM, then a configured webhook. The email-brief path does not currently supply a text-channel ID.

> **Current reliability and privacy status:** the implementation can report an empty inbox when both mail backends actually failed, can mark messages as briefed after an unsuccessful delivery attempt, and can report `notified: true` even when delivery returned an error or raised an exception. Recipient fallback and model-visible snippet handling also require hardening. See [Issue #10](https://github.com/Capslockb/gemini-live-discord-bridge/issues/10). Treat the returned `delivery` object—not the current `notified` boolean—as the best available delivery evidence.

## `local_email_brief` tool

```json
{
  "limit": 8,
  "force": false,
  "notify": true,
  "backend": "google"
}
```

- `limit` — maximum emails to consider. Default 8.
- `force` — skip the de-dup check and attempt another brief. Default false.
- `notify` — when true (default), call `build_and_notify()` and use `notification.deliver(mode="auto")`. Set false for a pure read with no notification attempt.
- `backend` — `"google"` (default, uses `google_api.py`) or `"himalaya"`. The implementation tries the requested backend first and then the other backend.

A successful build returns three buckets plus a per-email list:

```json
{
  "result": {
    "status": "ok",
    "backend": "google",
    "count": 5,
    "brief": "**1 important.**\n• Sarah Chen — URGENT: invoice overdue\n\n**1 FYI.**\n• Alex Kim — Quick question about the deploy\n\n**3 auto** (Promotions, Social, Updates).",
    "buckets": {
      "important": [{"id": "1", "from": "Sarah Chen ...", "subject": "URGENT: ...", "_score": 82}],
      "fyi": [],
      "auto": []
    },
    "emails": [{"id": "1", "from": "Sarah Chen ...", "subject": "URGENT: ...", "score": 82}],
    "notified": true,
    "delivery": {"status": "ok", "channel": "dm"}
  }
}
```

The current `buckets` objects retain the complete backend email dictionaries. With the Google backend, those dictionaries can include snippets of up to 300 characters even though the rendered spoken brief uses only sender and subject. Tool/model history must therefore be treated as inbox-sensitive data. Issue #10 tracks reducing the model-visible payload to the minimum required fields.

## Importance scoring (0–100)

The scoring formula is implemented in `email_brief.py:_score_email`:

| Signal | Score |
|---|---|
| Recency < 1h | +35 |
| Recency < 6h | +25 |
| Recency < 24h | +15 |
| Recency < 72h | +8 |
| Recency > 72h | +2 |
| Gmail label `IMPORTANT` | +25 |
| Gmail label `STARRED` | +15 |
| Gmail label `CATEGORY_PRIMARY` or `INBOX` | +10 |
| Subject matches urgent/asap/critical/emergency/deadline/overdue/invoice/contract/legal/signature/fwd | +12 |
| Sender contains `noreply`, `no-reply`, or `notifications@` | -30 |
| Gmail label `CATEGORY_PROMOTIONS`, `_SOCIAL`, `_UPDATES`, `_FORUMS`, `SPAM`, or `TRASH` | -50 |
| Already read | -10 |

Final score is clamped to 0–100. Buckets:

- **Important** (≥55)
- **FYI** (20–54)
- **Auto** (<20 or an auto-category label)

## Backend fallback

`fetch(limit, prefer)` tries the preferred backend first and then the other backend. The Google backend uses `google_api.py` and returns Gmail labels plus a snippet. The Himalaya backend uses the `himalaya` CLI for envelope-only listing without snippets or labels.

Current limitation: if both backends fail, `fetch()` returns an empty list with backend `none`, and `build_brief()` converts that into `status: "ok"`, `count: 0`, and “Your inbox is empty.” This is not reliable proof that the inbox is empty. Check backend availability separately until Issue #10 distinguishes backend failure from a successful empty result.

## De-duplication

State persists at `~/.hermes/voice-users/email-brief-state.json`:

```json
{
  "last_briefed_ids": ["1", "2", "3"],
  "last_brief_at": 1749312456.7
}
```

The scheduler and on-demand calls with `force=false` only attempt a brief when at least one email ID is absent from `last_briefed_ids`. The history is capped at 500 IDs.

Current limitation: `build_and_notify()` advances this state after the notification attempt regardless of whether the dispatcher returned `ok`, `error`, `unavailable`, or `no_subscribers`, and it also advances state after a caught delivery exception. It then returns `notified: true`. A failed attempt can therefore suppress retries for the same messages. This behavior is tracked in Issue #10.

The de-dup set is separate from the per-email reminder loop's seen set in `bridge_email.py`. Both paths can evaluate the same inbox independently.

## Background scheduler

`email_brief.py:start_brief_scheduler(get_bridge_fn, interval)` starts a daemon thread. The default interval is 30 minutes and is controlled by `DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS`. It is started by `bridge_http.py:run_sidecar()` after the live bridge and the notification scheduler start.

The scheduler resolves the destination in this order:

1. the active bridge target user;
2. `DISCORD_VOICE_LIVE_USER_ID`;
3. a deployment-specific user ID embedded in the current source.

Set `DISCORD_VOICE_LIVE_USER_ID` explicitly and verify the destination before enabling scheduled briefs. The embedded fallback is not a portable or privacy-safe default and is scheduled for removal under Issue #10.

The scheduler uses the live bridge returned by `get_bridge_fn`, allowing `auto` delivery to try voice first. If voice is unavailable it can try a DM and then a configured webhook. Email sender and subject data may leave the local process through those destinations.

## When to use

| Use case | Tool |
|---|---|
| “What just came in?” on demand | `local_email_brief` with `force=true` |
| Scheduled inbox brief | background scheduler, default 30-minute interval |
| Read a specific email | `local_email_read` |
| Reply to an email | `local_email_reply` |

Until Issue #10 is resolved, use `notify=false` when a pure read is sufficient, inspect the returned backend and delivery fields, and do not treat `notified: true` as conclusive delivery evidence.

## Disable

Set `DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED=false` to disable the scheduler. The on-demand `local_email_brief` tool remains available.