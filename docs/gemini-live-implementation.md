# Gemini Live implementation notes

This page documents how this repository currently talks to Gemini Live. It is based on `bridge_core.py`, `bridge_audio.py`, `bridge_http.py`, `bridge_config.py`, `bridge_decls.py`, `bridge_tools.py`, and `__init__.py`, plus the Google Live API documentation checked on 2026-06-20.

Official references:

- Gemini Live overview: https://ai.google.dev/gemini-api/docs/live-api
- Live API WebSocket reference: https://ai.google.dev/api/live

## What this bridge is

This repo is a **server-to-server WebSocket bridge** between Discord voice and Gemini Live.

```text
Discord voice channel
  -> discord-ext-voice-recv decoded PCM
  -> local speech gate / downsample
  -> Gemini Live WebSocket realtimeInput.audio
  -> Gemini serverContent audio output
  -> Discord AudioSource playback
```

It is not a browser WebRTC client. The Discord bot stays inside the Hermes gateway process. Bridge lifecycle and model coordination run in `bridge_core.py`, audio receive/playback lives in `bridge_audio.py`, and the local HTTP sidecar lives in `bridge_http.py`.

## Gemini Live protocol shape

The code connects to:

```text
wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=$GEMINI_API_KEY
```

The Live API is stateful over WebSocket. The first client message is a single `setup` payload. After setup completes, the client sends `realtimeInput` and `toolResponse` messages. The server can send `setupComplete`, `serverContent`, `toolCall`, `toolCallCancellation`, `goAway`, and `sessionResumptionUpdate`.

This repo uses raw WebSockets instead of the GenAI SDK so the bridge can stay close to Discord's receive/playback loop and Hermes plugin lifecycle.

## Setup payload used by the code

`bridge_core.GeminiLiveBridge._connect_model()` builds a setup payload with these important fields:

- `model`: sent as `models/<selected-model>`.
- `generationConfig.responseModalities`: `AUDIO` only.
- `generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`: default voice is `Kore`.
- `realtimeInputConfig.activityHandling`: `START_OF_ACTIVITY_INTERRUPTS` for barge-in.
- `realtimeInputConfig.turnCoverage`: `TURN_INCLUDES_ONLY_ACTIVITY`.
- `realtimeInputConfig.automaticActivityDetection`: enabled, with low start/end sensitivity, `prefixPaddingMs=0`, and `silenceDurationMs=40`.
- `inputAudioTranscription` and `outputAudioTranscription`: enabled.
- `systemInstruction`: base system prompt plus optional Honcho context and per-user overrides.
- `tools`: Live API function declarations grouped by feature area and filtered by per-user allowlist when available.

`mediaResolution` is intentionally omitted. Code comments note that the current model lineup rejects the field even though the broader API reference lists it in the general generation config shape.

Top-level `voice_activity_detection` is also intentionally omitted. VAD tuning lives under `realtimeInputConfig.automaticActivityDetection`.

## Model selection and fallback

The bridge first tries:

```bash
GEMINI_MODEL=gemini-3.1-flash-live-preview
```

Then it appends unique entries from:

```bash
GEMINI_LIVE_MODEL_FALLBACKS=gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025,gemini-2.5-flash-native-audio-preview-09-2025
```

If setup fails for a candidate, the WebSocket is closed and the next model is tried. If every candidate fails, startup fails with `No Gemini Live model could start`.

## Audio input path

`bridge_audio.GeminiPCMSink` receives decoded Discord PCM from `discord-ext-voice-recv` because `wants_opus()` returns `False`.

The expected Discord-side input is:

```text
48 kHz stereo PCM16, 20 ms frames
```

The bridge then:

1. ignores unknown users and bots;
2. optionally filters by `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS`;
3. runs a fast peak-amplitude speech gate on the 48 kHz stereo frame;
4. downsamples to 16 kHz mono;
5. queues the 16 kHz mono PCM for Gemini.

Gemini receives:

```json
{"realtimeInput": {"audio": {"data": "<base64>", "mimeType": "audio/pcm;rate=16000"}}}
```

The Live API overview documents raw PCM16 16 kHz little-endian audio input and raw PCM16 24 kHz little-endian audio output. The bridge matches that shape at the Gemini boundary.

## Audio output path

Gemini returns audio in `serverContent.modelTurn.parts[].inlineData` where the MIME type starts with `audio/pcm`.

The bridge:

1. base64-decodes the PCM chunk;
2. optionally adds pre-roll silence when a model turn opens;
3. optionally fades in the chunk;
4. feeds 24 kHz mono PCM into `bridge_audio.LiveAudioSource`;
5. upsamples to Discord's 48 kHz stereo PCM in `LiveAudioSource.read()`;
6. lets Discord encode/play the source into the voice channel.

Current output timing knobs:

```bash
DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS=320
DISCORD_VOICE_LIVE_OUTPUT_FADE_IN_MS=0
DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS=0.005
DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS=240
DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT=true
```

## Barge-in and interruption behavior

The Live API supports `START_OF_ACTIVITY_INTERRUPTS`, also called barge-in. The bridge enables it in `realtimeInputConfig.activityHandling`.

The code also adds a local fast path: when user speech energy is detected while model output is open, the Discord output queue is cleared locally before waiting for Gemini's server-side `interrupted` event. That is the load-bearing fix for snappy interruption.

