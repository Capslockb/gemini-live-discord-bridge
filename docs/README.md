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
| [`personality.md`](personality.md) | Public-safe conversational behavior, editing guidance, and prompt-disclosure boundaries |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation and fallback health handling |
| [`notification.md`](notification.md) | Local notifications, scheduled notifications, and `/notify` |
| [`email-brief.md`](email-brief.md) | Email brief tool and scheduler |
| [`sfx-library.md`](sfx-library.md) | Slot-based sound-effect library, configuration, and current asset boundary |
| [`sfx-credits.md`](sfx-credits.md) | Recorded sound-effect provenance and unresolved redistribution-rights status |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, and webhook configuration |
| [`video.md`](video.md) | Frame input, video state, and feeder behavior |
| [`changelog.md`](changelog.md) | Documentation changelog; the repository changelog is `../CHANGELOG.md` |

## Current security, privacy, rights, and protocol status

- The sidecar binds to `127.0.0.1:18943` by default and is not a public HTTP service.
- `/health` is anonymous and read-only.
- `/stop`, `/say`, `/frame`, and `/notify` are intended to require `X-API-Secret`, but the current `main` auth path is blocked by [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5). Treat those routes as unavailable until the repair is merged and validated.
- `/notes` is currently anonymous and returns recent stored note/transcript events. Keep the sidecar loopback-only and review [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) before exposing it through any proxy, browser-accessible route, or tunnel.
- Several voice-reachable tools remain security-sensitive under [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4): fallback web extraction lacks private/special-address and redirect revalidation, system inspection uses a string-prefix path check, `local_calc` permits unbounded exponentiation, and `local_email_send` has no independent recipient confirmation or allowlist. Do not expose these tools to untrusted speech or autonomous external delivery until the relevant boundary and exact-head tests are implemented.
- `_format_response()` calculates `Content-Length` from a Python string before UTF-8 encoding, so the response builder is not byte-correct for direct multi-byte Unicode bodies. Current reviewed route bodies use default `json.dumps(...)`, which normally ASCII-escapes non-ASCII data; routine transcript or emoji values have therefore not been shown to truncate every current route response. Direct Unicode strings, `ensure_ascii=False`, and future callers remain unsafe until [Issue #13](https://github.com/Capslockb/gemini-live-discord-bridge/issues/13) is fixed and tested.
- `DISCORD_VOICE_LIVE_USER_ID` currently falls back to a repository-embedded Discord account. `/voice-live`, `/voice-live-leave`, and the `voice_live` tool can therefore infer a channel or guild from that account when explicit caller or channel identifiers are absent. Configure the intended user explicitly or pass `guild_id` plus `channel_id`; do not treat the embedded value as a portable default. The canonical runtime, installer, and identity-routing implementation is tracked in [`Capslockb/hermes-live-discord-agent-plugin#18`](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18); [this repository's Issue #16](https://github.com/Capslockb/gemini-live-discord-bridge/issues/16) is the mirror/provenance surface.
- `VOICE_OWNER_DISCORD_ID` also has a repository-embedded fallback. A matching profile can receive persisted `is_owner: true` authorization and expanded destructive or inspection tool access, and changing or unsetting the environment variable does not demote an already elevated profile YAML. Treat `~/.hermes/voice-users/*.yaml` as security-sensitive authorization state and review it explicitly when changing owners. The canonical runtime and migration implementation is tracked in [`Capslockb/hermes-live-discord-agent-plugin#18`](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18); [this repository's Issue #17](https://github.com/Capslockb/gemini-live-discord-bridge/issues/17) is the mirror/provenance surface and must not become an independent divergent authorization fix.
- The bridge does not create conventional audio recordings, but note/transcript events are persisted under `~/.hermes/voice-live-notes/` by default unless configuration changes that location or behavior.
- Email briefs can currently mask total backend failure as an empty inbox, advance de-duplication after failed delivery, and report `notified: true` without successful delivery. The scheduler also has recipient-routing and model-visible snippet privacy blockers. Keep scheduled briefs disabled unless the destination and data boundary are explicitly verified, and see [Issue #10](https://github.com/Capslockb/gemini-live-discord-bridge/issues/10).
- The repository includes derived WAV files whose provenance is recorded but whose redistribution permission has not been verified. Use your own explicitly licensed files or set `DISCORD_VOICE_LIVE_SFX_ENABLED=false` while [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12) remains open. The eventual root software license tracked in [Issue #7](https://github.com/Capslockb/gemini-live-discord-bridge/issues/7) must not be treated as granting rights to third-party media.
- Public documentation must describe observable behavior and supported configuration without reproducing verbatim system prompts, privileged control grammar, deployment-specific identity data, or tool-authorization instructions. See [`personality.md`](personality.md).

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
- Shared or mirrored identity and authorization code must have one canonical implementation owner and an explicit import, archive, or tested-backport policy; do not maintain independent security fixes in duplicate files.
- Generated pages in `docs-site/` should be regenerated only after their source Markdown and generator behavior have been reviewed against the current code.
- Generated docs and release artifacts must not make unsupported software-license or third-party-media rights claims.