# Fallback chain — multi-CLI delegation with health registry

The voice agent delegates coding tasks to a pool of CLIs (opencode, codex, gemini, numasec, hermes-api). When one is broken, the agent shouldn't be stuck — `execute_with_fallback` automatically reroutes to a healthy neighbor.

## The pool

Defined in `delegation_agent.py:PLATFORMS`:

| Platform | Binary | Best for | Tokens | Rate limit |
|---|---|---|---|---|
| opencode | `/home/caps/.local/bin/opencode` | code/refactor/test/debug | 126k | 100/h |
| codex | `/home/caps/.npm-global/bin/codex` | reasoning/multi-file refactors | 195k | 50/h |
| gemini | `/home/caps/.npm-global/bin/gemini` | huge context/vision/audio | 900k | 1M tok |
| numasec | `/home/caps/.npm-global/bin/numasec` | security/review | 120k | 60/h |
| hermes-api | HTTP `API_SERVER_HOST:API_SERVER_PORT` (default `127.0.0.1:8088`) | general | — | 200/h |

The CLI paths above are the current hard-coded source values, not portable install defaults. The rate-limit values are local counters or estimates, not authoritative provider quotas.

## The chain

Defined in `delegation_agent.py:_FALLBACK_CHAIN`:

```python
_FALLBACK_CHAIN = {
    "codex":    ["opencode", "hermes-api", "gemini"],
    "opencode": ["codex", "hermes-api", "gemini"],
    "numasec":  ["opencode", "codex", "hermes-api"],
    "gemini":   ["opencode", "codex", "hermes-api"],
    "hermes-api": ["opencode", "codex", "gemini"],
}
```

The chain is **bidirectional** between opencode/codex (they substitute for each other most often) and otherwise follows the explicit order above. `choose_fallback()` checks the persisted health registry only; it does not consult the local rate-limit counters when selecting a fallback neighbor.

## Health registry

Broken platforms are persisted to `~/.hermes/voice-platform-health.json` with a TTL (default 600s = 10 min). After TTL expires, `get_health_snapshot()` prunes the entry and the platform is considered healthy again.

```json
{
  "codex": {
    "reason": "rate_limit: 429 from openai.com",
    "marked_at": 1749312456.7,
    "expires_at": 1749313056.7,
    "ttl_seconds": 600
  }
}
```

`is_platform_healthy(platform)` reads the pruned snapshot. `mark_platform_broken(platform, reason, ttl_seconds)` writes it. `clear_platform_health(platform=None)` removes one or all entries.

## `execute_with_fallback(prompt, platform, ...)`

The wrapper that every `local_delegate_execute` call goes through:

1. **Pre-check** — if the platform is already in the broken registry, recurse to the first registry-healthy neighbor with `fallback_from=<original>` and `requested_platform=<original>`.
2. **Spawn** — call the platform's executor (CLI executors create a tmux window and log file; `hermes-api` makes an HTTP request).
3. **Poll the initial log** after the configured delay (default about 5 seconds) for the break signals implemented in `_BROKEN_LOG_PATTERNS`.
4. **If a break signal is detected**: mark the platform broken, recurse to the first registry-healthy neighbor, return a merged result with `fallback_from` and `fallback_reason` populated.
5. **Otherwise**: return the original result with `active_platform == requested_platform`.

This is an initial-startup heuristic, not end-to-end task-success verification. Later failures outside the inspected log prefix or delay do not automatically trigger another fallback.

## `local_delegate_health` tool

Lets the agent inspect, clear, or manually mark the registry without touching the file directly.

```json
// local_delegate_health(action="list")
{
  "result": {
    "unhealthy": {
      "codex": {
        "reason": "rate_limit: 429 from openai.com",
        "expires_in_seconds": 432
      }
    },
    "fallback_chain": {
      "codex": ["opencode", "hermes-api", "gemini"]
    }
  }
}

// local_delegate_health(action="clear", platform="codex")
{
  "result": {
    "cleared": "codex",
    "unhealthy": {}
  }
}
```

The manual-mark action uses `ttl_seconds`, not `ttl`:

```text
local_delegate_health(action="mark", platform="codex", reason="rate limit", ttl_seconds=60)
```

## Why defense in depth

`execute_with_fallback` handles execution-time failures. `suggest_platform` separately filters platforms that are locally rate-limited or present in the broken registry before scoring. The two paths are related but do not apply identical eligibility checks.

## Smoke test

The fallback-chain smoke test performs real execution and may launch a CLI or Hermes API request. Run it only in an isolated development environment. The original example also needs the current function argument names: `ttl_seconds` for `mark_platform_broken`, all four required arguments for `suggest_platform`, and a `session_id` for `execute_with_fallback`.

The generated `docs-site/fallback-chain.html` page may remain stale until the executable documentation generator and generated outputs are reviewed and regenerated.