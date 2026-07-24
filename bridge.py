"""
Discord Voice Live Bridge — facade module.

bridge.py was split into focused modules (see the repo audit / PR notes):

  bridge_config.py   — env-var configuration, constants, logging, system prompt
  bridge_context.py  — Honcho context injection
  bridge_email.py    — email autocorrect + reminder loop
  bridge_tools.py    — voice tool runners (GitHub, Spotify, web, local, sysinspect)
  bridge_opencode.py — opencode tmux session management + watchers
  bridge_audio.py    — PCM helpers, typing SFX, LiveAudioSource
  bridge_core.py     — GeminiLiveBridge + VoiceLiveBridge classes
  bridge_http.py     — sidecar HTTP server, run_sidecar, BRIDGE singleton

This facade re-exports every public and internal name so existing imports
(`from bridge import GeminiLiveBridge`, `import bridge`, etc.) keep working.
"""

import sys

from bridge_config import *   # noqa: F401,F403
from bridge_context import *  # noqa: F401,F403
from bridge_email import *    # noqa: F401,F403
from bridge_tools import *    # noqa: F401,F403
from bridge_opencode import * # noqa: F401,F403
from bridge_audio import *    # noqa: F401,F403
from bridge_core import *     # noqa: F401,F403
from bridge_http import *     # noqa: F401,F403

from bridge_config import logger, GEMINI_API_KEY, _rkt
from bridge_tools import (
    _SPOTIFY_FUNCTION_DECLARATIONS,
    _WEB_FUNCTION_DECLARATIONS,
    _LOCAL_FUNCTION_DECLARATIONS,
    _HOMEASSISTANT_FUNCTION_DECLARATIONS,
    _SYSINSPECT_FUNCTION_DECLARATIONS,
)
from bridge_opencode import _OPENCODE_FUNCTION_DECLARATIONS


# Register all known tool names with the per-user profile system so the
# allowlist vocabulary is in sync with the declarations above.
def _register_all_known_tools():
    if _rkt is None:
        return
    for decl_list in (
        _SPOTIFY_FUNCTION_DECLARATIONS,
        _WEB_FUNCTION_DECLARATIONS,
        _LOCAL_FUNCTION_DECLARATIONS,
        _HOMEASSISTANT_FUNCTION_DECLARATIONS,
        _OPENCODE_FUNCTION_DECLARATIONS,
        _SYSINSPECT_FUNCTION_DECLARATIONS,
    ):
        try:
            for d in decl_list:
                if isinstance(d, dict) and d.get("name"):
                    _rkt(d["name"])
        except Exception as exc:
            logger.debug("tool registration failed: %s", exc)


_register_all_known_tools()


if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("FATAL: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    print("Voice Live sidecar started (standalone test mode)", file=sys.stderr)
    print("Run via Hermes plugin to provide voice_client and adapter", file=sys.stderr)
