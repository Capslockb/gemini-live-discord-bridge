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

# Skip credential prompts only; required values must already exist
./install.sh --no-prompt

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

A plain `./install.sh` clones into `${HERMES_HOME:-$HOME/.hermes}/plugins/discord-voice` only when that path does not already exist. On an existing installation it prints `skipping clone` and continues against the existing tree without fetching or checking its revision, so it is **not an update command**. Use `--from-local` when you need the exact current checkout. `--no-prompt` only bypasses the prompts; it does not verify that the required credentials exist before reporting installation completion. See [Issue #11](https://github.com/Capslockb/gemini-live-discord-bridge/issues/11).

Current interactive credential writes do not preserve every secret value literally: `&`, `|`, and backslashes can be altered by the installer's `sed` replacement path. Until [Issue #23](https://github.com/Capslockb/gemini-live-discord-bridge/issues/23) is fixed, do not enter credentials containing those characters through the prompts. Populate `${HERMES_HOME:-$HOME/.hermes}/.env` through a trusted editor or secret-management path, keep its permissions restrictive, and then use `--no-prompt`. Never put real secrets in command-line arguments or shell history.

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
5. stream audio in both directions until `/voice-live-leave` or idle hangup.
