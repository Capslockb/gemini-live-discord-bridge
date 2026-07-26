# Hermes Live — Gemini Discord Voice Bridge

![Hermes Live Banner](docs/banner.png)

> **Current Gemini Live runtime for Hermes Discord voice.**
> Full-duplex Discord audio · Gemini Multimodal Live · local sidecar API · video frame feed · tool calling · Hermes plugin install.

## Status: active, but transitioning toward SORA Bridge

This repository is still the **working Hermes Discord voice plugin** for Gemini Multimodal Live. Use it today when you need the live `/voice-live` Discord bridge.

We are slowly transitioning the broader bridge layer into [`Capslockb/sora-agent`](https://github.com/Capslockb/sora-agent), because the current code, README, and static docs site have drifted apart. The long-term direction is:

- keep this repo stable as the Gemini Live runtime while migration happens;
- move shared bridge orchestration, provider selection, API/TUI control, MCP, and VOIP work into SORA;
- stop presenting older static docs as if they are a perfect source of truth;
- regenerate docs from the actual code once the SORA migration settles.

For now: **Gemini bridge = current Discord/Gemini runtime. SORA bridge = migration target and broader control layer.**

---

## Repository reality check

| Area | Current state |
|---|---|
| Runtime | In-process Hermes plugin named `discord-voice` |
| Plugin metadata | `plugin.yaml` reports version `0.3.5` |
| Discord commands | `/voice-live` and `/voice-live-leave` via Hermes |
| Hermes tools | `voice_live`, `voice_live_leave`, `voice_live_status`, `voice_live_frame`, `voice_live_video_status`, `voice_live_notes` |
| Gemini model default | `gemini-3.1-flash-live-preview`, with fallback list via env |
| Gemini voice default | `Kore` |
| Audio path | Discord 48 kHz stereo PCM → 16 kHz mono Gemini input → 24 kHz mono Gemini output → Discord 48 kHz stereo |
| Sidecar API | Local HTTP, default `127.0.0.1:18943`, configurable with `DISCORD_VOICE_LIVE_PORT` |
| Sidecar auth | **Known high-severity blocker:** current `main` raises `NameError` in the mutating-route auth comparison. Treat `/stop`, `/say`, `/frame`, and `/notify` as unavailable until [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) is fixed and tested. |
| HTTP response framing | `_format_response()` calculates `Content-Length` from a Python string before UTF-8 encoding. Default JSON responses normally ASCII-escape non-ASCII values, but direct Unicode bodies, `ensure_ascii=False`, and future non-escaping callers remain unsafe until [Issue #13](https://github.com/Capslockb/gemini-live-discord-bridge/issues/13) is fixed and tested. |
| Video delivery | The `/frame` route, external feeder, and `voice_live_frame` client are not end-to-end operational. Startup, secret-file, and missing-auth-header defects are tracked in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9). |
| Transcript exposure | `/notes` is currently unauthenticated and returns recent transcript/note events. Keep the sidecar strictly loopback-only and review [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) before exposing it through any proxy or browser-accessible path. |
| Static docs site | Kept in `docs-site/`, but it is a static snapshot and may lag behind the markdown docs/code |

---

## Gemini Live implementation notes

The bridge uses the Gemini Live WebSocket endpoint directly instead of the GenAI SDK. The setup payload, audio format, VAD/interruption behavior, frame input, tool-call handling, reconnect behavior, and sidecar API are documented here:

**[`docs/gemini-live-implementation.md`](docs/gemini-live-implementation.md)**

That page is the current best explanation of how the modular `bridge_core.py`, `bridge_audio.py`, `bridge_http.py`, and related modules map Discord voice to Gemini Live. `bridge.py` is a compatibility facade.

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

The installer expects the Hermes layout at:

```text
~/.hermes/hermes-agent/venv/bin/python
~/.hermes/plugins/discord-voice
~/.hermes/.env
```

A plain `./install.sh` clones only when the plugin path does not already exist. On a rerun it leaves the existing tree unchanged, so it is not an update command. Use `--from-local` to install the exact current checkout. `--no-prompt` only bypasses prompts and can still report completion when required credentials are absent. The executable correction is tracked in [Issue #11](https://github.com/Capslockb/gemini-live-discord-bridge/issues/11).

---

## Required environment

Minimum required:

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***        # GOOGLE_API_KEY also works in code
```

Strongly recommended:

```bash
DISCORD_VOICE_LIVE_USER_ID=123456789012345678
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

## What this bridge actually does

| Feature | Current implementation |
|---|---|
| Full-duplex Discord voice | Receives decoded Discord audio, streams PCM to Gemini Live, plays Gemini audio back into Discord |
| Gemini Live WebSocket | Uses Google Gemini Live `BidiGenerateContent` WSS directly |
| Audio conversion | Discord 48 kHz stereo input → Gemini 16 kHz mono input; Gemini 24 kHz mono output → Discord 48 kHz stereo playback |
| Barge-in | Uses Gemini `START_OF_ACTIVITY_INTERRUPTS` plus a local fast output-clear path on speech energy |
| First-turn mute | Sends empty `audioStreamEnd` after `setupComplete`; deployment should set `DISCORD_VOICE_LIVE_GREETING=` if it wants zero greeting output |
| Video frames | `/frame`, the external feeder, and `voice_live_frame` are present but blocked on current `main`; see [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) and [`docs/video.md`](docs/video.md). |
| Tool calling | Handles local, web, Spotify, GitHub, Home Assistant, email, inspection, onboarding, and delegation-style tools when configured |
| Honcho context | Optional per-user context injection into the system prompt |
| Proactive notification path | Local notification dispatcher and `/notify` sidecar route; authenticated sidecar delivery remains blocked by [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5). |
| Notes/transcripts | JSONL-style note events under `~/.hermes/voice-live-notes/` by default |
| Idle handling | Idle prompts, grace hangup, and fallback auto-leave are env-driven |

---

## Sidecar HTTP API

Default bind:

```text
http://127.0.0.1:18943
```

> **Security status:** current `main` has a known auth-path crash in `bridge_http.py`. Requests reaching the secret comparison raise `NameError`, so the mutating routes below must not be treated as operational until [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) is fixed with exact-head tests. `/notes` is also intentionally unauthenticated in the current code and exposes recent transcript data; do not proxy or otherwise broaden access to the sidecar while [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) remains open.

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

## Documentation map

Current markdown docs:

- [`docs/gemini-live-implementation.md`](docs/gemini-live-implementation.md) — Gemini Live setup/audio/video/tools/reconnect details.
- [`docs/architecture.md`](docs/architecture.md) — architecture and lifecycle.
- [`docs/env-vars.md`](docs/env-vars.md) — code-grounded env var defaults.
- [`docs/quickstart.md`](docs/quickstart.md) — install and first session.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — operational failure modes.
- [`docs/video.md`](docs/video.md) — current frame-feeder and `/frame` status.

`docs-site/` is a static site snapshot. Treat the markdown docs and code as more current until the static site is regenerated.

---

## Migration plan: Gemini bridge → SORA bridge

The migration is intentionally gradual.

1. **Stabilize this repo** as the working Gemini Live Discord runtime.
2. **Document the truth** instead of preserving older marketing copy.
3. **Move orchestration into SORA**: provider selection, CLI control, API/TUI surface, MCP, VOIP, config, diagnostics.
4. **Transplant or wrap the working Gemini runtime** once SORA’s bridge layer is ready.
5. **Retire duplicate docs** once SORA owns the bridge surface cleanly.

Until step 4 is complete, do not assume `sora-agent` fully replaces this repo for live Discord/Gemini audio.

---

## Development notes

```bash
# Compile-check plugin files through the Hermes venv
~/.hermes/hermes-agent/venv/bin/python -m py_compile *.py

# Run installed regression tests if present
cd ~/.hermes/plugins/discord-voice
~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_interrupt_latency tests.test_transcript_latency -v
```

Do not run multiple competing Gemini/SORA voice plugins against the same Discord bot/channel unless you are intentionally testing conflicts.

---

## Related project

- [`Capslockb/sora-agent`](https://github.com/Capslockb/sora-agent) — the SORA Bridge / CLI / API / Hermes-plugin migration target.

---

## License

This repository does not currently contain a canonical `LICENSE` file. Do not assume MIT or other reuse rights from older generated documentation. The owner decision and follow-up work are tracked in [Issue #7](https://github.com/Capslockb/gemini-live-discord-bridge/issues/7).