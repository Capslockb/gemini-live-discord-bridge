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

OPENCODE_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_OPENCODE_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}


OPENCODE_BIN = os.getenv("OPENCODE_BIN", "/home/caps/.local/bin/opencode")


OPENCODE_DEFAULT_MODEL = os.getenv("OPENCODE_DEFAULT_MODEL", "anthropic/claude-sonnet-4")


OPENCODE_TMUX_SESSION = os.getenv("OPENCODE_TMUX_SESSION", "opencode-voice")


_OPENCODE_SESSIONS: Dict[Any, Dict[str, Any]] = {}


_OPENCODE_CURRENT_USER: Optional[str] = None


_opencode_current_bridge: Optional[Any] = None


def _opencode_set_user(user_id: Optional[str]) -> None:
    """Set the current opencode user context (called from bridge executor before dispatch)."""
    global _OPENCODE_CURRENT_USER
    _OPENCODE_CURRENT_USER = user_id or None


def _opencode_key(session_name: str) -> Any:
    """Return the registry key for the current user + session."""
    return (_OPENCODE_CURRENT_USER, session_name)


def _opencode_session_label(key: Any) -> str:
    """Return a human-readable label for a session key (e.g. 'user42/refactor' or 'refactor')."""
    if isinstance(key, tuple):
        return f"{key[0]}/{key[1]}"
    return str(key)


def _opencode_sanitize_name(raw: str) -> str:
    """Normalize a session name to the form used as the registry key.

    Must match the sanitization in _run_opencode_tool() so status/stop/send
    lookups hit the same key that opencode_run created. Without this, a user
    calling opencode_run with name='Refactor' stores the session as
    'refactor' but cannot find it later by typing 'Stop session Refactor'.
    """
    import re as _re
    if not raw:
        return f"oc-{int(time.time())}"
    return _re.sub(r"[^a-z0-9-]", "-", raw.lower())[:32].strip("-") or f"oc-{int(time.time())}"


def _opencode_tmux_window_name(session_name: str) -> str:
    """Return tmux window name for a given opencode voice session.

    Per-user tmux session names: oc-<user_prefix>-<session> so two users
    running 'refactor' don't collide in the same tmux server.
    """
    prefix = ""
    if _OPENCODE_CURRENT_USER:
        prefix = re.sub(r"[^a-z0-9]", "", str(_OPENCODE_CURRENT_USER).lower())[:8] or "anon"
        return f"oc-{prefix}-{session_name}"
    return f"oc-{session_name}"


def _opencode_list_sessions() -> List[Dict[str, Any]]:
    """Return summary of tracked opencode sessions for the CURRENT user only."""
    if _OPENCODE_CURRENT_USER is None:
        items = [(k, v) for k, v in _OPENCODE_SESSIONS.items() if not isinstance(k, tuple)]
    else:
        items = [(k, v) for k, v in _OPENCODE_SESSIONS.items() if isinstance(k, tuple) and k[0] == _OPENCODE_CURRENT_USER]
    return [
        {
            "name": (k[1] if isinstance(k, tuple) else k),
            "user": (k[0] if isinstance(k, tuple) else None),
            "tmux_window": meta.get("tmux_window"),
            "goal": meta.get("goal", "")[:200],
            "created_at": meta.get("created_at"),
        }
        for k, meta in sorted(items, key=lambda kv: -kv[1].get("created_at", 0))
    ]


OPENCODE_WATCHER_ENABLED = os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER", "true").lower() in {"1", "true", "yes", "on"}


OPENCODE_WATCHER_POLL_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_POLL_SECONDS", "5"))


OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS", "30"))


OPENCODE_WATCHER_INITIAL_DELAY_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_INITIAL_DELAY_SECONDS", "60"))


