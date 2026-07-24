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
from bridge_config import GOOGLE_API_BIN

def _autocorrect_email_address(raw: str) -> Tuple[str, List[str]]:
    """Best-effort STT error correction for email addresses.

    Returns (corrected, list_of_change_notes). If nothing changed,
    notes is empty.
    """
    if not raw:
        return "", []
    notes: List[str] = []
    s = raw.strip()
    if s != raw:
        notes.append("stripped whitespace")

    # Lowercase the whole address. Most providers (Gmail, Outlook, etc.)
    # ignore case in the local-part too, and STT transcribers frequently
    # return all-caps. This is the safe default.
    if s != s.lower():
        notes.append("lowercased")
        s = s.lower()

    # Common STT word substitutions (case-insensitive on the whole address).
    # Use re.IGNORECASE so "AT", "AT" (caps) and "at" all match.
    pre = s
    substitutions = [
        (r"\s+at\s+", "@"),          # "alice at example.com" / "ALICE AT ..."
        (r"\s+@", "@"),              # trailing " @" → "@"
        (r"@\s+", "@"),              # "@ ..." → "@..."
        (r"\s+dot\s+", "."),          # "example dot com" / "EXAMPLE DOT COM"
        (r"\s+\.\s+", "."),           # "example . com" → "example.com"
        (r"\s+underscore\s+", "_"),
        (r"\s+_\s+", "_"),
        (r"\s+dash\s+", "-"),
        (r"\s+-\s+", "-"),
        (r"\s+at\s*$", "@"),           # trailing "at" with no domain
    ]
    for pattern, repl in substitutions:
        new = re.sub(pattern, repl, s, flags=re.IGNORECASE)
        if new != s:
            notes.append(f"applied regex {pattern!r}")
            s = new

    # Collapse doubled spaces (defensive — the regex above usually does this)
    if "  " in s:
        notes.append("collapsed double spaces")
        s = re.sub(r"\s{2,}", "", s)

    # Sanity check: result must contain exactly one '@' and at least one '.'
    # after the '@'. If not, bail and return the original.
    if s.count("@") != 1 or "." not in s.split("@", 1)[1]:
        return raw.strip(), []

    return s, notes


EMAIL_REMINDER_BLOCKLIST_DOMAINS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "google.com",
    "apple.com",
    "microsoft.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "amazon.com",
    "paypal.com",
    "stripe.com",
    "docker.com",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
)


EMAIL_REMINDER_BLOCKLIST_KEYWORDS = (
    "newsletter",
    "noreply",
    "no-reply",
    "donotreply",
    "unsubscribe",
    "automated",
    "auto-generated",
    "auto-generated",
    "notification",
    "receipt",
    "invoice",
    "statement",
    "verification code",
    "password reset",
    "confirm your email",
    "verify your email",
    "ci/",
    "build ",
    "deployment",
    "merge request",
    "pull request",
    "pr #",  # GitHub-style "[repo] PR #123: ..."
    "[bot]",
)


def _should_remind_email(sender: str, subject: str) -> bool:
    """Return True if an incoming email should trigger an 'important email' reminder.

    Filter out automated senders, notifications, newsletters, and CI
    systems. Remind only for what looks like a real human-to-human email.
    """
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    for d in EMAIL_REMINDER_BLOCKLIST_DOMAINS:
        if d in sender_l:
            return False
    for kw in EMAIL_REMINDER_BLOCKLIST_KEYWORDS:
        if kw in subject_l or kw in sender_l:
            return False
    # Must have a reasonable sender format
    if "@" not in sender_l:
        return False
    # Final guard: also check the original (non-lowered) subject for
    # GitHub PR pattern that includes brackets and capital letters
    if subject and re.search(r"\[.+\]\s*pr\s*#\d+", subject, re.IGNORECASE):
        return False
    return True


EMAIL_REMINDER_POLL_SECONDS = float(
    os.getenv("DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS", "300")
)


EMAIL_REMINDER_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}


EMAIL_REMINDER_MAX_PER_HOUR = int(
    os.getenv("DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR", "3")
)


_EMAIL_REMINDER_TASK: Optional["asyncio.Task"] = None


_EMAIL_REMINDER_LAST_FIRED: List[float] = []


_EMAIL_REMINDER_SEEN_IDS: Dict[str, float] = {}


_EMAIL_REMINDER_MAX_SEEN = 200


