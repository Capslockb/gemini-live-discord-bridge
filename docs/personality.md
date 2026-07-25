# Personality — system prompt, ping-pong rhythm, boredom switch

The source of truth for the system prompt is `bridge_config.py:BASE_SYSTEM_PROMPT`. The compatibility facade in `bridge.py` re-exports that constant for existing imports. The prompt is **not** a documentation file — it is a set of behavioral contracts the model is told to follow.

## Sections of the prompt (in order)

1. **Identity** — "You are S0RA, the AI companion of Capslockb (he calls you B)."
2. **Capabilities** — Spotify, web search, Gmail, Home Assistant, and video awareness when an authenticated frame is actually available. The prompt advertises video capability, but the bundled frame paths are blocked on current `main`; see [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9).
3. **VIDEO / SCREEN-SHARE guard** — strict conditional: "Only describe video you have actually received in the current turn."
4. **FIRST-TURN BEHAVIOUR** — "do NOT generate any audio. Wait for the user to speak first."
5. **PINGPONG RHYTHM** — split into question rounds and development rounds.
6. **FORMAT & ANSWER SHAPE** — answer first, then bullets; emotion is seasoning not the meal.
7. **CALL-OUT MODE** — puncture nonsense, move the work forward.
8. **PROACTIVE TOOL USE** — suggest tools before being asked.
9. **PROACTIVE ENGAGEMENT** — drive the conversation; if it's stalling, SAY IT.
10. **BOREDOM SWITCH** — escalate into NAG MODE if the chat drags.
11. **EDGE & COMEDY** — push boundaries, match B's dry sarcastic style.
12. **GF STATE / BOREDOM** — when B is checked out, shift energy: games, music, random maintenance.
13. **VOCAL EXPRESSION** — at most one inline speech tag per reply.
14. **TOOL BEHAVIOUR** — typing sound is normal, don't apologize for tool use.

## Why the prompt is **so** long

Each section addresses a specific regression observed in earlier sessions. The model collapses to "polite assistant" if any one of them is missing.

| Section | Regression it fixes |
|---|---|
| VIDEO guard | Prevents screen-share hallucination; it does not prove that either current frame client delivered an image. |
| FIRST-TURN | First-turn token burn |
| PINGPONG | Monologue-style lectures when the question is still fuzzy |
| FORMAT | "Just laughing and not formatting answers" — emotion replacing substance |
| CALL-OUT | Hand-waving gets rubber-stamped instead of challenged |
| PROACTIVE TOOL | Tools forgotten unless prompted |
| PROACTIVE ENGAGEMENT | Long pauses with no nudge to keep moving |
| BOREDOM SWITCH | Stalls silently instead of escalating |
| VOCAL EXPRESSION cap | "<laugh> <laugh> <laugh>" spam |

## How to edit the prompt

Edit `BASE_SYSTEM_PROMPT` in `bridge_config.py`. Do not edit only the `bridge.py` facade; it contains compatibility imports, not the prompt definition. After editing:

1. Compile-check both the source module and facade: `python -m py_compile bridge_config.py bridge.py`
2. Restart the gateway: `systemctl --user restart hermes-gateway`
3. Test by joining voice and triggering the relevant behavior.

For video behavior, prompt text alone is not validation. Do not describe the bundled `/frame` clients as operational until the startup and authentication work in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9) is fixed and tested.

**Do not** add hedging like "be helpful and harmless" — the model interprets that as permission to revert to assistant defaults.

## Honcho context injection

The static prompt can be appended with a per-session "Honcho context" block. The environment variable is `VOICE_LIVE_HONCHO_CONTEXT` (default `true`); `HONCHO_CONTEXT_ENABLED` is the corresponding Python constant, not an environment-variable name. Context is omitted when the feature is disabled, configuration is unavailable, the selected peer cannot be resolved, or the backend request fails.

When available, the block contains:

- The selected Honcho peer's representation.
- Conclusions from that peer's card, rendered as known facts.
- At most `VOICE_LIVE_HONCHO_MAX_CHARS` characters (default `1200`).

The Honcho block is **dynamic** and may vary by selected peer and session, while `BASE_SYSTEM_PROMPT` is static. Peer selection may be provided per user by the caller; otherwise it falls back through Honcho configuration, `VOICE_LIVE_HONCHO_PEER`, `HONCHO_PEER_NAME`, and the configured Discord user ID.