_MILESTONE_RE = re.compile(
    r"(?i)("
    r"\berror\b|\bexception\b|\btraceback\b|\bfailed\b|\bfatal\b|"
    r"\btest(s)?\s*(pass(ed)?|fail(ed)?)\b|"
    r"\bcompile(d)?\s*(success(fully)?|error)?\b|"
    r"\bbuild\s*(success(fully)?|fail(ed)?)\b|"
    r"\bdone\b|\bcomplete(d)?\b|\bfinish(ed)?\b|"
    r"\bcommit\b|\bpush(ed)?\b|"
    r"\u2713|\u2717|"
    r")"
)


def _opencode_extract_progress(log_path: str, last_line_count: int, max_lines: int = 40) -> Tuple[str, int, bool]:
    """Read new lines from an opencode log file and build a progress summary.

    Returns:
        (progress_text, new_line_count, is_milestone)

    The progress_text is short enough to inject as a single text turn
    (~200-500 chars). If no new content, returns empty string.
    """
    try:
        p = Path(log_path)
        if not p.exists():
            return "", last_line_count, False
        with p.open("r", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        logger.debug("opencode watcher: read failed (%s): %s", log_path, exc)
        return "", last_line_count, False

    if len(lines) <= last_line_count:
        return "", last_line_count, False

    new_lines = lines[last_line_count:][-max_lines:]
    new_line_count = len(lines)

    # Strip blank lines and ANSI escape codes
    cleaned = []
    for ln in new_lines:
        s = re.sub(r"\x1b\[[0-9;]*m", "", ln.rstrip())
        if s.strip():
            cleaned.append(s)
    if not cleaned:
        return "", new_line_count, False

    # Milestone detection: scan the cleaned lines for any keyword
    is_milestone = any(_MILESTONE_RE.search(s) for s in cleaned)

    # Build a concise summary — show first 3 + last 5 lines if long
    if len(cleaned) <= 8:
        body = "\n".join(cleaned)
    else:
        head = cleaned[:3]
        tail = cleaned[-5:]
        body = "\n".join(head) + f"\n... ({len(cleaned) - 8} more lines) ...\n" + "\n".join(tail)

    if is_milestone:
        progress = f"[opencode milestone] {body}"
    else:
        progress = f"[opencode progress] {body}"

    # Cap length to keep the voice turn short
    if len(progress) > 600:
        progress = progress[:600] + "..."
    return progress, new_line_count, is_milestone


def _opencode_tmux_window_alive(tmux_session: str, window_name: str) -> bool:
    """Return True if the named tmux window still exists."""
    import subprocess
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", tmux_session, "-F", "#{window_name}"],
            capture_output=True,
            timeout=5,
        )
        if out.returncode != 0:
            return False
        windows = out.stdout.decode(errors="replace").splitlines()
        return window_name in windows
    except Exception:
        return False


_OPENCODE_WATCHERS: Dict[Any, "asyncio.Task"] = {}


_OPENCODE_BRIDGE_REFS: Dict[Any, Any] = {}


def _opencode_register_bridge(session_name: str, user_id: Optional[str], bridge: Any) -> None:
    """Store a weak ref to the bridge for the watcher's send_text calls."""
    import weakref
    key = (user_id, session_name)
    try:
        _OPENCODE_BRIDGE_REFS[key] = weakref.ref(bridge)
    except TypeError:
        # Bridge doesn't support weakref (e.g. compiled class) — skip
        pass


def _opencode_get_bridge(session_name: str, user_id: Optional[str]) -> Any:
    """Get the bridge instance for the watcher's send_text calls.

    Tries the per-session registry first (set by _run_opencode_tool_with_bridge).
    Falls back to the module-level _opencode_current_bridge if no per-session
    ref was registered yet.
    """
    key = (user_id, session_name)
    ref = _OPENCODE_BRIDGE_REFS.get(key)
    if ref is not None:
        bridge = ref()
        if bridge is not None:
            return bridge
        _OPENCODE_BRIDGE_REFS.pop(key, None)
    # Fallback: use the most recently active bridge (works when there's
    # only one active session per user, which is the common case)
    return _opencode_current_bridge


