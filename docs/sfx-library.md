# SFX library — multi-slot UI sound effects

A small slot-based system for playing UI sound effects into the active voice session. Each slot maps to a WAV file that is converted to 24 kHz mono PCM16 before playback and fires on a specific bridge event.

## The four slots

| Slot | Triggered by | Typical sound |
|---|---|---|
| `tool_init` | First tool call of a session (one-shot per session) | Soft chime — "I'm ready to work" |
| `error` | Uncaught exception in `_run_local_tool` | Sharp beep — "something went wrong" |
| `notification` | Successful `local_notify` delivery, including email brief delivery | Light ping — "you have a message" |
| `transition` | Session start after `vc.play()` succeeds | Pop/swoosh — "we're connected" |

The `tool_init` SFX uses a one-shot guard (`_run_local_tool._tool_init_played`) so it does not replay on every tool call.

## File layout

Default runtime directory: `~/.hermes/voice-users/sfx/`

```text
~/.hermes/voice-users/sfx/
├── tool_init.wav
├── error.wav
├── notification.wav
└── transition.wav
```

The repository also contains files under `sfx/`, and `install.sh` copies a bundled file into the runtime directory only when the corresponding destination file is missing. The current redistribution rights for those bundled files are not verified; see [`sfx-credits.md`](sfx-credits.md) and [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12). Use your own explicitly licensed files or disable SFX until that issue is resolved.

Files do not need to arrive in the target playback format. The loader accepts supported WAV inputs and converts them to 24 kHz mono PCM16. Supplying that format directly avoids the simple in-process resampling path.

## Environment variables

Per-slot path override:

```bash
DISCORD_VOICE_LIVE_SFX_TOOL_INIT=/path/to/custom_chime.wav
DISCORD_VOICE_LIVE_SFX_ERROR=/path/to/custom_beep.wav
DISCORD_VOICE_LIVE_SFX_NOTIFICATION=/path/to/custom_ping.wav
DISCORD_VOICE_LIVE_SFX_TRANSITION=/path/to/custom_pop.wav
```

Per-slot volume, clamped by the runtime to `0.0` through `1.5` where `1.0` means no scaling:

```bash
DISCORD_VOICE_LIVE_SFX_TOOL_INIT_VOLUME=0.55
DISCORD_VOICE_LIVE_SFX_ERROR_VOLUME=0.45
DISCORD_VOICE_LIVE_SFX_NOTIFICATION_VOLUME=0.50
DISCORD_VOICE_LIVE_SFX_TRANSITION_VOLUME=0.60
```

Global enable:

```bash
DISCORD_VOICE_LIVE_SFX_ENABLED=true    # default
```

Set this to `false` to prevent bundled or locally configured SFX from loading or playing.

Global SFX directory, overriding `~/.hermes/voice-users/sfx/`:

```bash
DISCORD_VOICE_LIVE_SFX_DIR=/custom/sfx/dir
```

## `local_sfx_test` tool

The agent can play a slot in the current voice session or inspect the configured slots:

```json
{"slot": "notification"}
```

```json
{"action": "list"}
```

A play result resembles:

```json
{"result": {"status": "played", "slot": "notification", "bytes": 33600, "duration_s": 0.7}}
```

A list result reports each slot's resolved path, existence, volume, and cached byte count.

If no voice session is active, playback returns `{"status": "no_active_source"}`. Missing or unsupported files return a controlled no-SFX result rather than making the voice bridge fail.

## Adding a new slot

1. Add `<slot>.wav` to the configured SFX directory, or set `DISCORD_VOICE_LIVE_SFX_<SLOT>` to an explicit path.
2. In `sfx.py`, add the slot name to `DEFAULT_SFX_PATHS` and `DEFAULT_SFX_VOLUMES`.
3. Call `play_sfx("<slot>")` from the bridge event that should trigger it.
4. Add the slot to the `local_sfx_test` declaration and runner behavior.
5. Restart the Hermes gateway after code changes.

Adding or replacing media in the repository is an asset/licensing-sensitive change. Do not assume the repository software license grants rights to a third-party WAV.

## Cache invalidation

The first successful load of a slot is cached in memory. Replacing a WAV at the same path does not change the active cached bytes.

To pick up a replacement:

- restart the Hermes gateway; or
- call `invalidate_cache()` from reviewed code.

There is currently no public cache-invalidation tool, so a normal operator should restart the gateway after replacing a file.