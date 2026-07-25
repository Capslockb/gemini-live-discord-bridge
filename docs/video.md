# Video frame feeder

The repository includes `scripts/video-frame-feeder.py`, a companion screen-capture client for the bridge's `/frame` route. The intended flow is:

```text
local display → FFmpeg capture → authenticated loopback POST /frame → Gemini Live
```

Discord bots cannot consume a user's Discord screen share through this plugin. The feeder captures a real local display or window instead.

## Current status: blocked

Do not treat the installed feeder or `voice_live_frame` tool as operational on current `main`.

There are several connected blockers:

1. `scripts/video-frame-feeder.py` assigns argparse's reserved `-h` option to `--height`, so parser construction fails before `--help` or a capture can run.
2. Current `/frame` authentication fails in `bridge_http.py` before returning a controlled authorization result; this is tracked in [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5).
3. The external feeder sends no `X-API-Secret` header.
4. The built-in `voice_live_frame` client in `__init__.py` also sends no `X-API-Secret` header.
5. `install.sh` creates `~/.hermes/control.secret`, while the runtime reads `DISCORD_VOICE_LIVE_SECRET_FILE`, defaulting to `~/.hermes/voice-live-control-secret`. The installer-created file is not the runtime control secret.

The combined repair is tracked in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9). It requires separately reviewed executable and security-sensitive changes.

## Installation behavior

Running `install.sh` currently copies the feeder to:

```text
~/.hermes/scripts/video-frame-feeder.py
```

and marks it executable. This only installs the current script; it does not resolve the startup or authentication blockers above.

Manual copying is equivalent:

```bash
mkdir -p ~/.hermes/scripts
cp scripts/video-frame-feeder.py ~/.hermes/scripts/video-frame-feeder.py
chmod 0755 ~/.hermes/scripts/video-frame-feeder.py
```

## Intended local usage after repair

The sidecar currently binds only to `127.0.0.1`, so the supported endpoint boundary is local to the bridge host:

```bash
python3 ~/.hermes/scripts/video-frame-feeder.py \
  --endpoint http://127.0.0.1:18943/frame \
  --source-label my-display
```

Do not use a Tailscale, LAN, proxy, or browser-accessible endpoint with the current server. The sidecar is loopback-only by design, and `/notes` remains unauthenticated transcript data while [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) is open.

After the repair, clients must obtain the canonical control secret without printing or logging it and attach it as `X-API-Secret`. Do not copy `~/.hermes/control.secret` as a workaround; that is not the runtime's default secret file.

## Current CLI definition

These are the values currently declared in the script, although the `-h` conflict prevents normal CLI startup:

| Flag | Current behavior |
|---|---|
| `--endpoint` | Defaults to `VOICE_BRIDGE_FRAME_URL` or `http://127.0.0.1:18943/frame`. |
| `--interval` | Capture-attempt interval; values below 1 second are clamped to 1 second. |
| `--source` | Defaults to `screen`; may be a window title or X11 window ID. |
| `--width`, `-w` | Capture width; default `768`. |
| `--height` | Intended capture height; default `768`. The current `-h` alias conflicts with argparse help. |
| `--min-change` | Hamming-distance threshold; default `2`. The advertised `0–64` range is not currently enforced. |
| `--stddev-min` | Minimum 8×8 pixel standard deviation; default `0`, which disables uniform-frame rejection. The advertised `0–255` range is not currently enforced. |
| `--no-content-filter` | Disables hash and standard-deviation filtering. |
| `--source-label` | Added to the request as the URL-encoded `source` query parameter, not an HTTP header. Defaults to `--source`. |
| `--force` | Adds `force=true`, bypassing the bridge's recent-audio gate. |
| `--once` | Performs one capture attempt and exits; filtering can skip delivery. |

## Filtering pipeline

When filtering is enabled, the script:

1. captures an 8×8 grayscale thumbnail as exactly 64 raw bytes;
2. computes an average hash and pixel standard deviation;
3. skips the frame when it is below the configured content/change thresholds;
4. captures the full JPEG only after the thumbnail passes;
5. submits the JPEG to `/frame`.

The script currently advances its remembered hash before full-frame capture or bridge acceptance, so that value means "last content selected for a send attempt," not necessarily "last accepted frame."

## Validation required after repair

A reviewed fix should demonstrate all of the following on the exact commit:

- `--help` and a one-shot parser invocation start successfully;
- the feeder and `voice_live_frame` attach the canonical secret without exposing it in arguments, output, or logs;
- missing and incorrect secrets return controlled `401` responses;
- a correct secret reaches normal `/frame` validation;
- `source` and `force` query parameters remain encoded correctly;
- local loopback remains the default network boundary;
- tests use stubbed capture and HTTP paths and do not contact Gemini, Discord, Tailscale, or real displays.

## See also

- [`quickstart.md`](quickstart.md)
- [`architecture.md`](architecture.md)
- [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) — sidecar and transcript privacy hardening
- [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) — mutating-route authentication repair
- [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) — frame-delivery and feeder repair