def _bridge_user_id(bridge: Any) -> Optional[str]:
    """Resolve the Discord user id from the active bridge without requiring a bound self."""
    if bridge is None:
        return None
    profile = getattr(bridge, "_user_profile", None)
    profile_id = getattr(profile, "discord_id", None) if profile is not None else None
    return profile_id or getattr(bridge, "_target_user_id", None)


async def _opencode_watcher_loop(
    session_name: str,
    tmux_session: str,
    tmux_window: str,
    log_path: str,
    user_id: Optional[str],
    goal: str,
    model: Optional[str],
) -> None:
    """Background task: watch an opencode log, inject progress into Gemini Live.

    Runs until the tmux window dies (task finished or killed) or the bridge
    disconnects. Sends voice updates with throttling + milestone detection.
    """
    import time as _time
    key = (user_id, session_name)
    last_line_count = 0
    last_voice_at: Optional[float] = None
    started_at = _time.monotonic()
    last_window_alive = True
    milestone_triggered = False
    final_summary_sent = False

    try:
        # Initial delay before any voice activity
        await asyncio.sleep(OPENCODE_WATCHER_INITIAL_DELAY_SECONDS)

        while True:
            # Check tmux window liveness
            alive = _opencode_tmux_window_alive(tmux_session, tmux_window)
            if not alive and last_window_alive:
                # The opencode session ended. Read final log and send summary.
                last_window_alive = False
                await asyncio.sleep(2.0)  # let tee flush
                progress, last_line_count, _ = _opencode_extract_progress(
                    log_path, last_line_count, max_lines=20
                )
                bridge = _opencode_get_bridge(session_name, user_id)
                elapsed = int(_time.monotonic() - started_at)
                mins, secs = divmod(elapsed, 60)
                elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
                final_body = ("Here is the final output:\n" + progress) if progress else "No output captured."
                final = (
                    f"[opencode finished after {elapsed_str}] "
                    f"Session '{session_name}' has ended. {final_body}"
                )
                if bridge is not None:
                    try:
                        await bridge.send_text(final)
                    except Exception:
                        pass
                # Webhook: opencode_finished
                try:
                    from webhook_dispatcher import emit_opencode_status, emit_opencode_transcript
                    emit_opencode_status(
                        "opencode_finished", session_name, final,
                        fields=[{"name": "Duration", "value": elapsed_str, "inline": True}],
                    )
                    if progress:
                        emit_opencode_transcript(session_name, progress[-1500:])
                except Exception:
                    pass
                # AFK breakout: also fire a proactive notification via the multi-channel
                # dispatcher (criterion #6). If B is still in voice, deliver() routes to
                # voice; otherwise it falls back to DM/webhook so B gets pinged even
                # when AFK. The voice send_text above already handled the in-voice
                # case, so we deduplicate by skipping voice-mode here.
                try:
                    from notification import deliver as _watcher_deliver
                    _watcher_deliver(
                        text=(
                            f"Opencode session '{session_name}' finished after {elapsed_str}. "
                            f"Goal was: {goal[:200]}"
                        ),
                        mode="auto",
                        bridge=bridge,
                        adapter=getattr(bridge, "_adapter", None) if bridge is not None else None,
                        user_id=user_id,
                        title="Opencode finished",
                        source="opencode_watcher",
                    )
                except Exception as exc:
                    logger.debug("opencode watcher: breakout notify failed: %s", exc)
                final_summary_sent = True
                logger.info(
                    "opencode watcher: session %s finished after %ss, final update sent",
                    session_name, elapsed,
                )
                break
            last_window_alive = alive

            # Read new log content
            progress, last_line_count, is_milestone = _opencode_extract_progress(
                log_path, last_line_count, max_lines=30
            )
            if progress:
                now = _time.monotonic()
                elapsed = int(now - started_at)
                mins, secs = divmod(elapsed, 60)
                elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
                # Throttle: only speak if enough time has passed OR milestone
                should_speak = False
                if is_milestone:
                    should_speak = True
                    milestone_triggered = True
                elif last_voice_at is None or (now - last_voice_at) >= OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS:
                    should_speak = True

                if should_speak:
                    # Don't barge in if the user is currently speaking
                    bridge = _opencode_get_bridge(session_name, user_id)
                    if bridge is None:
                        # Bridge gone — stop watching
                        break
                    last_input = getattr(bridge, "metrics", {}).get("last_input_monotonic")
                    if last_input is not None and (now - float(last_input)) < 5.0:
                        # User is speaking right now — defer this update
                        pass
                    else:
                        # Compose a turn that asks Gemini to speak the progress
                        turn = (
                            f"User is waiting for opencode session '{session_name}' "
                            f"(goal: {goal[:120]}). It has been running for {elapsed_str}. "
                            f"Here is the latest output — please summarize briefly for the user in voice.\n\n"
                            f"{progress}"
                        )
                        try:
                            await bridge.send_text(turn)
                            last_voice_at = now
                            # Webhook: progress (or milestone)
                            try:
                                from webhook_dispatcher import emit_opencode_status, emit_opencode_transcript
                                sub = "opencode_milestone" if is_milestone else "opencode_progress"
                                emit_opencode_status(
                                    sub, session_name, progress,
                                    fields=[{"name": "Elapsed", "value": elapsed_str, "inline": True}],
                                )
                                emit_opencode_transcript(session_name, progress)
                            except Exception:
                                pass
                        except Exception as exc:
                            logger.debug("opencode watcher: send_text failed: %s", exc)

            await asyncio.sleep(OPENCODE_WATCHER_POLL_SECONDS)
    except asyncio.CancelledError:
        # Watcher was cancelled (e.g. bridge disconnect or user opencode_stop).
        # Send a brief "stopped" notice if the bridge is still around.
        bridge = _opencode_get_bridge(session_name, user_id)
        if bridge is not None and not final_summary_sent:
            try:
                await bridge.send_text(
                    f"[opencode watcher stopped] Session '{session_name}' was stopped or bridge disconnected."
                )
            except Exception:
                pass
        raise
    except Exception as exc:
        logger.warning("opencode watcher: loop crashed: %s", exc, exc_info=True)
    finally:
        _OPENCODE_WATCHERS.pop(key, None)
        _OPENCODE_BRIDGE_REFS.pop(key, None)
        logger.debug("opencode watcher: cleaned up %s", key)


