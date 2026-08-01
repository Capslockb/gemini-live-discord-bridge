# Changelog — gemini-live-discord-bridge

> **Status notice:** This changelog is a public release summary, not evidence that every feature is production-ready. Review the linked open issues, current source, pull-request diff, review threads, and exact-head CI before enabling control, notification, video, email, networking, tool-execution, or bundled-media features.
>
> The repository does not currently contain a canonical owner-approved `LICENSE` file. See [Issue #7](https://github.com/Capslockb/gemini-live-discord-bridge/issues/7). Bundled SFX rights are a separate unresolved question tracked in [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12).

## 0.3.5 — 2026-06-09

Repository metadata identifies the current plugin version as `0.3.5`.

### Added and documented

- Modular Gemini Live ↔ Discord voice transport.
- Local loopback control sidecar.
- Delegation fallback plumbing.
- Notifications and scheduled notifications.
- Email brief integration.
- Webhook dispatch.
- SFX playback.
- Video-frame ingestion paths.
- User profile and onboarding persistence.
- Static documentation under `docs-site/`.

### Current support boundaries

- **Control API:** mutating sidecar routes remain blocked by the authentication defect tracked in [Issue #5](https://github.com/Capslockb/gemini-live-discord-bridge/issues/5). The sidecar is intended for loopback use; transcript notes require separate privacy review under [Issue #4](https://github.com/Capslockb/gemini-live-discord-bridge/issues/4).
- **HTTP framing:** Unicode response-length correctness remains tracked in [Issue #13](https://github.com/Capslockb/gemini-live-discord-bridge/issues/13).
- **Video frames:** feeder parsing, secret-file alignment, and authenticated client delivery remain open in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9).
- **Email briefs:** backend failure reporting, retry state, recipient routing, and model-visible message data remain open in [Issue #10](https://github.com/Capslockb/gemini-live-discord-bridge/issues/10).
- **Installer:** deterministic reruns and unattended validation remain under review in [Issue #11](https://github.com/Capslockb/gemini-live-discord-bridge/issues/11); literal and atomic credential persistence is tracked separately in [Issue #23](https://github.com/Capslockb/gemini-live-discord-bridge/issues/23).
- **Fallback delegation:** portable binary discovery and rate-limit-aware selection remain open in [Issue #8](https://github.com/Capslockb/gemini-live-discord-bridge/issues/8).
- **Routing and authorization:** deployment-specific user and owner fallbacks are tracked in [Issue #16](https://github.com/Capslockb/gemini-live-discord-bridge/issues/16) and [Issue #17](https://github.com/Capslockb/gemini-live-discord-bridge/issues/17).
- **Storage paths:** custom-root SFX, scheduled-notification, and Google Workspace helper paths are tracked in [Issue #18](https://github.com/Capslockb/gemini-live-discord-bridge/issues/18), [Issue #20](https://github.com/Capslockb/gemini-live-discord-bridge/issues/20), and [Issue #21](https://github.com/Capslockb/gemini-live-discord-bridge/issues/21).
- **Bundled media:** redistribution rights for included SFX remain unverified; see [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12).

### Documentation status — 2026-07-29

- Public documentation was corrected to remove deployment-specific identifiers, private control guidance, unsupported licensing and media-rights claims, stale version labels, broken local links, unsafe operational examples, and capability statements not supported by current runtime behavior.
- The generated site remains checked into the repository. Changes to `scripts/build_docs_site.py` must be reviewed together with the complete regenerated `docs-site/*.html` output before the generator can be treated as authoritative.
- Historical local benchmarks and earlier release descriptions are not substitutes for current validation.

## Earlier releases

### 0.3.4 — 2026-06-09

Introduced a local output-clear path for faster interruption handling, tighter Gemini activity-detection settings, interrupt metrics, and focused interruption/transcript tests. Historical latency figures were environment-specific and should not be treated as current performance guarantees.

### 0.3.3 — 2026-06-09

Added video-state event recording and user notification plumbing for screen-sharing state changes.

### 0.3.2 — 2026-06-09

Added feeder installation and video documentation. Current frame delivery remains subject to the blockers in [Issue #9](https://github.com/Capslockb/gemini-live-discord-bridge/issues/9).

### 0.3.1 — 2026-06-09

Documented a sibling managed-assistant transport and exposed transport discovery metadata.

### 0.3.0 — 2026-06-07

Added delegation fallback, notifications, email briefs, SFX playback, onboarding/profile features, video-state handling, installer support, and expanded documentation. Current portability, privacy, routing, media-rights, and installer boundaries are listed above.

### 0.2.8 — 2026-06-07

Added user-presence checks, first-turn suppression, video-initialization events, and feeder-side frame filtering.

### 0.2.7 — 2026-06-05

Added video-awareness messaging and persisted new-user onboarding state.

### 0.2.6 — 2026-06-05

Removed an unsupported Gemini setup field that prevented bridge startup with the reviewed model configuration.

### 0.2.5 — 2026-06-05

Added repository-status and note-taking integration. Mutation-capable repository operations require normal user authorization and review; historical deployment-specific authentication details are intentionally omitted here.

### 0.2.4 — 2026-06-05

Added webhook dispatch, spoken email-address normalization, email reminder polling, and related tests.

### 0.2.3 — 2026-06-05

Updated typing SFX and added progress-notification plumbing. Bundled-media rights remain governed by [Issue #12](https://github.com/Capslockb/gemini-live-discord-bridge/issues/12).

### 0.2.2 — 2026-06-04

Added per-user profile isolation and owner-gated tool permissions. Repository-embedded identity defaults and persisted owner-state revocation are now tracked in [Issue #17](https://github.com/Capslockb/gemini-live-discord-bridge/issues/17).

### 0.2.1 — 2026-06-04

Added the first regression-test suite.

### 0.1.0 — 2026-06-03

Initial Gemini Live ↔ Discord voice bridge with slash commands, local tools, notes, integrations, interruption handling, and a local control sidecar.

## Verification guidance

Use the current source, open issues, pull-request diff, review threads, and exact-head CI results when evaluating a change. Historical release text, generated pages, local benchmark notes, or previously documented defaults should not be treated as current validation by themselves.