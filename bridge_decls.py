"""Static voice-tool function declarations extracted from bridge_tools.py.

Pure data (plus the *_ENABLED env flags) so bridge_core can build the Gemini
setup payload without importing the tool runners.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bridge_opencode import OPENCODE_DEFAULT_MODEL


_SPOTIFY_FUNCTION_DECLARATIONS = [
    {
        "name": "spotify_play",
        "description": "Start or resume Spotify playback. Optionally provide track URIs, a playlist/album URI, or a device ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "uris": {"type": "array", "items": {"type": "string"}, "description": "Track URIs to play directly"},
                "context_uri": {"type": "string", "description": "Playlist or album URI to play"},
                "device_id": {"type": "string", "description": "Target Spotify device ID"},
            },
        },
    },
    {
        "name": "spotify_pause",
        "description": "Pause Spotify playback.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_next",
        "description": "Skip to the next track.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_previous",
        "description": "Go to the previous track.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_get_state",
        "description": "Get the current Spotify playback state: what's playing, volume, active device, progress.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_set_volume",
        "description": "Set Spotify playback volume (0-100).",
        "parameters": {
            "type": "object",
            "properties": {
                "volume_percent": {"type": "integer", "description": "Volume from 0 to 100"},
            },
            "required": ["volume_percent"],
        },
    },
    {
        "name": "spotify_search",
        "description": "Search Spotify catalog for tracks, albums, artists, or playlists.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "types": {"type": "array", "items": {"type": "string"}, "description": "One or more of: track, album, artist, playlist, show, episode"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "spotify_add_to_queue",
        "description": "Add a track to the Spotify queue by its URI.",
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Spotify track URI to add"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "spotify_playlists",
        "description": "Manage Spotify playlists — list your playlists, get details, create new ones, add/remove tracks. For hyper-personalized 'mood' or 'recommended' playlists, use action='create' with a creative name matching the user's request, then action='add_items' with track URIs found via spotify_search.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "add_items", "remove_items", "update_details"],
                    "description": "Action to perform"
                },
                "name": {"type": "string", "description": "Playlist name (required for create, optional for update_details)"},
                "playlist_id": {"type": "string", "description": "Spotify playlist ID (for get, add_items, remove_items, update_details)"},
                "description": {"type": "string", "description": "Playlist description (for create, update_details)"},
                "public": {"type": "boolean", "description": "Make playlist publicly visible (for create, update_details)"},
                "collaborative": {"type": "boolean", "description": "Allow collaborators (for create, update_details)"},
                "uris": {"type": "array", "items": {"type": "string"}, "description": "Track URIs to add/remove (required for add_items, remove_items)"},
                "limit": {"type": "integer", "description": "Max playlists to list (default 20, for list action)"},
                "position": {"type": "integer", "description": "Insert position in playlist (for add_items)"},
            },
            "required": ["action"],
        },
    },
]


_WEB_FUNCTION_DECLARATIONS = [
    {
        "name": "web_search",
        "description": "Search the web for current information, facts, news, products, or research topics. Returns URLs, titles, and descriptions. Use this when answering time-sensitive questions or verifying current information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "limit": {"type": "integer", "description": "Maximum results to return (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_extract",
        "description": "Extract full content from specific web pages. Use after web_search to read a full article or page content.",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "List of page URLs to extract"},
            },
            "required": ["urls"],
        },
    },
]


_GITHUB_FUNCTION_DECLARATIONS = [
    {
        "name": "local_github_repo_list",
        "description": "List the user's GitHub repositories using the `gh` CLI. Returns name, full name, description, visibility, and last-updated timestamp. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max repos to return (default 20, max 50)"},
            },
        },
    },
    {
        "name": "local_github_issues",
        "description": "List issues for a specific GitHub repo. Returns number, title, state, author, URL, labels.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format, e.g. 'Capslockb/gemini-live-discord-bridge'"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Issue state filter (default: open)"},
                "limit": {"type": "integer", "description": "Max issues (default 15, max 50)"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "local_github_prs",
        "description": "List pull requests for a specific GitHub repo. Returns number, title, state, author, URL, branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format"},
                "state": {"type": "string", "enum": ["open", "closed", "merged", "all"], "description": "PR state filter (default: open)"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "local_github_issue_create",
        "description": "Create a new issue on a GitHub repo. Use sparingly — only when the user explicitly asks. Returns the new issue URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body (markdown)"},
                "labels": {"type": "string", "description": "Comma-separated label names"},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "name": "local_github_note",
        "description": "Persist a free-form note for the next Hermes turn or future voice session. Notes are written to ~/.hermes/voice-users/voice-session-notes.jsonl in append-only mode. Use this to capture action items, todos, or context that should survive across voice sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The note text (max ~4000 chars)"},
                "category": {"type": "string", "description": "Category for filtering (e.g. 'todo', 'followup', 'context')"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "local_github_notes_read",
        "description": "Read back persisted voice session notes (most recent first). Use after the call to recall what the user wanted done.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max notes to return (default 20, max 100)"},
                "category": {"type": "string", "description": "Optional category filter"},
            },
        },
    },
    {
        "name": "local_github_suggest_repos",
        "description": (
            "Suggest interesting GitHub repos based on topics or interests. "
            "Searches GitHub for popular repos matching the given keywords, "
            "checks if you already starred them, and returns a curated "
            "recommendation list with descriptions, stars, and URLs. "
            "Proactively suggest this when you learn the user's interests "
            "or during idle moments (criterion #30)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to search (e.g. ['hermes agent', 'discord bots'])",
                },
                "limit_per_topic": {
                    "type": "integer",
                    "description": "Max results per topic (default 3, max 5)",
                },
            },
            "required": ["interests"],
        },
    },
]


HA_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_HA_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}


HA_VOICE_TOOLS_ENABLED = os.getenv("HASS_TOKEN", "").strip() != "" and HA_VOICE_TOOLS_ENABLED


_HOMEASSISTANT_FUNCTION_DECLARATIONS = [
    {
        "name": "local_homeassistant_entity_list",
        "description": "List all Home Assistant entities with their current state and friendly name. Use this to discover available devices, sensors, switches, lights, and other entities in the smart home.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_homeassistant_get_state",
        "description": "Get the current state of a specific Home Assistant entity (e.g., light.living_room, sensor.temperature). Returns state, attributes, and last_changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Full entity ID, e.g. light.living_room or sensor.temperature_bedroom"},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "local_homeassistant_call_service",
        "description": "Call a Home Assistant service to control devices — turn lights on/off, set temperature, lock doors, trigger automations, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain, e.g. light, switch, climate, lock, automation"},
                "service": {"type": "string", "description": "Service name, e.g. turn_on, turn_off, set_temperature"},
                "entity_id": {"type": "string", "description": "Target entity ID, e.g. light.living_room"},
                "data": {"type": "object", "description": "Optional service data as JSON object (e.g. brightness, temperature, rgb_color)"},
            },
            "required": ["domain", "service", "entity_id"],
        },
    },
    {
        "name": "local_homeassistant_get_services",
        "description": "List all available Home Assistant service domains and their services. Use this when you need to know what services are available for a specific domain.",
        "parameters": {"type": "object", "properties": {}},
    },
]


_LOCAL_FUNCTION_DECLARATIONS = [
    {
        "name": "local_weather",
        "description": "Get current weather for a location. Defaults to Amsterdam, NL if no location provided. Returns temperature, conditions, wind.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name or lat,lon. Defaults to Amsterdam."},
            },
        },
    },
    {
        "name": "local_translate",
        "description": "Translate text between languages. Auto-detects source if not specified. Supports Dutch, Romanian, English, Spanish.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "target_language": {"type": "string", "description": "Target language code or name: en, nl, ro, es"},
                "source_language": {"type": "string", "description": "Optional source language. Auto-detected if omitted."},
            },
            "required": ["text", "target_language"],
        },
    },
    {
        "name": "local_time",
        "description": "Get current time for a timezone or city. Defaults to local system time in Europe/Amsterdam.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Timezone like Europe/Amsterdam, UTC, or city."},
            },
        },
    },
    {
        "name": "local_remind",
        "description": "Store a voice reminder locally or list upcoming reminders. Action 'add' appends a note with optional minutes delay. Action 'list' shows stored reminders. Read-only append, never deletes anything.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list"], "description": "add or list"},
                "text": {"type": "string", "description": "Reminder text (required for add)"},
                "minutes": {"type": "integer", "description": "Minutes from now (optional, for add)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "local_email",
        "description": "List recent unread emails via Himalaya CLI. Returns sender, subject, date in a spoken-friendly list.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of emails to list (default 5)"},
            },
        },
    },
    {
        "name": "local_email_read",
        "description": "Fetch the full content of a specific email by ID. Returns sender, recipient, subject, date, and body text. Use IDs from local_email (email list). Uses the Gmail API.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID (numeric, from email list results)"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "local_email_send",
        "description": "Compose and send a new email via Gmail. Provide recipient, subject, and body. Works best for short messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "local_email_reply",
        "description": "Reply to an existing email by its Gmail message ID. Properly threads the reply with In-Reply-To and References headers. Use IDs from local_email (email list).",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID to reply to"},
                "body": {"type": "string", "description": "Reply body text (plain text)"},
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "local_email_brief",
        "description": (
            "Build a proactive spoken brief of recent inbox mail (criterion #7). "
            "Fetches the latest N emails, scores them by importance (recency, "
            "Gmail labels, urgent keywords, sender heuristics), and groups them "
            "into Important / FYI / Auto. Returns a concise summary to you AND "
            "fires local_notify(mode='auto') so the user gets pinged even when "
            "AFK. By default the scheduler ticks every 30 minutes and only "
            "briefs when there's new mail; pass force=true to always brief. "
            "Pass notify=false for a pure read (no DM/webhook fired). The "
            "backend is auto-selected (google_api.py preferred, himalaya fallback)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Optional. Max emails to consider. Default 8.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Optional. Skip the de-dup check and always brief.",
                },
                "notify": {
                    "type": "boolean",
                    "description": "Optional. Default true. Set false for a pure read without DM/webhook.",
                },
                "backend": {
                    "type": "string",
                    "enum": ["google", "himalaya", "auto"],
                    "description": "Optional. 'google' tries google_api.py first; 'himalaya' tries himalaya first; 'auto' uses the same default as google. Default 'google'.",
                },
            },
        },
    },
    {
        "name": "local_systemd",
        "description": "Check systemd user services status. Lists active services or checks a specific one. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Specific service name, e.g. hermes-gateway. If omitted, lists all active user services."},
            },
        },
    },
    {
        "name": "local_docker",
        "description": "List running Docker containers with names and status. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_tailscale",
        "description": "Show Tailscale tailnet peers and their online status. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_notes",
        "description": "Search voice call notes and transcripts for keywords. Returns matching entries with timestamps.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_disk",
        "description": "Check disk space usage for mounted filesystems. Returns human-readable usage. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_calc",
        "description": "Evaluate a safe mathematical expression: + - * / ** parentheses sqrt abs sin cos log round. Returns numeric result.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression, e.g. 500 * 1.21 or sqrt(256) + abs(-10)"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "local_uptime",
        "description": "Get system uptime, load averages, and memory summary. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_news",
        "description": "Get a brief summary of recent news headlines. Uses web search internally. Topic defaults to tech.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "News topic: AI, tech, general. Defaults to tech."},
                "limit": {"type": "integer", "description": "Headlines to return (default 5)"},
            },
        },
    },
    {
        "name": "local_youtube",
        "description": "Search YouTube for videos by query. Returns titles and URLs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_honcho",
        "description": "Search personal memory and facts stored in Honcho. Look up past decisions, preferences, configurations, or identities. Returns matching memory excerpts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Memory query or keyword to search"},
                "limit": {"type": "integer", "description": "Max memory excerpts (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_user_onboarding_answer",
        "description": (
            "Persist the user's answer to a single onboarding question (criterion #32). "
            "Use the question_id from local_user_onboarding_get_questions and the "
            "user's spoken answer. Marks the profile's onboarding_completed=true once "
            "all questions in the set are answered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {
                    "type": "string",
                    "description": "One of: name, timezone, work, interests, style, pet_peeves",
                },
                "answer": {
                    "type": "string",
                    "description": "The user's answer (free text — STT transcript)",
                },
            },
            "required": ["question_id", "answer"],
        },
    },
    {
        "name": "local_user_onboarding_get_questions",
        "description": (
            "Return the list of onboarding questions to ask the user (criterion #32). "
            "Call this on the first turn of a new user's first voice session to learn "
            "their name, timezone, work, interests, communication style, and pet peeves. "
            "Then call local_user_onboarding_answer for each."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    # ── Multi-CLI delegation tools (criterion #23-#25) ─────────────────
    {
        "name": "local_delegate_quick",
        "description": (
            "Immediately execute a reversible coding, repository, build, test, or analysis task "
            "on Codex, OpenCode, or the healthiest fallback. Use this directly when the user asks "
            "you to do something; do not stop at a suggestion and do not ask for confirmation unless "
            "the requested action is destructive, irreversible, publishes externally, or spends money."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Concrete task to execute now"},
                "platform": {
                    "type": "string",
                    "enum": ["auto", "opencode", "codex"],
                    "description": "Preferred backend; auto chooses a healthy backend",
                },
                "workdir": {
                    "type": "string",
                    "description": "Existing project directory under SORA_DELEGATION_ALLOWED_ROOTS; omitted uses isolated scratch",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "local_delegate_status",
        "description": (
            "Read back a previously launched delegation. Returns completed with a sanitized output tail, "
            "running with the active session handle, or failed with a safe reason. Use this instead of "
            "guessing whether Codex or OpenCode finished."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sessionId": {"type": "string", "description": "Session ID returned by a delegation tool"},
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex"],
                },
            },
            "required": ["sessionId", "platform"],
        },
    },
    {
        "name": "local_delegate_suggest",
        "description": (
            "Analyze a task and suggest the best delegation platform + ETA. "
            "Call this before delegating a task to choose between the sandboxed "
            "OpenCode and Codex runners. "
            "Returns platform suggestion, reason, ETA, rate-limits, and context-fit warnings. "
            "The user should confirm the suggestion before proceeding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The user's full original goal"},
                "project_size": {
                    "type": "string",
                    "enum": ["tiny", "small", "medium", "large", "xlarge"],
                    "description": "Estimated size: tiny (1 file), small (2-5 files), medium (multi-file), large (new feature), xlarge (project-level)",
                },
                "scope": {
                    "type": "string",
                    "enum": ["code", "refactor", "security", "research", "analysis", "build", "test"],
                    "description": "Type of work",
                },
                "complexity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "extreme"],
                    "description": "Complexity: low = straightforward, extreme = uncharted territory",
                },
                "project_root": {
                    "type": "string",
                    "description": "Optional project root directory for context-fit estimation",
                },
            },
            "required": ["goal", "project_size", "scope", "complexity"],
        },
    },
    {
        "name": "local_delegate_assemble",
        "description": (
            "Assemble a platform-optimized system prompt for the target CLI. "
            "Call AFTER local_delegate_suggest and the user confirms the platform. "
            "The output is a ready-to-send prompt that includes sub-goals, constraints, "
            "and platform-specific instructions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The main goal"},
                "subgoals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered sub-goals (broken down by the agent)",
                },
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex"],
                    "description": "The platform chosen by local_delegate_suggest",
                },
                "project_root": {"type": "string", "description": "Optional project root"},
            },
            "required": ["goal", "subgoals", "platform"],
        },
    },
    {
        "name": "local_delegate_execute",
        "description": (
            "Execute a delegation on the chosen platform. "
            "Pass the assembled prompt from local_delegate_assemble and the platform "
            "name. Returns a session_id that must be checked via local_delegate_status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The assembled system prompt from local_delegate_assemble"},
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex"],
                    "description": "Sandboxed target platform",
                },
                "session_id": {"type": "string", "description": "A unique session name (lowercase, no spaces)"},
                "workdir": {
                    "type": "string",
                    "description": "Existing project directory under SORA_DELEGATION_ALLOWED_ROOTS; omitted uses isolated scratch",
                },
            },
            "required": ["prompt", "platform", "session_id"],
        },
    },
    {
        "name": "local_delegate_eta",
        "description": (
            "Update the ETA estimation for future tasks based on how long the "
            "last delegation actually took vs the estimate. Improves accuracy over time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actual_seconds": {"type": "integer", "description": "How long the task actually took"},
                "estimated_seconds": {"type": "integer", "description": "What was estimated"},
            },
            "required": ["actual_seconds", "estimated_seconds"],
        },
    },
    {
        "name": "local_delegate_health",
        "description": (
            "Inspect and manage the platform-fallback health registry. Use action='list' to see "
            "which delegation platforms (opencode, codex, gemini, numasec, hermes-api) are currently "
            "marked broken and the fallback chain that will be used. Use action='clear' to remove "
            "a platform from the broken list (pass platform='codex' or omit to clear all). Use "
            "action='mark' to manually flag a platform as broken with a custom reason and TTL — "
            "useful when you already know codex auth is down before spawning it. "
            "Tool fallback is automatic: local_delegate_execute will auto-route to a healthy "
            "neighbor when the requested platform is broken, so you usually do NOT need to call "
            "this proactively — only when you want to inspect, override, or manually clear state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "clear", "mark"],
                    "description": "What to do with the health registry",
                },
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex"],
                    "description": "Required for action='clear' (omit to clear all) and action='mark'",
                },
                "reason": {
                    "type": "string",
                    "description": "Required for action='mark'. Why this platform is being flagged.",
                },
                "ttl_seconds": {
                    "type": "integer",
                    "description": "Optional, default 600. How long the broken flag should last before auto-expiring.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "local_notify",
        "description": (
            "Break out of reply-only mode and notify the user proactively, on your own accord "
            "(criterion #6). Use this for completion pings, scheduled reminders, alerts, and "
            "status updates the user would want even if they're not actively talking. mode='auto' "
            "picks the best available path: voice if the bridge is running and the user is in the "
            "voice channel, else Discord DM, else configured webhook. mode='voice' pushes text "
            "into the next Gemini turn so the user hears it. mode='dm' sends a Discord DM. "
            "mode='channel' posts in a specific Discord text channel. mode='webhook' fires a "
            "configurable webhook event. mode='all' fans out to every channel. The dispatcher "
            "thread-safety means you can call this from any path — including background tools "
            "like the opencode watcher when a long delegation finishes while the user is AFK."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message to send. Keep it concise; the user will see it as a notification.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "voice", "dm", "channel", "webhook", "all"],
                    "description": "Delivery mode. Default 'auto'.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional short title (used as embed title in webhooks, prefix in voice).",
                },
                "source": {
                    "type": "string",
                    "description": "Optional tag for routing/throttling. e.g. 'delegation', 'email', 'reminder'.",
                },
                "channel_id": {
                    "type": "string",
                    "description": "Required for mode='channel'. Discord channel snowflake ID.",
                },
                "user_id": {
                    "type": "string",
                    "description": "Optional override for DM target. Defaults to the bridge's target user.",
                },
                "event_class": {
                    "type": "string",
                    "description": "Optional webhook event class. Default 'agent.notify'.",
                },
                "sub_event": {
                    "type": "string",
                    "description": "Optional webhook sub-event. Default 'agent_notification'.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "local_notify_schedule",
        "description": (
            "Queue a deferred notification that will fire after N seconds (criterion #6). Use this "
            "for 'remind me in 10 minutes', 'ping me when this finishes', or any time the user "
            "asks for an out-of-band follow-up. The schedule persists to disk and survives bridge "
            "restarts. The dispatcher polls every 2s and fires due entries via local_notify's "
            "auto path. Use list=true to inspect the current queue without scheduling. "
            "Pass cancel_id to remove a previously scheduled entry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The message to deliver at fire time."},
                "delay_seconds": {
                    "type": "integer",
                    "description": "Optional. Seconds from now to fire. Use either delay_seconds OR fire_at_epoch.",
                },
                "fire_at_epoch": {
                    "type": "number",
                    "description": "Optional. Absolute fire time as Unix epoch seconds. Use either delay_seconds OR fire_at_epoch.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "voice", "dm", "channel", "webhook", "all"],
                    "description": "Same modes as local_notify. Default 'auto'.",
                },
                "title": {"type": "string", "description": "Optional title."},
                "source": {"type": "string", "description": "Optional source tag."},
                "channel_id": {"type": "string", "description": "Optional channel_id for mode='channel'."},
                "list": {
                    "type": "boolean",
                    "description": "Set true to list scheduled notifications (no other action).",
                },
                "cancel_id": {
                    "type": "string",
                    "description": "ID returned from a previous schedule call. Cancels that entry.",
                },
            },
        },
    },
    {
        "name": "local_sfx_test",
        "description": (
            "Play a UI sound effect into the active voice session (criterion #8). "
            "Slots: 'tool_init' (chime on first tool call), 'error' (sharp beep on tool failure), "
            "'notification' (soft chime on local_notify delivery / email brief), 'transition' (pop on "
            "session start/stop). Pass action='list' to see which slots are configured and which WAVs "
            "are loaded. Useful for testing that the sfx library is wired correctly. No-op if no voice "
            "session is active."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "slot": {
                    "type": "string",
                    "enum": ["tool_init", "error", "notification", "transition"],
                    "description": "Which sfx slot to play. Required unless action='list'.",
                },
                "action": {
                    "type": "string",
                    "enum": ["play", "list"],
                    "description": "Default 'play'. Use 'list' to inspect configured slots.",
                },
            },
        },
    },
]


LOCAL_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_LOCAL_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}


SYSINSPECT_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}


_SYSINSPECT_FUNCTION_DECLARATIONS = [
    {
        "name": "local_inspect_read",
        "description": (
            "Read a file's first N lines from an allowlisted path (under ~/.hermes, "
            "hermes-workspace, honcho, /etc/systemd, /var/log, etc.). Use to inspect configs, "
            "check service state, look at skills/plugins. Never returns binary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "limit": {"type": "integer", "description": "Max lines to return (default 200, max 1000)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "local_inspect_grep",
        "description": (
            "Search for a regex pattern inside a file or directory under an allowlisted path. "
            "Returns matching lines with line numbers, capped at `limit`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to search inside"},
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "limit": {"type": "integer", "description": "Max matches (default 50, max 200)"},
            },
            "required": ["path", "pattern"],
        },
    },
]


__all__ = ['HA_VOICE_TOOLS_ENABLED', 'LOCAL_VOICE_TOOLS_ENABLED', 'SYSINSPECT_VOICE_TOOLS_ENABLED', '_GITHUB_FUNCTION_DECLARATIONS', '_HOMEASSISTANT_FUNCTION_DECLARATIONS', '_LOCAL_FUNCTION_DECLARATIONS', '_SPOTIFY_FUNCTION_DECLARATIONS', '_SYSINSPECT_FUNCTION_DECLARATIONS', '_WEB_FUNCTION_DECLARATIONS']
__all__ = [n for n in __all__ if n in globals()]