def _opencode_spawn_watcher(
    session_name: str,
    tmux_session: str,
    tmux_window: str,
    log_path: str,
    user_id: Optional[str],
    goal: str,
    model: Optional[str],
    bridge: Any,
) -> None:
    """Spawn a background watcher task. Idempotent (replaces existing)."""
    if not OPENCODE_WATCHER_ENABLED:
        return
    key = (user_id, session_name)
    # Cancel any prior watcher for this key
    prior = _OPENCODE_WATCHERS.pop(key, None)
    if prior is not None and not prior.done():
        prior.cancel()
    _opencode_register_bridge(session_name, user_id, bridge)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Not in an async context — defer watcher start to next call
        return
    task = loop.create_task(
        _opencode_watcher_loop(
            session_name=session_name,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            log_path=log_path,
            user_id=user_id,
            goal=goal,
            model=model,
        )
    )
    _OPENCODE_WATCHERS[key] = task
    logger.info(
        "opencode watcher: spawned for %s (user=%s, log=%s, poll=%.1fs, min_gap=%.1fs)",
        session_name, user_id, log_path,
        OPENCODE_WATCHER_POLL_SECONDS, OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS,
    )


def _opencode_stop_watcher(session_name: str, user_id: Optional[str]) -> None:
    """Cancel the watcher for a session (called from opencode_stop)."""
    key = (user_id, session_name)
    task = _OPENCODE_WATCHERS.pop(key, None)
    if task is not None and not task.done():
        task.cancel()
    _OPENCODE_BRIDGE_REFS.pop(key, None)


