# Hermes Live — Gemini Discord Voice Bridge

![Hermes Live Banner](docs/banner.png)

> **Current Gemini Live runtime for Hermes Discord voice.**
> Full-duplex Discord audio · Gemini Multimodal Live · local sidecar API · video frame feed · optional tool calling · Hermes plugin install.

## Status: active

This repository is the working Hermes Discord voice plugin for Gemini Multimodal Live. Use it for the live `/voice-live` Discord bridge.

The runtime code and Markdown documentation are the primary sources of truth. The checked-in static site under `docs-site/` is a generated snapshot and may lag until it is regenerated and reviewed.

---

## Repository reality check

| Area | Current state |
|---|---|
| Runtime | In-process Hermes plugin named `discord-voice` |
| Plugin metadata | `plugin.yaml` reports version `0.3.5` |
| Discord commands | `/voice-live` and `/voice-live-leave` via Hermes |
| Hermes tools | Session start, leave, status, frame, video-status, and notes operations |
| Gemini model default | `gemini-3.1-flash-live-preview`, with fallback list via env |
| Gemini voice default | `Kore` |
| Audio path | Discord 48 kHz stereo PCM → 16 kHz mono Gemini input → 24 kHz mono Gemini output → Discord 48 kHz stereo |
| Sidecar API | Local HTTP, default `127.0.0.1:18943`, configurable with `DISCORD_VOICE_LIVE_PORT` |
| Sidecar auth | **Known high-severity blocker:** current `main` raises `NameError` in the mutating-route auth comparison. Treat `/stop`, `/say`, `/frame`, and `/notify` as unavailable until [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) is fixed and tested. |
| HTTP response framing | `_format_response()` calculates `Content-Length` from a Python string before UTF-8 encoding. Default JSON responses normally ASCII-escape non-ASCII values, but direct Unicode bodies, `ensure_ascii=False`, and future non-escaping callers remain unsafe until [Issue #13](https://github.com/Capslockb/gemini-live-discord-bridge/issues/13) is fixed and tested. |
| Video delivery | The `/frame` route, external feeder, and `voice_live_frame` client are not end-to-end operational. Startup, secret-file, and missing-auth-header defects are tracked in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9). |
| Transcript exposure | `/notes` is currently unauthenticated and returns recent transcript/note events. Keep the sidecar strictly loopback-only and review [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) before exposing it through any proxy or browser-accessible path. |
| Static docs site | Kept in `docs-site/`, but it is a generated snapshot and may lag behind the Markdown docs and code |

---

## Gemini Live implementation notes

The bridge uses the Gemini Live WebSocket endpoint directly instead of the GenAI SDK. The setup payload, audio format, VAD/interruption behavior, frame input, tool-call handling, reconnect behavior, and sidecar API are documented here:

**[`docs/gemini-live-implementation.md`](docs/gemini-live-implementation.md)**

That page explains how the modular `bridge_core.py`, `bridge_audio.py`, `bridge_http.py`, and related modules map Discord voice to Gemini Live. `bridge.py` is a compatibility facade.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge

# 2. Install into Hermes
./install.sh                 # fresh remote install; prompts for env
./install.sh --from-local    # symlink this exact working copy for development
./install.sh --no-prompt     # skip prompts only; required env is not validated
./install.sh --uninstall     # remove plugin install

# 3. Restart Hermes gateway
systemctl --user restart hermes-gateway

