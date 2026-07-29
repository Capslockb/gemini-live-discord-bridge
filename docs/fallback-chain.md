# Fallback chain — current behavior and portability limits

The delegation helper can retry a task through another configured execution backend when the requested backend is recorded as unhealthy or an initial startup check detects a supported failure signal.

> **Experimental and host-specific.** Current CLI discovery contains installation-specific paths, and fallback selection does not apply the same rate-limit eligibility rules in every code path. Portable discovery and consistent selection remain open in [Issue #8](https://github.com/Capslockb/gemini-live-discord-bridge/issues/8).

## Configured backends

The current pool includes OpenCode, Codex, Gemini, Numasec, and a local Hermes API backend. Availability depends on the executable paths and API endpoint configured in the installed source.

The rate-limit values used by the helper are local counters or estimates. They are not authoritative provider quotas.

## Selection behavior

- The suggestion path filters locally rate-limited backends and entries marked unhealthy in the persisted health registry.
- The execution-time fallback path checks the health registry, but currently does not apply the same local rate-limit filter.
- A backend that fails during the initial inspected startup window can be marked unhealthy temporarily and another configured backend can be attempted.
- This mechanism does not prove end-to-end task success. Failures after the inspected startup window may not trigger another fallback.

## Health registry

Temporary backend health state is stored in `~/.hermes/voice-platform-health.json`. Entries expire after their configured TTL and are pruned when the registry is read.

The `local_delegate_health` tool can list, clear, or manually mark registry entries. Treat manual state changes as operator actions: verify the selected backend and reason, and do not include credentials or private task content in health reasons.

## Safe operating guidance

- Verify executable discovery on the target host before relying on automatic fallback.
- Do not treat local token or request counters as authoritative provider limits.
- Inspect the returned active backend and fallback metadata before reporting that a delegated task completed.
- Run real-execution smoke tests only in an isolated development environment; they can launch external CLIs or send a request to the configured Hermes API.

## Status

This page documents current limitations only. The executable portability and rate-limit-selection correction requires normal code review and is tracked in [Issue #8](https://github.com/Capslockb/gemini-live-discord-bridge/issues/8).