def _opencode_run_tmux(session_name: str, prompt: str, model: Optional[str], workdir: Optional[str]) -> Dict[str, Any]:
    """Spawn opencode in a new tmux window under the configured session.

    Returns {"name", "tmux_window", "tmux_session"}. Use opencode_status to tail.
    """
    import subprocess
    import shlex
    import time

    if not Path(OPENCODE_BIN).exists():
        return {"error": f"opencode binary not found at {OPENCODE_BIN}"}

    window_name = _opencode_tmux_window_name(session_name)
    model = model or OPENCODE_DEFAULT_MODEL
    workdir = workdir or str(Path.home())

    # Check tmux session exists; create if not
    check = subprocess.run(["tmux", "has-session", "-t", OPENCODE_TMUX_SESSION], capture_output=True)
    if check.returncode != 0:
        subprocess.run(["tmux", "new-session", "-d", "-s", OPENCODE_TMUX_SESSION, "-n", "_init"], check=False)

    # Kill any prior window with this name (re-run replaces old session)
    subprocess.run(["tmux", "kill-window", "-t", f"{OPENCODE_TMUX_SESSION}:{window_name}"], capture_output=True)

    # Build the opencode command. We use `opencode run` for one-shot task execution
    # with an explicit model. The full interactive TUI is reached via plain `opencode`.
    quoted_prompt = shlex.quote(prompt)
    quoted_model = shlex.quote(model)
    quoted_wd = shlex.quote(workdir)
    # Use a here-doc to feed the prompt to opencode run. -y auto-approves inside
    # the opencode session so the user doesn't get blocked by its own approvals;
    # the voice passthrough is for what the LIVE agent decides to surface.
    log_path = f"/tmp/opencode-{window_name}.log"
    cmd = (
        f"cd {quoted_wd} && "
        f"echo {quoted_prompt} | {OPENCODE_BIN} run --model {quoted_model} -y 2>&1 | "
        f"tee {shlex.quote(log_path)}; "
        f"echo '[opencode-voice] session ended, window will close in 60s'; "
        f"sleep 60"
    )

    create = subprocess.run(
        ["tmux", "new-window", "-d", "-t", OPENCODE_TMUX_SESSION, "-n", window_name, "bash", "-c", cmd],
        capture_output=True,
    )
    if create.returncode != 0:
        return {"error": f"tmux new-window failed: {create.stderr.decode(errors='replace').strip()}"}

    # Try to capture the pane PID for SIGINT support
    pane_pid_res = subprocess.run(
        ["tmux", "display-message", "-t", f"{OPENCODE_TMUX_SESSION}:{window_name}", "-p", "#{pane_pid}"],
        capture_output=True,
    )
    pane_pid = None
    if pane_pid_res.returncode == 0:
        try:
            pane_pid = int(pane_pid_res.stdout.decode().strip())
        except ValueError:
            pass

    _OPENCODE_SESSIONS[_opencode_key(session_name)] = {
        "tmux_window": window_name,
        "tmux_pane_pid": pane_pid,
        "created_at": time.time(),
        "goal": prompt,
        "model": model,
        "workdir": workdir,
        "log_path": f"/tmp/opencode-{window_name}.log",
        "user_id": _OPENCODE_CURRENT_USER,
    }
    # Webhook: opencode_started
    try:
        from webhook_dispatcher import emit_opencode_status
        emit_opencode_status(
            "opencode_started", session_name,
            f"Goal: {prompt[:200]}",
            fields=[{"name": "Model", "value": str(model or OPENCODE_DEFAULT_MODEL), "inline": True}],
        )
    except Exception:
        pass
    # Spawn the progress watcher so the user gets voice updates on long
    # opencode runs. Uses the module-global _opencode_current_bridge (set
    # by _run_opencode_tool_with_bridge) as a back-ref for send_text.
    _opencode_spawn_watcher(
        session_name=session_name,
        tmux_session=OPENCODE_TMUX_SESSION,
        tmux_window=window_name,
        log_path=f"/tmp/opencode-{window_name}.log",
        user_id=_OPENCODE_CURRENT_USER,
        goal=prompt,
        model=model,
        bridge=_opencode_current_bridge,
    )
    return {
        "result": {
            "name": session_name,
            "tmux_session": OPENCODE_TMUX_SESSION,
            "tmux_window": window_name,
            "model": model,
            "workdir": workdir,
            "tail_cmd": f"tmux attach -t {OPENCODE_TMUX_SESSION}:{window_name}",
            "log": f"/tmp/opencode-{session_name}.log",
            "next": (
                "Use opencode_status to poll progress, opencode_send to inject follow-up, "
                "opencode_stop to kill. Tell the user briefly what you spawned."
            ),
        }
    }


