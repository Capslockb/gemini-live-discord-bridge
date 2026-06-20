# Environment variables

Code-grounded environment variable reference for the current Gemini Discord voice bridge. Defaults are taken from `bridge.py`, `__init__.py`, and the related helper modules.

For protocol details, see [`gemini-live-implementation.md`](gemini-live-implementation.md).

## Required / strongly recommended

| Var | Default | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | Discord bot token. Required by the Hermes Discord adapter / installer path. |
| `GEMINI_API_KEY` | — | Gemini API key. Required unless `GOOGLE_API_KEY` is set. |
| `GOOGLE_API_KEY` | — | Fallback Gemini API key used when `GEMINI_API_KEY` is empty. |
| `DISCORD_VOICE_LIVE_USER_ID` | empty / deployment-specific fallback | Strongly recommended. Used for slash-command channel inference, target-user presence checks, and default Honcho peer naming. Explicit guild/channel tool calls can still work without it. |

## Gemini Live

| Var | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Primary Gemini Live model. Sent as `models/<model>` in setup. |
| `GEMINI_LIVE_MODEL_FALLBACKS` | `gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025,gemini-2.5-flash-native-audio-preview-09-2025` | Comma-separated fallback candidates. The code deduplicates candidates and tries them in order. |
| `DISCORD_VOICE_LIVE_VOICE` | `Kore` | Gemini prebuilt voice name used in `speechConfig.voiceConfig.prebuiltVoiceConfig`. |
| `DISCORD_VOICE_LIVE_GREETING` | `I'm here.` | Optional text injected after Gemini connects. Set empty if you want a silent connection after first-turn mute. |

## Sidecar control API

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_PORT` | `18943` | Local HTTP sidecar port on `127.0.0.1`. |
| `DISCORD_VOICE_LIVE_SECRET_FILE` | `~/.hermes/voice-live-control-secret` | File used to persist the `X-API-Secret` for mutating sidecar routes. |
| `DISCORD_VOICE_LIVE_NOTIFY_TIMEOUT` | `5` | Timeout for notification-side HTTP/control operations. |

Read-only routes are `/health` and `/notes`. Mutating routes `/stop`, `/say`, `/frame`, and `/notify` require `X-API-Secret`.

## Discord voice session

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS` | empty | Comma-separated user IDs accepted by the sink. Empty = allow all non-bot users in the voice channel. |
| `DISCORD_VOICE_LIVE_LEAVE_PHRASES` | built-in phrase list | Spoken phrases that request the bridge to leave, such as `leave voice`, `disconnect`, `hang up`, `bye`. |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS` | `900` | Fallback auto-leave timeout after quiet audio, if idle prompt flow is disabled or not active. |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS` | `120` | Minimum session uptime before idle prompt / auto-leave can fire. |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS` | `120` | Seconds of inactivity before the bridge prompts the user. |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS` | `60` | Seconds after the idle prompt before hangup if no activity returns. |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT` | `You alive, or am I hanging up?` | Text sent into Gemini for the idle prompt. |

## Audio timing / interruption

| Var | Default | Description |
|---|---|---|
| `GEMINI_AUDIO_STREAM_IDLE_END_SECONDS` | `0.25` | Seconds after last audio chunk before sending `realtimeInput.audioStreamEnd`. |
| `DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS` | `320` | Silence inserted before the first Gemini output audio chunk of a turn. |
| `DISCORD_VOICE_LIVE_OUTPUT_FADE_IN_MS` | `0` | Fade-in duration applied to the first chunk of a model turn. |
| `DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS` | `0.005` | How long `LiveAudioSource.read()` waits for queued audio before returning silence. |
| `DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS` | `240` | Silence inserted at the end of a completed output turn. |
| `DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT` | `true` | Clear queued Discord output when the user interrupts locally or Gemini sends `interrupted`. |

## Video / frame feeder

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_VIDEO_ENABLED` | `true` | Enables `/frame` and queued video input to Gemini. |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_FPS` | `1` | Max accepted frame rate. The code clamps this to 1 fps. |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES` | `524288` | Max accepted image payload size, 512 KiB by default. |
| `DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS` | `8` | Drop non-forced frames if no recent voice activity occurred within this window. |
| `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S` | `30` | Webhook threshold for announcing video reinitialization after quiet time. |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION` | `true` | Enable video state polling from the plugin layer. |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL` | `5` | Poll interval for video state detection. Note: current code uses this name, not `_SECONDS`. |

