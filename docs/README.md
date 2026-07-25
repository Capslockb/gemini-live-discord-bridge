# docs/ — Gemini Discord voice bridge documentation

This directory documents the current Hermes `discord-voice` plugin and its Gemini Multimodal Live runtime.

Use this source-of-truth order when files disagree:

1. Current runtime code on `main`.
2. `README.md` and the Markdown files in `docs/`.
3. `docs-site/`, which is a generated static snapshot and may lag behind the code and Markdown documentation.

## Index

| Doc | What it covers |
|---|---|
| [`gemini-live-implementation.md`](gemini-live-implementation.md) | Gemini Live setup, audio, video, tools, interruption, and reconnect behavior |
| [`architecture.md`](architecture.md) | End-to-end architecture, threading, sidecar, and lifecycle |
| [`quickstart.md`](quickstart.md) | Installation and first-session setup |
| [`env-vars.md`](env-vars.md) | Runtime environment variables and defaults |
| [`troubleshooting.md`](troubleshooting.md) | Operational failure modes and diagnostics |
| [`personality.md`](personality.md) | System-prompt and conversational behavior |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation and fallback health handling |
| [`notification.md`](notification.md) | Local notifications, scheduled notifications, and `/notify` |
| [`email-brief.md`](email-brief.md) | Email brief tool and scheduler |
| [`sfx-library.md`](sfx-library.md) | Slot-based sound-effect library and configuration |
| [`sfx-credits.md`](sfx-credits.md) | Sound-effect provenance and rights notes |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, and webhook configuration |
| [`video.md`](video.md) | Frame input, video state, and feeder behavior |
| [`changelog.md`](changelog.md) | Documentation changelog; the repository changelog is `../CHANGELOG.md` |

## Current security and data-handling status

- The sidecar binds to `127.0.0.1:18943` by default and is not a public HTTP service.
- `/health` is anonymous and read-only.
- `/stop`, `/say`, `/frame`, and `/notify` are intended to require `X-API-Secret`, but the current `main` auth path is blocked by [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5). Treat those routes as unavailable until the repair is merged and validated.
- `/notes` is currently anonymous and returns recent stored note/transcript events. Keep the sidecar loopback-only and review [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) before exposing it through any proxy, browser-accessible route, or tunnel.
- The bridge does not create conventional audio recordings, but note/transcript events are persisted under `~/.hermes/voice-live-notes/` by default unless configuration changes that location or behavior.

## Quick reference

```bash
# Install
./install.sh

# Uninstall
./install.sh --uninstall

# Check anonymous bridge health
curl -s http://127.0.0.1:18943/health | jq

# Restart the Hermes gateway after plugin changes
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -f

# Use from Discord
/voice-live
/voice-live-leave
```

Do not copy older unauthenticated examples for `/say`, `/frame`, `/notify`, or `/stop`. After Issue #5 is resolved, clients must send the configured `X-API-Secret`; until then, those routes remain blocked on current `main`.

## Scope boundaries

- The sidecar is local control infrastructure, not a production web API.
- The repository is still the working Gemini Discord runtime while broader orchestration migrates toward `Capslockb/sora-agent`; SORA does not yet replace every live Discord/Gemini path documented here.
- Generated pages in `docs-site/` should be regenerated only after their source Markdown and generator behavior have been reviewed against the current code.