def _opencode_status(name: str, tail_lines: int = 40) -> Dict[str, Any]:
    """Return tail of opencode session log + whether the window still exists."""
    import subprocess

    meta = _OPENCODE_SESSIONS.get(_opencode_key(name))
    if not meta:
        return {"error": f"no opencode session named '{name}'. Active: {[_opencode_session_label(k) for k in _OPENCODE_SESSIONS if (not isinstance(k, tuple)) or k[0] == _OPENCODE_CURRENT_USER]}"}

    log_path = meta["log_path"]
    window = meta["tmux_window"]
    log_content = ""
    if Path(log_path).exists():
        try:
            with open(log_path, "r", errors="replace") as f:
                log_content = "".join(f.readlines()[-tail_lines:])
        except Exception as exc:
            log_content = f"[log read failed: {exc}]"

    alive = subprocess.run(
        ["tmux", "list-windows", "-t", OPENCODE_TMUX_SESSION, "-F", "#{window_name}"],
        capture_output=True,
    )
    windows = alive.stdout.decode(errors="replace").splitlines()
    is_alive = window in windows

    return {
        "result": {
            "name": name,
            "user": _OPENCODE_CURRENT_USER,
            "alive": is_alive,
            "log_tail": log_content,
            "goal": meta.get("goal", "")[:200],
            "model": meta.get("model"),
        }
    }


def _opencode_send(name: str, message: str) -> Dict[str, Any]:
    """Send a follow-up message into a running opencode session via tmux send-keys.

    Note: opencode run reads its prompt from stdin once, so for one-shot sessions
    this only works if the session is still on the `tee |` line awaiting input —
    i.e. when using interactive `opencode` (not `opencode run`). For run-mode
    sessions the message is appended to the log file for the agent to pick up.
    """
    import subprocess

    meta = _OPENCODE_SESSIONS.get(_opencode_key(name))
    if not meta:
        return {"error": f"no opencode session named '{name}' for current user"}
    window = meta["tmux_window"]

    # Try to deliver into the tmux pane (interactive sessions)
    send = subprocess.run(
        ["tmux", "send-keys", "-t", f"{OPENCODE_TMUX_SESSION}:{window}", message, "Enter"],
        capture_output=True,
    )
    sent_to_pane = send.returncode == 0
    # Also append to the log so the agent can read it later regardless
    try:
        with open(meta["log_path"], "a") as f:
            f.write(f"\n[voice-followup] {message}\n")
    except Exception:
        pass
    return {"result": {"name": name, "sent_to_pane": sent_to_pane, "appended_to_log": True}}