async def _email_reminder_loop(bridge: Any) -> None:
    """Periodically check the inbox and voice-remind the user about important emails.

    Runs as a background asyncio task on the bridge. Polls the Gmail
    inbox via google_api.py every EMAIL_REMINDER_POLL_SECONDS,
    filters out automated senders via _should_remind_email(), and
    sends a voice reminder for any new important email.

    Throttled to EMAIL_REMINDER_MAX_PER_HOUR reminders per hour to
    avoid nagging.
    """
    import time as _time
    if not EMAIL_REMINDER_ENABLED:
        return
    seen_path = Path.home() / ".hermes" / "voice-users" / "email-reminder-seen.json"
    try:
        if seen_path.exists():
            with open(seen_path) as f:
                _EMAIL_REMINDER_SEEN_IDS.update(json.load(f))
    except Exception:
        pass
    # Allow 10s grace on first start so the user isn't immediately nagged
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.sleep(EMAIL_REMINDER_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        # Throttle: no more than N per rolling 60 min
        now = _time.monotonic()
        _EMAIL_REMINDER_LAST_FIRED[:] = [
            t for t in _EMAIL_REMINDER_LAST_FIRED if (now - t) < 3600
        ]
        if len(_EMAIL_REMINDER_LAST_FIRED) >= EMAIL_REMINDER_MAX_PER_HOUR:
            continue
        # Fetch unread inbox via google_api.py
        try:
            if not Path(GOOGLE_API_BIN).exists():
                continue
            out = subprocess.run(
                [sys.executable, GOOGLE_API_BIN, "gmail", "search", "is:unread in:inbox", "--max", "10"],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode != 0:
                continue
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list):
                continue
        except Exception:
            continue
        for item in items:
            mid = str(item.get("id", ""))
            sender = str(item.get("from", ""))
            subject = str(item.get("subject", ""))
            if not mid or mid in _EMAIL_REMINDER_SEEN_IDS:
                continue
            if not _should_remind_email(sender, subject):
                _EMAIL_REMINDER_SEEN_IDS[mid] = now
                continue
            # Fire voice reminder
            try:
                reminder_text = (
                    f"You have an important email from {sender} about '{subject}'. "
                    f"Want me to read it aloud or just keep going?"
                )
                await bridge.send_text(reminder_text)
                _EMAIL_REMINDER_LAST_FIRED.append(now)
                _EMAIL_REMINDER_SEEN_IDS[mid] = now
                # Webhook
                try:
                    from webhook_dispatcher import emit_bridge_status
                    emit_bridge_status(
                        "info", f"Email reminder: {sender} — {subject[:80]}"
                    )
                except Exception:
                    pass
                # Only one reminder per poll cycle
                break
            except Exception as exc:
                logger.debug("email reminder send_text failed: %s", exc)
        # Trim seen-ids dict
        if len(_EMAIL_REMINDER_SEEN_IDS) > _EMAIL_REMINDER_MAX_SEEN:
            cutoff = now - 86400  # 24h
            for k in [k for k, ts in _EMAIL_REMINDER_SEEN_IDS.items() if ts < cutoff]:
                _EMAIL_REMINDER_SEEN_IDS.pop(k, None)
        # Persist
        try:
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            with open(seen_path, "w") as f:
                json.dump(_EMAIL_REMINDER_SEEN_IDS, f)
        except Exception:
            pass


def _start_email_reminder_loop(bridge: Any) -> None:
    """Start the email reminder background task. Idempotent."""
    global _EMAIL_REMINDER_TASK
    if _EMAIL_REMINDER_TASK is not None and not _EMAIL_REMINDER_TASK.done():
        return
    if not EMAIL_REMINDER_ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _EMAIL_REMINDER_TASK = loop.create_task(_email_reminder_loop(bridge))
    logger.info("email reminder loop started (poll=%.0fs, max/hr=%d)",
                EMAIL_REMINDER_POLL_SECONDS, EMAIL_REMINDER_MAX_PER_HOUR)


def _stop_email_reminder_loop() -> None:
    global _EMAIL_REMINDER_TASK
    if _EMAIL_REMINDER_TASK is not None and not _EMAIL_REMINDER_TASK.done():
        _EMAIL_REMINDER_TASK.cancel()
    _EMAIL_REMINDER_TASK = None


__all__ = ['_autocorrect_email_address', 'EMAIL_REMINDER_BLOCKLIST_DOMAINS', 'EMAIL_REMINDER_BLOCKLIST_KEYWORDS', '_should_remind_email', 'EMAIL_REMINDER_POLL_SECONDS', 'EMAIL_REMINDER_ENABLED', 'EMAIL_REMINDER_MAX_PER_HOUR', '_EMAIL_REMINDER_TASK', '_EMAIL_REMINDER_LAST_FIRED', '_EMAIL_REMINDER_SEEN_IDS', '_EMAIL_REMINDER_MAX_SEEN', '_email_reminder_loop', '_start_email_reminder_loop', '_stop_email_reminder_loop']
__all__ = [n for n in __all__ if n in globals()]
