# Quick start

Install the current Gemini Live Discord voice bridge into Hermes and start one live session.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge

# 2. Fresh remote install — prompts for DISCORD_BOT_TOKEN and GEMINI_API_KEY
./install.sh

# Development install from this exact working tree
./install.sh --from-local

# Skip prompts; required credentials must already exist
./install.sh --no-prompt

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

A plain `./install.sh` is a fresh-install command, not an updater. If `${HERMES_HOME:-$HOME/.hermes}/plugins/discord-voice` already exists, the installer refuses to continue, reports the existing path type, and reports the Git revision plus clean/modified worktree state when available. It does not fetch, reset, delete, or overwrite that installation. `--from-local` preserves a symlink that already targets the current checkout and refuses conflicting paths. `--no-prompt` validates non-empty `DISCORD_BOT_TOKEN` and either `GEMINI_API_KEY` or `GOOGLE_API_KEY` before installation mutation. See [Issue #11](https://github.com/Capslockb/gemini-live-discord-bridge/issues/11).

`HERMES_HOME` relocates the plugin directory, Hermes environment file, Python virtual environment lookup, and installed video feeder. It does **not** currently relocate the SFX directory: `install.sh` still copies sound files to `$HOME/.hermes/voice-users/sfx`, while the runtime honors `DISCORD_VOICE_LIVE_SFX_DIR`. For a custom Hermes root, set `DISCORD_VOICE_LIVE_SFX_DIR="$HERMES_HOME/voice-users/sfx"` and populate that directory explicitly. See [Issue #18](https://github.com/Capslockb/gemini-live-discord-bridge/issues/18) and [`sfx-library.md`](sfx-library.md).

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
