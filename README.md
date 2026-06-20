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
| Hermes tools | `voice_live`, `voice_live_leave` |
| Gemini model default | `gemini-3.1-flash-live-preview`, with fallback list via env |
| Audio path | Discord 48 kHz stereo PCM → 16 kHz mono Gemini input → 24 kHz mono Gemini output → Discord 48 kHz stereo |
| Sidecar API | Local HTTP, default `127.0.0.1:18943`, configurable with `DISCORD_VOICE_LIVE_PORT` |
| Static docs site | Kept in `docs-site/`, but it is an older VOPI/static snapshot and may lag behind code |

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge

# 2. Install into Hermes
./install.sh                 # full install, prompts for env
./install.sh --from-local    # symlink current working copy for development
./install.sh --no-prompt     # use existing env values
./install.sh --uninstall     # remove plugin install

# 3. Restart Hermes gateway
systemctl --user restart hermes-gateway

# 4. From Discord, run:
/voice-live
/voice-live-leave
```

The installer expects the Hermes layout at:

```text
~/.hermes/hermes-agent/venv/bin/python
~/.hermes/plugins/discord-voice
~/.hermes/.env
```

---

## Required environment

Minimum required:

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***        # GOOGLE_API_KEY also works in code
```

Common optional settings:

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

See `docs/env-vars.md` for the older full list, but verify critical values against `bridge.py` and `plugin.yaml` until docs are regenerated.

---

## What this bridge actually does

| Feature | Current implementation |
|---|---|
| Full-duplex Discord voice | Receives Discord audio, streams PCM to Gemini Live, plays Gemini audio back into Discord |
| Gemini Live WebSocket | Uses Google Gemini Multimodal Live `BidiGenerateContent` WSS |
| Audio conversion | Discord 48 kHz stereo ↔ Gemini 16 kHz input / 24 kHz output |
| Video frames | Accepts still/video frames through the local sidecar and forwards them to Gemini |
| Tool calling | Handles local, web, Spotify, GitHub, Home Assistant, email, inspection, and delegation-style tools when configured |
| Honcho context | Optional per-user context injection into the system prompt |
| Proactive notification path | Local notification dispatcher and `/notify` sidecar route |
| Notes/transcripts | JSONL-style note events under the Hermes voice notes directory |
| Idle handling | Idle prompts and auto-leave behavior are env-driven |

---

## Sidecar HTTP API

Default bind:

```text
http://127.0.0.1:18943
```

Common routes used by the bridge/docs:

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Bridge health and metrics |
| `/frame` | POST | Submit a JPEG/PNG frame for Gemini vision |
| `/say` | GET/POST depending on caller path | Inject text into the live bridge |
| `/notes` | GET | Read recent note/transcript events |
| `/notify` | GET/POST | Send a proactive notification payload |
| `/stop` | GET/POST depending on handler path | Stop the active bridge |

The port is controlled by `DISCORD_VOICE_LIVE_PORT`.

---

## Documentation status

The old docs are still useful as background, but they are **not guaranteed to match the current code exactly**:

- [`docs-site/index.html`](docs-site/index.html) is a static site snapshot.
- [`docs/`](docs/) contains the source markdown used by that site.
- `bridge.py`, `plugin.yaml`, `install.sh`, and `requirements.txt` are the current source of truth.

Known drift to fix in a later docs pass:

- version labels differ between README, docs site, and `plugin.yaml`;
- some website copy still markets the VOPI build as the main release state;
- route names and env defaults need to be rechecked against the current code;
- SORA transition status was missing from the old README.

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

MIT.