# 4. From Discord, join a voice channel and run:
/voice-live
/voice-live-leave
```

The installer derives its main paths from `${HERMES_HOME:-$HOME/.hermes}`:

```text
${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python
${HERMES_HOME:-$HOME/.hermes}/plugins/discord-voice
${HERMES_HOME:-$HOME/.hermes}/.env
```

Current exception: `install.sh` still copies bundled SFX to `$HOME/.hermes/voice-users/sfx`, even when `HERMES_HOME` points elsewhere. For a custom root, set `DISCORD_VOICE_LIVE_SFX_DIR` and populate that directory explicitly. The executable path unification is tracked in [Issue #18](https://github.com/Capslockb/gemini-live-discord-bridge/issues/18).

A plain `./install.sh` clones only when the plugin path does not already exist. On a rerun it leaves the existing tree unchanged, so it is not an update command. Current `--from-local` behavior is destructive to an existing plugin path: it removes an existing installation directory and replaces any existing install symlink before linking the current checkout. Back up local modifications and verify the target first. `--no-prompt` only bypasses prompts and can still report completion when required credentials are absent. The non-destructive installer correction is tracked in [Issue #11](https://github.com/Capslockb/gemini-live-discord-bridge/issues/11) and draft [PR #22](https://github.com/Capslockb/gemini-live-discord-bridge/pull/22).

---

## Required environment

Minimum required:

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***        # GOOGLE_API_KEY also works in code
```

Required for safe `/voice-live` and `/voice-live-leave` channel inference on current `main`:

```bash
DISCORD_VOICE_LIVE_USER_ID=123456789012345678
```

Without that setting, the runtime falls back to a repository-embedded Discord account. Tool callers can avoid user inference only by supplying both `guild_id` and `channel_id`. The fail-closed runtime correction is tracked in [Issue #16](https://github.com/Capslockb/gemini-live-discord-bridge/issues/16).

Common optional settings:

```bash
DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS=123456789012345678,987654321098765432
DISCORD_VOICE_LIVE_PORT=18943
DISCORD_VOICE_LIVE_VOICE=Kore
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_MODEL_FALLBACKS=gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025,gemini-2.5-flash-native-audio-preview-09-2025
VOICE_LIVE_HONCHO_CONTEXT=true
VOICE_LIVE_HONCHO_MAX_CHARS=1200
```

Full code-grounded env reference: [`docs/env-vars.md`](docs/env-vars.md).

---

## What this bridge does

| Feature | Current implementation |
|---|---|
| Full-duplex Discord voice | Receives decoded Discord audio, streams PCM to Gemini Live, plays Gemini audio back into Discord |
| Gemini Live WebSocket | Uses Google Gemini Live `BidiGenerateContent` WSS directly |
| Audio conversion | Discord 48 kHz stereo input → Gemini 16 kHz mono input; Gemini 24 kHz mono output → Discord 48 kHz stereo playback |
| Barge-in | Uses Gemini `START_OF_ACTIVITY_INTERRUPTS` plus a local fast output-clear path on speech energy |
| First-turn mute | Sends empty `audioStreamEnd` after `setupComplete`; deployment should set `DISCORD_VOICE_LIVE_GREETING=` if it wants zero greeting output |
| Video frames | `/frame`, the external feeder, and `voice_live_frame` are present but blocked on current `main`; see [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) and [`docs/video.md`](docs/video.md). |
| Optional integrations | Tool calls can be enabled by deployment configuration. Review each integration's permissions, data boundary, and destination before enabling it. |
| Per-user context | Optional context can be supplied when explicitly configured |
| Proactive notification path | Local notification dispatcher and `/notify` sidecar route; authenticated sidecar delivery remains blocked by [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5). |
| Notes/transcripts | JSONL-style note events under `~/.hermes/voice-live-notes/` by default |
| Idle handling | Idle prompts, grace hangup, and fallback auto-leave are env-driven |

---

## Sidecar HTTP API

Default bind:

```text
http://127.0.0.1:18943
```

> **Security status:** current `main` has a known auth-path crash in `bridge_http.py`. Requests reaching the secret comparison raise `NameError`, so the mutating routes below must not be treated as operational until [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) is fixed with exact-head tests. `/notes` is also unauthenticated in the current code and exposes recent transcript data; do not proxy or otherwise broaden access to the sidecar while [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) remains open.

| Route | Intended auth | Purpose |
|---|---|---|
| `/health` | none | Bridge health and metrics |
| `/notes?limit=N` | none | Recent transcript/note events |
| `/frame` | `X-API-Secret` | Submit a JPEG/PNG/WebP frame for Gemini vision; current clients are also blocked by Issue #9. |
| `/say?text=...` | `X-API-Secret` | Inject text into the live bridge |
| `/notify` | `X-API-Secret` | Send a proactive notification payload |
| `/stop` | `X-API-Secret` | Stop the active bridge |

Secret file default:

```bash
DISCORD_VOICE_LIVE_SECRET_FILE=~/.hermes/voice-live-control-secret
```

---

## Transport-neutral mobile realtime adapter

`mobile_realtime.py` exposes the existing `GeminiLiveBridge` session core to a separate, loopback-only WebSocket transport for the SORA Mobile Gateway. It does not expose Discord interfaces and does not implement a second assistant. Existing Discord behavior remains in the Discord adapter.

The adapter accepts authenticated gateway traffic at `WS /v1/realtime`, preserves `contextId`, emits neutral lifecycle/transcript/tool/interruption events, accepts 16 kHz mono PCM input, and returns the bridge's established 24 kHz mono PCM output. The adapter uses the server-owned `GEMINI_API_KEY`; client session frames do not carry provider credentials. The key stays in process memory for the session and is passed to Google in the `x-goog-api-key` header rather than the URL.

Run only on loopback or an equivalent private service network:

```bash
export SORA_REALTIME_INTERNAL_TOKEN='[REDACTED]'
uvicorn mobile_realtime:app --host 127.0.0.1 --port 9930 --no-access-log
```

The Android/Tailscale-facing endpoint is the separately authenticated SORA Mobile Gateway, never this internal adapter directly.

---

## Documentation map

Current Markdown docs:

- [`docs/gemini-live-implementation.md`](docs/gemini-live-implementation.md) — Gemini Live setup, audio, video, tools, and reconnect details.
- [`docs/architecture.md`](docs/architecture.md) — architecture and lifecycle.
- [`docs/env-vars.md`](docs/env-vars.md) — code-grounded environment defaults.
- [`docs/quickstart.md`](docs/quickstart.md) — installation and first session.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — operational failure modes.
- [`docs/video.md`](docs/video.md) — current frame-feeder and `/frame` status.

`docs-site/` is a generated static-site snapshot. Treat the Markdown docs and code as more current until the site is regenerated and reviewed.

---

## Scope and maintenance

This repository documents and maintains the Gemini Live Discord bridge. Broader orchestration products, provider-routing plans, and unrelated control surfaces are outside this README's public support scope.

Keep public documentation focused on observable product behavior, supported configuration, security boundaries, troubleshooting, and contribution guidance. Avoid deployment-specific identifiers, credentials, private operational data, and claims that are not backed by the current code or tests.

---

## Development notes

```bash
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"

# Compile-check plugin files through the Hermes venv
"$HERMES_ROOT/hermes-agent/venv/bin/python" -m py_compile *.py

# Run installed regression tests if present
cd "$HERMES_ROOT/plugins/discord-voice"
"$HERMES_ROOT/hermes-agent/venv/bin/python" -m unittest tests.test_interrupt_latency tests.test_transcript_latency -v
```

Do not run multiple competing voice-bridge instances against the same Discord bot or channel unless you are intentionally testing conflicts.

---

## License

This repository does not currently contain a canonical `LICENSE` file. Do not assume MIT or other reuse rights from older generated documentation. The owner decision and follow-up work are tracked in [Issue #7](https://github.com/Capslockb/gemini-live-discord-bridge/issues/7).
