"""Extracted from bridge.py — part of the gemini-live-discord-bridge split. Do not edit in isolation; see bridge.py facade."""
import ast
import asyncio
import base64
import html
import json
import logging
import os
import queue
import random
import re
import subprocess
import sys
import time
import wave
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from typing import Any, Optional, Dict, List, Callable, Tuple

import numpy as np
logger = logging.getLogger("voice-live")
from bridge_config import HONCHO_CONTEXT_ENABLED, HONCHO_CONTEXT_MAX_CHARS, HONCHO_PEER_NAME

async def _build_honcho_context(peer_name_override: Optional[str] = None) -> str:
    """Fetch peer representation + card from Honcho for the system prompt.

    Uses the honcho client SDK (from honcho.client import Honcho) to avoid
    __init__.py export issues. Falls back to HTTP if SDK is unavailable.

    If peer_name_override is provided, use it as the Honcho peer (per-user isolation).
    Otherwise fall back to the module-level HONCHO_PEER_NAME (legacy single-user mode).
    """
    if not HONCHO_CONTEXT_ENABLED:
        return ""
    try:
        import json
        from pathlib import Path

        honcho_json = Path.home() / ".hermes" / "honcho.json"
        if not honcho_json.exists():
            return ""
        with open(honcho_json, "r") as f:
            data = json.load(f)

        host = data.get("hosts", {}).get("hermes", {})
        base_url = host.get("baseUrl") or data.get("baseUrl") or data.get("base_url") or "http://127.0.0.1:8000"
        workspace = host.get("workspace") or data.get("workspace") or data.get("app_id") or "hermes"
        # Allow honcho.json / caller to override the env-derived peer name
        peer_name = (
            peer_name_override
            or host.get("peerName")
            or data.get("peerName")
            or host.get("peer_name")
            or data.get("peer_name")
            or HONCHO_PEER_NAME
            or "user"
        )
        api_key = host.get("apiKey") or data.get("apiKey") or data.get("api_key")

        # 1. Try SDK first (from honcho.client to bypass __init__ shadow)
        try:
            from honcho.client import Honcho

            if not api_key:
                return ""

            h = Honcho(workspace_id=workspace, base_url=base_url, api_key=api_key)
            peer = h.peer(id=peer_name)

            repr_text = ""
            try:
                repr_text = peer.representation() or ""
            except Exception:
                pass

            card = []
            try:
                card = peer.get_card() or []
            except Exception:
                pass

            parts = []
            if repr_text:
                parts.append(repr_text)
            if card:
                parts.append("Known facts about the user:\n" + "\n".join(f"- {c}" for c in card))
            combined = "\n\n".join(parts)[:HONCHO_CONTEXT_MAX_CHARS]
            if combined:
                return f"\n\n--- USER MEMORY CONTEXT ---\n{combined}\n--- END CONTEXT ---"
            return ""

        except ImportError:
            # SDK not available — fall through to HTTP fallback
            pass

        # 2. HTTP fallback (for cases where SDK import fails)
        try:
            import httpx
        except ImportError:
            return ""

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            # List workspaces (v3 uses POST)
            ws_resp = await client.post("/v3/workspaces/list", headers=headers, json={})
            if ws_resp.status_code == 401:
                logger.warning("Honcho context injection: 401 — check honcho.json apiKey")
                return ""
            ws_resp.raise_for_status()
            workspaces = ws_resp.json()
            ws_id = None
            items = workspaces.get("items", []) if isinstance(workspaces, dict) else workspaces
            for ws in items:
                if ws.get("name") == workspace or ws.get("id") == workspace:
                    ws_id = ws.get("id")
                    break
            if not ws_id and items:
                ws_id = items[0].get("id")
            if not ws_id:
                return ""

            # List peers (v3 uses POST)
            peer_resp = await client.post(
                f"/v3/workspaces/{ws_id}/peers/list",
                headers=headers,
                json={},
            )
            peer_resp.raise_for_status()
            peers = peer_resp.json()
            peer_id = None
            peer_items = peers.get("items", []) if isinstance(peers, dict) else peers
            for p in peer_items:
                if p.get("id") == peer_name:
                    peer_id = p.get("id")
                    break
            if not peer_id:
                return ""

            # Fetch representation
            repr_text = ""
            try:
                repr_resp = await client.get(
                    f"/v3/workspaces/{ws_id}/peers/{peer_id}/representation",
                    headers=headers,
                )
                if repr_resp.status_code == 200:
                    repr_data = repr_resp.json()
                    repr_text = repr_data.get("representation", "") or ""
            except Exception:
                pass

            # Fetch card (conclusions)
            card = []
            try:
                card_resp = await client.get(
                    f"/v3/workspaces/{ws_id}/peers/{peer_id}/conclusions",
                    headers=headers,
                )
                if card_resp.status_code == 200:
                    card_data = card_resp.json()
                    card_items = card_data if isinstance(card_data, list) else card_data.get("items", [])
                    card = [item.get("conclusion", "") for item in card_items if item.get("conclusion")]
            except Exception:
                pass

        parts = []
        if repr_text:
            parts.append(repr_text)
        if card:
            parts.append("Known facts about the user:\n" + "\n".join(f"- {c}" for c in card))
        combined = "\n\n".join(parts)[:HONCHO_CONTEXT_MAX_CHARS]
        if combined:
            return f"\n\n--- USER MEMORY CONTEXT ---\n{combined}\n--- END CONTEXT ---"
        return ""

    except Exception as exc:
        logger.warning("Honcho context injection failed: %s", exc)
        return ""


__all__ = ['_build_honcho_context']
__all__ = [n for n in __all__ if n in globals()]
