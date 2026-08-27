from __future__ import annotations

from pathlib import Path
import base64
import json
import stat

import delegation_agent
import pytest


def _jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_opencode_policy_denies_shell_egress_and_external_credentials() -> None:
    policy = delegation_agent._opencode_permission_policy()

    assert policy["read"] == "allow"
    assert policy["edit"] == "allow"
    assert policy["bash"] == "deny"
    assert policy["webfetch"] == "deny"
    assert policy["websearch"] == "deny"
    assert policy["external_directory"] == "deny"
    assert policy["task"] == "deny"


def test_opencode_launcher_pins_verified_model_and_keeps_session_id(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_tmux_exec(workdir, window_name, command, log_path):
        captured.update(
            workdir=workdir,
            window_name=window_name,
            command=command,
            log_path=log_path,
        )
        return {"session_id": workdir, "tmux_window": window_name, "log_path": log_path}

    monkeypatch.setattr(delegation_agent, "_tmux_exec", fake_tmux_exec)
    monkeypatch.setenv("SORA_OPENCODE_MODEL", "opencode/mimo-v2.5-free")

    result = delegation_agent._run_opencode(
        "Inspect status",
        "voice-123",
        str(tmp_path),
        {"binary": "/home/caps/.local/bin/opencode"},
    )

    assert result["session_id"] == "voice-123"
    assert "--model opencode/mimo-v2.5-free" in str(captured["command"])
    assert " -- " in str(captured["command"])
    assert "Inspect status" in str(captured["command"])
    command = str(captured["command"])
    assert command.startswith("/bin/bash -c ")
    assert "trap cleanup EXIT" in command
    assert "--ro-bind / /" not in command
    assert "--ro-bind /usr /usr" in command
    assert "--setenv OPENCODE_PERMISSION" in command
    assert "/home/caps/.ssh" not in command
    assert f"--bind {tmp_path} /workspace" in command
    assert command.index(" --tmpfs /tmp ") < command.index(f" --bind {tmp_path} /workspace")


def test_opencode_uses_ephemeral_writable_state_overlay(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "opencode-source"
    state_root = tmp_path / "opencode-state"
    workdir = tmp_path / "work"
    source.mkdir()
    workdir.mkdir()
    (source / "auth.json").write_text('{"credential":"placeholder"}')
    (source / "account.json").write_text('{"account":"placeholder"}')
    captured: dict[str, str] = {}

    def fake_tmux_exec(workdir_arg, window_name, command, log_path):
        del workdir_arg, window_name, log_path
        captured["command"] = command
        return {"tmux_window": "test", "log_path": "/tmp/test.log"}

    monkeypatch.setattr(delegation_agent, "_tmux_exec", fake_tmux_exec)
    monkeypatch.setenv("SORA_OPENCODE_DATA_DIR", str(source))
    monkeypatch.setenv("SORA_DELEGATION_STATE_ROOT", str(state_root))

    delegation_agent._run_opencode(
        "Inspect status",
        "voice-state",
        str(workdir),
        {"binary": "/home/caps/.local/bin/opencode"},
    )

    states = list(state_root.iterdir())
    assert len(states) == 1
    state = states[0]
    assert (state / "auth.json").read_text() == (source / "auth.json").read_text()
    assert (state / "auth.json").stat().st_mode & 0o777 == 0o600
    assert f"--bind {state} /home/sora/.local/share/opencode" in captured["command"]
    assert str(state / "auth.json") not in captured["command"]


def test_delegation_runtime_files_are_private_and_randomized(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "runs"
    monkeypatch.setenv("SORA_DELEGATION_RUN_ROOT", str(root))

    first = delegation_agent._prepare_delegation_run_files("opencode", "voice-private")
    second = delegation_agent._prepare_delegation_run_files("opencode", "voice-private")

    assert first["run_dir"] != second["run_dir"]
    for prepared in (first, second):
        run_dir = Path(prepared["run_dir"])
        log = Path(prepared["log_path"])
        status = Path(prepared["status_path"])
        assert run_dir.parent == root
        assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
        assert stat.S_IMODE(status.stat().st_mode) == 0o600
        assert log.parent == status.parent == run_dir


def test_tmux_exec_rejects_symlink_replacement_before_launch(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    log = tmp_path / "output.log"
    log.symlink_to(victim)

    result = delegation_agent._tmux_exec(str(tmp_path), "symlink", "true", str(log))

    assert result["error"] == "could not prepare delegation status files"
    assert victim.read_text() == "unchanged"


def test_private_metadata_restores_status_lookup_after_registry_reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SORA_DELEGATION_RUN_ROOT", str(tmp_path / "runs"))
    prepared = delegation_agent._prepare_delegation_run_files("opencode", "voice-restore")
    recorded = {
        **prepared,
        "session_id": "voice-restore",
        "active_platform": "opencode",
        "tmux_window": "del-voice-restore",
    }
    delegation_agent._record_delegation(recorded)
    delegation_agent._ACTIVE_DELEGATIONS.clear()

    restored = delegation_agent.lookup_delegation("voice-restore", "opencode")

    assert restored is not None
    assert restored["log_path"] == prepared["log_path"]
    assert Path(restored["log_path"]).parent == Path(prepared["run_dir"])


def test_subscription_error_marks_backend_broken(tmp_path: Path) -> None:
    log = tmp_path / "delegate.log"
    log.write_text("Error: this model requires a subscription or extra usage, upgrade for access")

    reason = delegation_agent.detect_broken_log(str(log))

    assert reason is not None


def test_opencode_usage_page_marks_execution_broken(tmp_path: Path) -> None:
    log = tmp_path / "opencode-help.log"
    log.write_text("opencode run [message..]\n\nrun opencode with a message\nOptions:\n  --help\n")

    reason = delegation_agent.detect_broken_log(str(log))

    assert reason is not None
    assert "usage" in reason.lower() or "help" in reason.lower()


def test_observe_delegation_returns_completed_sanitized_output(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "completed.log"
    status = tmp_path / "completed.status"
    log.write_text("LIVE_TOOLS_VERIFIED\napi_key=private-value\n")
    status.write_text("0\n")
    monkeypatch.setattr(delegation_agent, "_tmux_window_active", lambda _: False)

    observed = delegation_agent.observe_delegation(
        {"tmux_window": "del-test", "log_path": str(log), "status_path": str(status)},
        wait_seconds=0,
    )

    assert observed["status"] == "completed"
    assert "LIVE_TOOLS_VERIFIED" in observed["output_tail"]
    assert "private-value" not in observed["output_tail"]
    assert "[REDACTED]" in observed["output_tail"]


def test_partial_output_without_exit_marker_never_reports_completed(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "partial.log"
    log.write_text("I changed one file before the worker crashed\n")
    monkeypatch.setattr(delegation_agent, "_tmux_window_active", lambda _: False)

    observed = delegation_agent.observe_delegation(
        {"tmux_window": "del-test", "log_path": str(log), "status_path": str(tmp_path / "missing.status")},
        wait_seconds=0,
    )

    assert observed["status"] == "failed"
    assert "completion" in observed["error"].lower() or "marker" in observed["error"].lower()


def test_delegation_without_workdir_uses_isolated_session_repo(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def fake_run(prompt, session_id, workdir, info):
        del prompt, session_id, info
        captured["workdir"] = workdir
        return {"status": "started"}

    monkeypatch.setattr(delegation_agent, "_run_opencode", fake_run)
    monkeypatch.setenv("SORA_DELEGATION_SCRATCH_ROOT", str(tmp_path))

    result = delegation_agent.execute_delegation("Return OK", "opencode", "voice-scope-1")

    workdir = Path(captured["workdir"])
    assert result["status"] == "started"
    assert workdir == tmp_path / "voice-scope-1"
    assert (workdir / ".git").is_dir()


def test_delegation_rejects_missing_explicit_workdir(tmp_path: Path) -> None:
    result = delegation_agent.execute_delegation(
        "Return OK",
        "opencode",
        "voice-scope-2",
        str(tmp_path / "missing"),
    )

    assert "error" in result
    assert "workdir" in result["error"].lower()


def test_delegation_rejects_path_traversal_session_id(monkeypatch, tmp_path: Path) -> None:
    launched: list[str] = []
    monkeypatch.setenv("SORA_DELEGATION_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setattr(
        delegation_agent,
        "_run_opencode",
        lambda *args, **kwargs: launched.append("opencode") or {"status": "started"},
    )

    result = delegation_agent.execute_delegation(
        "Return OK",
        "opencode",
        "../../escape",
    )

    assert result["error"] == "invalid delegation session_id"
    assert launched == []


def test_delegation_rejects_explicit_workdir_outside_allowed_roots(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("SORA_DELEGATION_ALLOWED_ROOTS", str(allowed))

    result = delegation_agent.execute_delegation(
        "Return OK",
        "opencode",
        "voice-scope-3",
        str(outside),
    )

    assert "error" in result
    assert "allowed" in result["error"].lower()


def test_codex_launcher_uses_workspace_write_sandbox(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def fake_tmux_exec(workdir, window_name, command, log_path):
        del workdir, window_name, log_path
        captured["command"] = command
        return {"tmux_window": "test", "log_path": "/tmp/test.log"}

    monkeypatch.setattr(delegation_agent, "_tmux_exec", fake_tmux_exec)

    delegation_agent._run_codex(
        "Inspect status",
        "voice-124",
        str(tmp_path),
        {"binary": "/home/caps/.npm-global/bin/codex"},
    )

    assert "--sandbox workspace-write" in captured["command"]
    assert "danger-full-access" not in captured["command"]


def test_nonzero_marker_stops_original_before_fallback_launch(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    log = tmp_path / "opencode.log"
    status = tmp_path / "opencode.status"
    log.write_text("Error: this model requires a subscription or extra usage\n")
    status.write_text("1\n")

    def fake_execute(prompt, platform, session_id, workdir=None):
        del prompt, session_id, workdir
        events.append(f"launch:{platform}")
        if platform != "opencode":
            return {"active_platform": platform, "status": "started"}
        return {
            "active_platform": platform,
            "tmux_window": f"del-{platform}",
            "log_path": str(log),
            "status_path": str(status),
        }

    monkeypatch.setattr(delegation_agent, "execute_delegation", fake_execute)
    monkeypatch.setattr(delegation_agent, "is_platform_healthy", lambda _: True)
    monkeypatch.setattr(delegation_agent, "mark_platform_broken", lambda *args, **kwargs: None)
    monkeypatch.setattr(delegation_agent, "preflight_platform", lambda _: None)
    monkeypatch.setattr(delegation_agent, "choose_fallback", lambda platform: "codex" if platform == "opencode" else None)
    monkeypatch.setattr(delegation_agent, "_tmux_window_active", lambda _: False)
    monkeypatch.setattr(delegation_agent.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        delegation_agent,
        "_stop_delegation_window",
        lambda result: events.append(f"stop:{result['active_platform']}") or True,
        raising=False,
    )

    delegation_agent.execute_with_fallback(
        "Inspect status",
        "opencode",
        "voice-fallback",
        health_check_delay=0.1,
    )

    assert events == ["launch:opencode", "stop:opencode", "launch:codex"]


def test_success_marker_wins_over_error_looking_output(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    log = tmp_path / "completed.log"
    status = tmp_path / "completed.status"
    log.write_text("Reviewed text: subscription required was the bug we fixed\n")
    status.write_text("0\n")

    monkeypatch.setattr(
        delegation_agent,
        "execute_delegation",
        lambda *args, **kwargs: {
            "session_id": "marker-first",
            "active_platform": "opencode",
            "tmux_window": "del-opencode",
            "log_path": str(log),
            "status_path": str(status),
        },
    )
    monkeypatch.setattr(delegation_agent, "is_platform_healthy", lambda _: True)
    monkeypatch.setattr(delegation_agent, "preflight_platform", lambda _: None)
    monkeypatch.setattr(delegation_agent, "choose_fallback", lambda _: events.append("fallback") or "codex")
    monkeypatch.setattr(delegation_agent, "_tmux_window_active", lambda _: False)
    monkeypatch.setattr(delegation_agent.time, "sleep", lambda _: None)

    result = delegation_agent.execute_with_fallback(
        "Inspect status",
        "opencode",
        "marker-first",
        health_check_delay=0.1,
    )

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert events == []


def test_tmux_inspection_error_is_not_treated_as_inactive(monkeypatch) -> None:
    class Result:
        returncode = 2
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(delegation_agent.subprocess, "run", lambda *args, **kwargs: Result())

    with pytest.raises(RuntimeError, match="inspect delegation"):
        delegation_agent._tmux_window_active("del-test")


def test_stop_delegation_fails_closed_when_state_cannot_be_inspected(monkeypatch) -> None:
    monkeypatch.setattr(
        delegation_agent.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr(
        delegation_agent,
        "_tmux_window_active",
        lambda _: (_ for _ in ()).throw(RuntimeError("inspection unavailable")),
    )

    assert delegation_agent._stop_delegation_window({"tmux_window": "del-test"}) is False


def test_codex_preflight_rejects_expired_access_token(monkeypatch, tmp_path: Path) -> None:
    auth_home = tmp_path / "codex"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": _jwt({"exp": 1}),
                    "refresh_token": "present-but-unusable",
                },
            },
        ),
    )
    monkeypatch.setenv("CODEX_HOME", str(auth_home))

    reason = delegation_agent.preflight_platform("codex")

    assert reason is not None
    assert "expired" in reason.lower()