## SFX / typing indicator

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_SFX_ENABLED` | `true` | Master SFX enable in the SFX module. |
| `DISCORD_VOICE_LIVE_SFX_DIR` | `~/.hermes/voice-users/sfx/` | Directory for slot-based SFX files. |
| `DISCORD_VOICE_LIVE_SFX_<SLOT>` | per-slot | Override WAV path for a slot. Slots include `TOOL_INIT`, `ERROR`, `NOTIFICATION`, `TRANSITION`. |
| `DISCORD_VOICE_LIVE_SFX_<SLOT>_VOLUME` | per-slot | Per-slot volume. |
| `DISCORD_VOICE_LIVE_TYPING_SOUND` | `true` | Enable typing/click sound during tool calls. |
| `DISCORD_VOICE_LIVE_TYPING_SFX` | empty | Optional WAV path for typing sound. |
| `DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME` | `0.35` | Typing sound volume. |
| `DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK` | `false` | Generate synthetic clicks if the WAV is missing. |

## Notes / transcripts

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_NOTES_DIR` | `~/.hermes/voice-live-notes/` | JSONL transcript/note event directory used by `/notes`. |

## Autostart

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_AUTOSTART` | `false` | If true, schedule autostart after the gateway/adapter is ready. |
| `DISCORD_VOICE_LIVE_AUTOSTART_FILE` | `~/.hermes/voice-live-autostart.json` | JSON file used for autostart parameters. |
| `DISCORD_VOICE_LIVE_GUILD_ID` | empty | Guild ID used for autostart if channel inference is not possible. |
| `DISCORD_VOICE_LIVE_CHANNEL_ID` | empty | Voice channel ID used for autostart if inference is not possible. |
| `DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE` | `true` | Keep the autostart file after successful start. |

## Tool gates

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_LOCAL_TOOLS` | `true` | Umbrella gate for local bridge tools. |
| `DISCORD_VOICE_LIVE_WEB_TOOLS` | `true` | Web search/extract tools. |
| `DISCORD_VOICE_LIVE_SPOTIFY_TOOLS` | `true` | Spotify voice tools. |
| `DISCORD_VOICE_LIVE_GITHUB_TOOLS` | `true` | GitHub tools. |
| `DISCORD_VOICE_LIVE_HA_TOOLS` | `true`, but requires `HASS_TOKEN` | Home Assistant tools. Disabled automatically if `HASS_TOKEN` is empty. |
| `DISCORD_VOICE_LIVE_OPENCODE_TOOLS` | `true` | OpenCode/delegation tools. |
| `DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS` | `true` | Read-only allowlisted system inspection tools. |
| `DISCORD_VOICE_LIVE_EMAIL_TOOLS` | `true` | Email read/send/reply/brief tools where backend is configured. |

## Email reminders / briefs

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED` | `true` | Enable per-email reminder loop. |
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS` | `300` | Poll interval for unread inbox checks. |
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR` | `3` | Reminder cap per rolling hour. |
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED` | `true` | Enable scheduled email brief. |
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS` | `1800` | Email brief interval. |
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_LIMIT` | `8` | Max emails per brief. |

## Honcho / profile context

| Var | Default | Description |
|---|---|---|
| `VOICE_LIVE_HONCHO_CONTEXT` | `true` | Inject Honcho context into the system prompt. |
| `VOICE_LIVE_HONCHO_MAX_CHARS` | `1200` | Max Honcho context characters. |
| `VOICE_LIVE_HONCHO_PEER` | `HONCHO_PEER_NAME`, then user ID, then `user` | Override peer name used for memory context. |
| `VOICE_USERS_DIR` | `~/.hermes/voice-users/` | Per-user profile directory. |
| `VOICE_OWNER_DISCORD_ID` | env only | Owner ID used by owner-only commands. |

## External integrations

| Var | Default | Description |
|---|---|---|
| `GOOGLE_API_BIN` | auto-detected | Path to `google_api.py` used for Google Workspace/email helpers. |
| `HASS_URL` | `http://homeassistant.local:8123` | Home Assistant base URL. |
| `HASS_TOKEN` | — | Home Assistant long-lived access token. Required for HA tools. |
| `OPENCODE_BIN` | `~/.local/bin/opencode` | Path to OpenCode binary. |
| `OPENCODE_DEFAULT_MODEL` | OpenCode default | Model passed to OpenCode. |
| `OPENCODE_TMUX_SESSION` | `voice-opencode` | tmux session name for delegated OpenCode work. |