def _opencode_interrupt(name: str) -> Dict[str, Any]:
    """Send SIGINT (Ctrl-C) to a running opencode session so you can ask it a follow-up."""
    import subprocess
    import signal
    import os

    meta = _OPENCODE_SESSIONS.get(_opencode_key(name))
    if not meta:
        return {"error": f"no opencode session named '{name}' for current user"}
    
    window = meta["tmux_window"]
    # Cancel the watcher so it doesn't fire a final summary
    _opencode_stop_watcher(name, _OPENCODE_CURRENT_USER)
    
    method = "send_keys"
    pane_pid = meta.get("tmux_pane_pid")
    if pane_pid:
        try:
            os.killpg(pane_pid, signal.SIGINT)
            method = "sigint"
        except (ProcessLookupError, PermissionError):
            pass
    
    # Always fall back/ensure with send-keys for robustness
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{OPENCODE_TMUX_SESSION}:{window}", "C-c"],
        capture_output=True,
    )
    
    return {"result": {"name": name, "interrupted": True, "tmux_window": window, "method": method}}


def _opencode_stop(name: str) -> Dict[str, Any]:
    """Kill the opencode session's tmux window and remove from registry."""
    import subprocess

    meta = _OPENCODE_SESSIONS.pop(_opencode_key(name), None)
    if not meta:
        return {"error": f"no opencode session named '{name}' for current user"}
    window = meta["tmux_window"]
    # Cancel the progress watcher first so it doesn't fire a final summary
    # for a session we just killed.
    _opencode_stop_watcher(name, _OPENCODE_CURRENT_USER)
    subprocess.run(
        ["tmux", "kill-window", "-t", f"{OPENCODE_TMUX_SESSION}:{window}"],
        capture_output=True,
    )
    # Webhook: opencode_stopped
    try:
        from webhook_dispatcher import emit_opencode_status
        emit_opencode_status("opencode_stopped", name, "User requested stop")
    except Exception:
        pass
    return {"result": {"name": name, "killed": True, "tmux_window": window}}


_OPENCODE_FUNCTION_DECLARATIONS = [
    {
        "name": "opencode_run",
        "description": (
            "Spawn a full OpenCode coding agent in a tmux window to handle a coding/build task "
            "the user is asking for. Returns a session name to track. Always confirm with the user "
            "before invoking this if the task is non-trivial — they may want to run it themselves. "
            "Use for: code changes, refactors, building features, running tests, fixing bugs. "
            "Do NOT use for: simple questions, lookups, things the live voice channel can answer directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Plain-English description of what opencode should do",
                },
                "name": {
                    "type": "string",
                    "description": "Short session name (lowercase, no spaces), e.g. 'refactor-auth'",
                },
                "model": {
                    "type": "string",
                    "description": f"Model to use (default {OPENCODE_DEFAULT_MODEL})",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (default ~/)",
                },
            },
            "required": ["goal", "name"],
        },
    },
    {
        "name": "opencode_status",
        "description": (
            "Poll an opencode session: returns the last 40 log lines and whether the tmux window is "
            "still alive. Use this between tool calls to report progress to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name returned by opencode_run"},
                "tail_lines": {"type": "integer", "description": "How many recent log lines (default 40, max 200)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "opencode_list",
        "description": "List all tracked opencode sessions (name, tmux window, goal).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "opencode_send",
        "description": (
            "Send a follow-up message into a running opencode session. For interactive opencode "
            "sessions this is delivered live; for one-shot `opencode run` sessions it's appended "
            "to the log for the next status poll."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name"},
                "message": {"type": "string", "description": "Message to send"},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "opencode_stop",
        "description": "Kill a running opencode session's tmux window and forget it.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Session name"}},
            "required": ["name"],
        },
    },
    {
        "name": "opencode_interrupt",
        "description": "Send SIGINT (Ctrl-C) to a running opencode session so you can ask it a follow-up. Keeps the tmux window alive — use opencode_stop to kill it entirely. Falls back to tmux send-keys C-c if the process group signal fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name returned by opencode_run"}
            },
            "required": ["name"]
        }
    },
]


