# Troubleshooting

Common bridge failures and how to fix them. For implementation details, see [`gemini-live-implementation.md`](gemini-live-implementation.md).

## `/voice-live` cannot infer my voice channel

The slash wrapper infers the channel from `DISCORD_VOICE_LIVE_USER_ID`.

Check:

```bash
grep '^DISCORD_VOICE_LIVE_USER_ID=' ~/.hermes/.env
```

Then join a Discord voice channel before running `/voice-live`.

If you start via Hermes tool call instead of the slash command, pass `guild_id` and `channel_id` explicitly.

## Bridge seems slow to start

Let one connect cycle finish. Discord voice connect can take time, and repeatedly restarting the gateway can reset retries or make rate limits worse.

Watch logs:

```bash
journalctl --user -u hermes-gateway -f
```

Look for:

- `VoiceLive: connecting`
- `Control API listening on 127.0.0.1:18943`
- `Setup complete for model ...`
- `VoiceLive: bridge active`

## Health check says not started

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

If the sidecar is unavailable, the bridge is not running or the port differs from `DISCORD_VOICE_LIVE_PORT`.

If the sidecar returns:

```json
{"status":"not_started","running":false}
```

then the HTTP server is reachable but no active bridge object is registered.

## Mutating sidecar routes return `401 unauthorized`

`/health` and `/notes` are read-only and unauthenticated. These routes require `X-API-Secret`:

- `/stop`
- `/say`
- `/frame`
- `/notify`

Use:

```bash
SECRET=$(cat ~/.hermes/voice-live-control-secret)
curl -s -H "X-API-Secret: $SECRET" "http://127.0.0.1:18943/say?text=ping"
```

If the secret file is missing, check `DISCORD_VOICE_LIVE_SECRET_FILE` and gateway logs for control-secret initialization warnings.

## Gemini setup fails for all models

The code tries `GEMINI_MODEL` first, then unique entries from `GEMINI_LIVE_MODEL_FALLBACKS`.

Check:

```bash
grep -E '^(GEMINI_API_KEY|GOOGLE_API_KEY|GEMINI_MODEL|GEMINI_LIVE_MODEL_FALLBACKS)=' ~/.hermes/.env
journalctl --user -u hermes-gateway -n 100 --no-pager | grep -i 'Gemini Live model\|Gemini connect failed\|setup'
```

If you see a schema error around setup fields, compare the code with [`gemini-live-implementation.md`](gemini-live-implementation.md). `mediaResolution` and top-level `voice_activity_detection` are intentionally omitted.

## First-turn hallucination or unwanted opening line

The bridge sends an empty `audioStreamEnd` immediately after `setupComplete` to suppress unwanted first-turn generation.

If you still hear a greeting, check:

```bash
grep '^DISCORD_VOICE_LIVE_GREETING=' ~/.hermes/.env
```

The code default is currently `I'm here.`. Set this empty for a silent connection:

```bash
DISCORD_VOICE_LIVE_GREETING=
```

Then restart:

```bash
systemctl --user restart hermes-gateway
```

## No inbound audio reaches Gemini

Check health:

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

Look for:

- `voice_connected: true`
- `receiving_active: true`
- `voice_sink_frames` increasing
- `voice_sink_decoded_frames` increasing when you speak
- `audio_in_chunks` increasing when speech passes the gate

Common causes:

- `discord-ext-voice-recv` missing or not loaded;
- the user is filtered out by `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS`;
- the speaker is a bot, which the sink ignores;
- speech is too quiet for the simple peak-energy gate.

## Audio output plays late or keeps talking over the user

The bridge has two interruption paths:

1. Gemini-side `START_OF_ACTIVITY_INTERRUPTS`.
2. Local output queue clear when speech energy is detected during an open model turn.

Check these env values:

```bash
DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT=true
DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS=0.005
DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS=320
DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS=240
```

If interruptions are still slow, inspect health for `local_interrupt_events` and logs for `serverContent.interrupted` behavior.

## `/frame` drops images

Check:

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool | grep video
```

Common drop reasons:

| Reason | Meaning |
|---|---|
| `disabled` | `DISCORD_VOICE_LIVE_VIDEO_ENABLED=false` |
| `unsupported_mime` | Only JPEG, PNG, and WebP are accepted |
| `size_limit` | Payload exceeds `DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES` |
| `fps_limit` | More than 1 fps by default |
| `no_recent_voice` | Frame was not forced and no recent voice activity occurred |

Manual forced frame test:

```bash
SECRET=$(cat ~/.hermes/voice-live-control-secret)
curl -s \
  -H "X-API-Secret: $SECRET" \
  -H "Content-Type: image/jpeg" \
  --data-binary @frame.jpg \
  "http://127.0.0.1:18943/frame?force=true&source=manual"
```

## Tool calls hang

Gemini `toolCall` messages are handled in the receive loop, but blocking handlers are sent through an executor. Long-running tools should return status quickly and let the user poll.

Watch logs:

```bash
journalctl --user -u hermes-gateway -n 100 --no-pager | grep -i 'Gemini tool call\|tool call handler\|local_\|opencode_'
```

If a handler never returns, later tool calls can queue behind it.

## `toolCallCancellation` appears in logs

The code currently logs cancellations but does not attempt rollback. If a tool already caused side effects, the bridge does not undo them.

For risky future tools, prefer idempotent actions, dry-run defaults, or explicit confirmation before side effects.

## Email brief returns `no backend`

Both configured email backends failed or are unavailable.

Check:

```bash
python ~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py auth
```

Also check any `himalaya` configuration if that backend is expected.

## Home Assistant tools do not appear

`DISCORD_VOICE_LIVE_HA_TOOLS=true` is not enough. The code also requires `HASS_TOKEN` to be non-empty.

```bash
grep -E '^(HASS_URL|HASS_TOKEN|DISCORD_VOICE_LIVE_HA_TOOLS)=' ~/.hermes/.env
```

## Autostart does not join voice

Autostart intentionally waits for the configured user to be in a voice channel to avoid token burn.

Check:

```bash
grep -E '^(DISCORD_VOICE_LIVE_AUTOSTART|DISCORD_VOICE_LIVE_AUTOSTART_FILE|DISCORD_VOICE_LIVE_USER_ID|DISCORD_VOICE_LIVE_GUILD_ID|DISCORD_VOICE_LIVE_CHANNEL_ID)=' ~/.hermes/.env
```

The code default for `DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE` is `true`.

## Log locations

- Gateway: `journalctl --user -u hermes-gateway -f`
- Hermes logs: `~/.hermes/logs/`
- Bridge notes/transcripts: `~/.hermes/voice-live-notes/` by default
- SFX files: `~/.hermes/voice-users/sfx/`
- Control secret: `~/.hermes/voice-live-control-secret` by default
