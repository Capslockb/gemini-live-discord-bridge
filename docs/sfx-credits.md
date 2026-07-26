# SFX credits and provenance

The repository currently includes four derived WAV files under `sfx/`. The installer can copy those files into `~/.hermes/voice-users/sfx/` when the corresponding local slots are empty.

The source videos and processing notes below establish provenance only. The repository does **not** currently include the source license text, archived permission evidence, or another clear grant showing that the derived clips may be redistributed with this project. Attribution, short duration, resampling, gain changes, fading, or looping do not by themselves establish redistribution rights.

Treat the bundled clips as **rights-unverified** until [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12) is resolved. This media-rights question is separate from the repository software-license decision in [Issue #7](https://github.com/Capslockb/gemini-live-discord-bridge/issues/7).

## Recorded source provenance

Playlist: **"UI Sound Effects for App & Game Development"** by **Brand Name Audio** on YouTube  
URL: https://www.youtube.com/playlist?list=PLOK_EJ2O31LrGG7HvPiMeIsEiq4Wg6j-U  
Recorded access date: 2026-06-07

The playlist title, publisher name, video IDs, and access date are not evidence of a particular license or permission grant.

## Specific source videos

| Slot | YouTube ID | Recorded title | Processing note |
|---|---|---|---|
| `tool_init` | `oYS1Qg98QTg` | `UI Notification CHIMES PACK` | First chime near `1.96s`, located with `silencedetect` |
| `error` | `1QweURriLQA` | `Loud Beep Sound Effects (UI User Interface)` | First beep near `1.00s`, repeated into an approximately `2.8s` alert |
| `notification` | `XhLOi8C7FLc` | `iPhone Android UI / UX Ringtones` | Short notification-style segment |
| `transition` | `x8njWIqFKms` | `The BEST POP Sound Effects` | First pop near `1.91s`, with gain adjustment |

## Recorded processing steps

1. Downloaded audio with `yt-dlp`.
2. Located candidate attacks with FFmpeg `silencedetect`.
3. Cut short windows around the selected sounds.
4. Repeated the error clip to create a longer alert.
5. Resampled to 24 kHz mono PCM16.
6. Applied gain adjustments where needed.
7. Applied a short fade-out.

These transformations describe how the repository files were produced; they do not determine copyright ownership or licensing.

## Current operator guidance

Until the rights status is verified:

- use WAV files you created yourself or obtained under explicit terms that permit this use and redistribution;
- configure replacements with `DISCORD_VOICE_LIVE_SFX_<SLOT>` or a separate `DISCORD_VOICE_LIVE_SFX_DIR`;
- or disable SFX with `DISCORD_VOICE_LIVE_SFX_ENABLED=false`.

Do not assume that a future root software license will automatically cover third-party audio files. If the bundled files are retained, each file needs an auditable source, license or permission record, required attribution, and redistribution basis. Otherwise the files should be replaced or removed through a separately reviewed media/release change.