def _run_opencode_tool_with_bridge(
    name: str, args: Dict[str, Any], user_id: Optional[str], bridge: Any
) -> Dict[str, Any]:
    """Bridge entry point: set the per-user context then dispatch.

    The `bridge` reference is stored module-globally so the watcher (spawned
    in the executor's thread) can call back into send_text() from the
    gateway's event loop via the weak-ref registry. The bridge also exposes
    itself via a thread-local for the duration of the call.
    """
    global _opencode_current_bridge
    _opencode_set_user(user_id)
    _opencode_current_bridge = bridge
    try:
        return _run_opencode_tool(name, args)
    finally:
        _opencode_current_bridge = None


def _run_opencode_tool_with_user(name: str, args: Dict[str, Any], user_id: Optional[str]) -> Dict[str, Any]:
    """Legacy entry point: set the per-user context then dispatch (no bridge)."""
    _opencode_set_user(user_id)
    return _run_opencode_tool(name, args)


def _run_opencode_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch opencode_* tools. All handlers are synchronous and run in executor."""
    try:
        if name == "opencode_run":
            session_name = args.get("name") or f"oc-{int(time.time())}"
            # Sanitize name: lowercase, hyphens, no spaces, max 32 chars.
            # The same sanitization runs in _opencode_sanitize_name() so
            # status/send/stop lookups always hit the same registry key.
            session_name = _opencode_sanitize_name(session_name)
            return _opencode_run_tmux(
                session_name=session_name,
                prompt=args.get("goal", ""),
                model=args.get("model"),
                workdir=args.get("workdir"),
            )
        if name == "opencode_status":
            return _opencode_status(
                name=_opencode_sanitize_name(args.get("name", "")),
                tail_lines=min(max(int(args.get("tail_lines", 40)), 1), 200),
            )
        if name == "opencode_list":
            return {"result": {"sessions": _opencode_list_sessions()}}
        if name == "opencode_send":
            return _opencode_send(name=_opencode_sanitize_name(args.get("name", "")),
                                 message=args.get("message", ""))
        if name == "opencode_stop":
            return _opencode_stop(name=_opencode_sanitize_name(args.get("name", "")))
        if name == "opencode_interrupt":
            return _opencode_interrupt(name=_opencode_sanitize_name(args.get("name", "")))
        return {"error": f"Unknown opencode tool: {name}"}
    except Exception as exc:
        logger.exception("opencode tool %s crashed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


__all__ = ['OPENCODE_VOICE_TOOLS_ENABLED', 'OPENCODE_BIN', 'OPENCODE_DEFAULT_MODEL', 'OPENCODE_TMUX_SESSION', '_OPENCODE_SESSIONS', '_OPENCODE_CURRENT_USER', '_opencode_current_bridge', '_opencode_set_user', '_opencode_key', '_opencode_session_label', '_opencode_sanitize_name', '_opencode_tmux_window_name', '_opencode_list_sessions', 'OPENCODE_WATCHER_ENABLED', 'OPENCODE_WATCHER_POLL_SECONDS', 'OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS', 'OPENCODE_WATCHER_INITIAL_DELAY_SECONDS', '_MILESTONE_RE', '_opencode_extract_progress', '_opencode_tmux_window_alive', '_OPENCODE_WATCHERS', '_OPENCODE_BRIDGE_REFS', '_opencode_register_bridge', '_opencode_get_bridge', '_bridge_user_id', '_opencode_watcher_loop', '_opencode_spawn_watcher', '_opencode_stop_watcher', '_opencode_run_tmux', '_opencode_status', '_opencode_send', '_opencode_interrupt', '_opencode_stop', '_OPENCODE_FUNCTION_DECLARATIONS', '_run_opencode_tool_with_bridge', '_run_opencode_tool_with_user', '_run_opencode_tool']
__all__ = [n for n in __all__ if n in globals()]
