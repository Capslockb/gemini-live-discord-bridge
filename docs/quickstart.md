# Quick start

Install the current Gemini Live Discord voice bridge into Hermes and start one live session.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge

# 2. Install — prompts for DISCORD_BOT_TOKEN and GEMINI_API_KEY
./install.sh

# Development install from current working tree
./install.sh --from-local

# Non-interactive install using existing env
./install.sh --no-prompt

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

## Minimum env

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***        # GOOGLE_API_KEY also works in code
```

Recommended for slash-command inference and user-presence handling:

```bash
DISCORD_VOICE_LIVE_USER_ID=123456789012345678
DISCORD_VOICE_LIVE_VOICE=Kore
DISCORD_VOICE_LIVE_PORT=18943
```

Full env reference: [`env-vars.md`](env-vars.md).

## First session

From Discord, join a voice channel, then run:

```text
/voice-live
/voice-live-leave
```

The bridge will:

1. connect to your voice channel through `discord-ext-voice-recv`;
2. create the Discord RX sink and TX audio source;
3. open the Gemini Live WebSocket;
4. send the Gemini `setup` payload and wait for `setupComplete`;
5. send an immediate empty `audioStreamEnd` to suppress unwanted first-turn generation;
6. wait for voice input, unless `DISCORD_VOICE_LIVE_GREETING` is configured.

The current code default for `DISCORD_VOICE_LIVE_GREETING` is `I'm here.`. Set it empty if you want a fully silent connection.

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

You should see values like:

```json
{
  "status": "ok",
  "running": true,
  "voice_connected": true,
  "receiving_active": true,
  "configured_model": "gemini-3.1-flash-live-preview"
}
```

After you speak, `audio_in_chunks` and transcript-related counters should begin increasing if transcription is working.

## Manual image frame test — currently blocked

Current `main` cannot complete the mutating-route authentication comparison: requests to `/frame`, `/say`, `/notify`, and `/stop` fail before the intended authorization result is returned. Do not use these routes while [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5) remains open.

After the authentication fix is merged and validated on its exact head, the intended frame-test procedure is:

```bash
SECRET=$(cat ~/.hermes/voice-live-control-secret)
curl -s \
  -H "X-API-Secret: $SECRET" \
  -H "Content-Type: image/jpeg" \
  --data-binary @frame.jpg \
  "http://127.0.0.1:18943/frame?force=true&source=manual" | python3 -m json.tool
```

Then check:

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

Look for `video_in_frames`, `video_sent_frames`, `video_dropped_frames`, and `video_last_reason`.

## Common pitfalls

- **Bridge seems slow to start** — let one connect cycle finish. Restarting the gateway repeatedly can make Discord voice/CDN retries worse.
- **No `/voice-live` target channel found** — join a Discord voice channel first and set `DISCORD_VOICE_LIVE_USER_ID` to your Discord snowflake.
- **`/frame`, `/say`, `/notify`, or `/stop` fail on current `main`** — this is the known authentication blocker in Issue #5, not evidence that the supplied secret is merely wrong.
- **Need recent transcript notes** — `/notes` is currently unauthenticated and exposes recent transcript/note events; keep the sidecar loopback-only and do not proxy it while [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4) remains open.
- **Unexpected greeting** — set `DISCORD_VOICE_LIVE_GREETING=` if you want no text injected after setup.
- **No inbound audio** — verify `discord-ext-voice-recv` installed, `receiving_active=true`, and the user is not filtered by `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS`.

## Next

- [`gemini-live-implementation.md`](gemini-live-implementation.md) — exact Gemini Live setup/audio/video/tool behavior.
- [`architecture.md`](architecture.md) — bridge lifecycle and runtime objects.
- [`env-vars.md`](env-vars.md) — current env defaults.
- [`troubleshooting.md`](troubleshooting.md) — operational failures and fixes.