When the server later sends `serverContent.interrupted`, the output queue is also cleared if `DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT=true`.

## Turn ending and first-turn mute

During normal input, the send loop tracks the last audio chunk time. If no audio has been sent for `GEMINI_AUDIO_STREAM_IDLE_END_SECONDS`, default `0.25`, it sends:

```json
{"realtimeInput": {"audioStreamEnd": true}}
```

Google documents `audioStreamEnd` as the marker that the audio stream ended while automatic activity detection is enabled. The client can reopen the stream by sending more audio.

After `setupComplete`, `bridge_core.VoiceLiveBridge.start()` also sends an immediate empty `audioStreamEnd` to suppress unwanted first-turn generation.

Caveat: the code default for `DISCORD_VOICE_LIVE_GREETING` is currently `I'm here.`. If you want a silent connection, set it empty in the environment.

## Video / frame input

The bridge accepts JPEG, PNG, and WebP frames through `bridge_core.GeminiLiveBridge.feed_video_frame()` and the `bridge_http.py` sidecar `/frame` route.

Important limits:

```bash
DISCORD_VOICE_LIVE_VIDEO_ENABLED=true
DISCORD_VOICE_LIVE_VIDEO_MAX_FPS=1
DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES=524288
DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS=8
```

Frames are dropped when video is disabled, the MIME type is unsupported, the payload is too large, the 1 fps cap is exceeded, or no recent voice activity exists and `force=false`.

Accepted frames are queued as:

```json
{"realtimeInput": {"video": {"data": "<base64>", "mimeType": "image/jpeg"}}}
```

## Tool calls

The server can send `toolCall` messages containing `functionCalls`. Declarations are assembled from `bridge_decls.py`; execution is dispatched through `bridge_tools.py`; matching `toolResponse` messages are returned with the original call IDs.

Tool groups currently include Spotify, web, local utilities, Home Assistant, OpenCode/delegation, system inspection, GitHub, email, email brief, and onboarding/profile tools.

Tool execution is dispatched through the event loop executor so blocking work does not freeze the WebSocket receive loop.

`toolCallCancellation` is logged but not deeply acted on. If a tool has already caused side effects, the current code does not attempt rollback.

## Session resumption and reconnect

The code watches for:

- `sessionResumptionUpdate`
- `goAway`
- WebSocket close code `1008`

On `goAway` or receive errors, `_restart()` disconnects, backs off exponentially up to 30 seconds, reconnects, resets queues, and recreates the PCM receive sink.

Caveat: the code stores a resumption handle when the server sends one, but treat this as partial resumption plumbing unless a future patch explicitly wires `sessionResumption.handle` into setup.

## Sidecar HTTP API

The bridge starts a localhost sidecar on:

```text
127.0.0.1:${DISCORD_VOICE_LIVE_PORT:-18943}
```

Routes on current `main`:

| Route | Intended auth | Current status |
|---|---|---|
| `/health` | none | Available for local health/metrics checks |
| `/notes?limit=N` | none | Available, but exposes transcript/note events and the notes-file path to any local caller |
| `/stop` | `X-API-Secret` | **Blocked by Issue #5:** the auth path raises before it can return a controlled response |
| `/say?text=...` | `X-API-Secret` | **Blocked by Issue #5** |
| `/frame` | `X-API-Secret` | **Blocked by Issue #5** |
| `/notify` | `X-API-Secret` | **Blocked by Issue #5** |

The secret is generated in `__init__.py` and persisted by default at:

```bash
DISCORD_VOICE_LIVE_SECRET_FILE=~/.hermes/voice-live-control-secret
```

The code intends to leave `/health` and `/notes` anonymous and protect mutating routes with `X-API-Secret`, but that policy is not currently operational for the mutating routes. Keep the sidecar bound to loopback, do not proxy or browser-expose it, and treat `/notes` as transcript-sensitive until the authentication and Host/Origin findings in Issues #4 and #5 are resolved.

## Documentation footguns fixed by this page

Older docs claimed or implied a few things that no longer match code:

- the modular implementation now lives primarily in `bridge_core.py`, `bridge_audio.py`, `bridge_http.py`, `bridge_decls.py`, and `bridge_tools.py`; `bridge.py` is a compatibility facade;
- default voice is `Kore`, not `en-US-JennyNeural`;
- `DISCORD_VOICE_LIVE_USER_ID` currently falls back to a repository-embedded Discord account. Configure the intended user explicitly for slash-command inference, or provide explicit `guild_id` plus `channel_id` for tool calls that do not need user inference; do not treat the embedded value as a portable default. The canonical runtime and installer fix is tracked in `Capslockb/hermes-live-discord-agent-plugin#18`, with this repository's Issue #16 retained as mirror/provenance status;
- `KEEP_AUTOSTART_FILE` defaults true in code;
- mutating sidecar routes are currently unavailable on `main` because their intended authentication path is blocked by Issue #5;
- `/notes` is unauthenticated and returns persisted transcript/note data, so loopback binding is a required interim boundary;
- `voice_live_status`, `voice_live_frame`, `voice_live_video_status`, and `voice_live_notes` exist as Hermes tools, not just `voice_live` and `voice_live_leave`;
- session resumption is only partial plumbing right now.
