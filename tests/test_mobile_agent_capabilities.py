from __future__ import annotations

import delegation_agent
import requests
import json
import sys
import time
import types

from bridge_config import BASE_SYSTEM_PROMPT
from bridge_decls import _LOCAL_FUNCTION_DECLARATIONS
import bridge_tools
from bridge_tools import _run_local_tool
from mobile_realtime import _configured_tool_allowlist


ACTION_TOOLS = {
    "web_search",
    "web_extract",
    "local_honcho",
    "local_delegate_quick",
    "local_delegate_execute",
    "local_delegate_status",
    "local_delegate_health",
}


def test_mobile_live_exposes_real_action_tools() -> None:
    declared = {item["name"] for item in _LOCAL_FUNCTION_DECLARATIONS}
    assert ACTION_TOOLS <= _configured_tool_allowlist()
    assert {name for name in ACTION_TOOLS if name.startswith("local_")} <= declared


def test_live_prompt_executes_reversible_actions_without_confirmation() -> None:
    normalized = " ".join(BASE_SYSTEM_PROMPT.lower().split())
    assert "do not ask for confirmation before reversible" in normalized
    assert "narrate" in normalized


def test_quick_delegation_executes_in_one_tool_call(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_execute_with_fallback(prompt, platform, session_id, workdir=None, health_check_delay=5.0):
        captured.update(
            prompt=prompt,
            platform=platform,
            session_id=session_id,
            workdir=workdir,
            health_check_delay=health_check_delay,
        )
        return {"session_id": session_id, "active_platform": platform, "status": "started"}

    monkeypatch.setattr(delegation_agent, "execute_with_fallback", fake_execute_with_fallback)

    response = _run_local_tool(
        "local_delegate_quick",
        {
            "goal": "Inspect the repository status and report it",
            "platform": "codex",
            "workdir": str(tmp_path),
        },
    )

    assert response["result"]["status"] == "started"
    assert captured["platform"] == "codex"
    assert "Inspect the repository status" in str(captured["prompt"])


def test_concurrent_quick_delegations_use_unique_session_ids(monkeypatch) -> None:
    session_ids: list[str] = []

    def fake_execute(prompt, platform, session_id, workdir=None, health_check_delay=5.0):
        del prompt, platform, workdir, health_check_delay
        session_ids.append(session_id)
        return {"session_id": session_id, "active_platform": "opencode", "status": "started"}

    monkeypatch.setattr(delegation_agent, "execute_with_fallback", fake_execute)
    monkeypatch.setattr(time, "time", lambda: 1234.567)

    _run_local_tool("local_delegate_quick", {"goal": "First", "platform": "opencode"})
    _run_local_tool("local_delegate_quick", {"goal": "Second", "platform": "opencode"})

    assert len(session_ids) == 2
    assert session_ids[0] != session_ids[1]


def test_live_delegate_execute_rejects_unsandboxed_backends(monkeypatch) -> None:
    launched: list[str] = []

    def fake_execute(prompt, platform, session_id, workdir=None, health_check_delay=5.0):
        del prompt, session_id, workdir, health_check_delay
        launched.append(platform)
        return {"status": "started"}

    monkeypatch.setattr(delegation_agent, "execute_with_fallback", fake_execute)

    responses = [
        _run_local_tool(
            "local_delegate_execute",
            {"prompt": "Inspect", "platform": platform, "session_id": f"unsafe-{platform}"},
        )
        for platform in ("gemini", "numasec", "hermes-api")
    ]

    assert [response["error"] for response in responses] == [
        "unsupported sandboxed delegation platform",
        "unsupported sandboxed delegation platform",
        "unsupported sandboxed delegation platform",
    ]
    quick = _run_local_tool(
        "local_delegate_quick",
        {"goal": "Inspect", "platform": "hermes-api"},
    )
    health = _run_local_tool(
        "local_delegate_health",
        {"action": "mark", "platform": "hermes-api", "reason": "raw bypass"},
    )
    assert quick["error"] == "unsupported sandboxed delegation platform"
    assert health["error"] == "unsupported sandboxed delegation platform"
    assert launched == []


def test_live_delegate_suggest_filters_unsandboxed_backends(monkeypatch) -> None:
    monkeypatch.setattr(
        delegation_agent,
        "suggest_platform",
        lambda **kwargs: {
            "suggestion": "hermes-api",
            "available_platforms": ["hermes-api", "opencode", "codex"],
            "reason": "host tools",
        },
    )
    monkeypatch.setattr(delegation_agent, "get_health_snapshot", lambda: {})

    response = _run_local_tool(
        "local_delegate_suggest",
        {"goal": "Inspect", "project_size": "small", "scope": "code", "complexity": "low"},
    )["result"]

    assert response["available_platforms"] == ["opencode", "codex"]
    assert response["suggestion"] == "opencode"
    assert response["unsafe_platforms"] == ["hermes-api"]


def test_web_plugin_discovery_cannot_shadow_local_delegation_module(monkeypatch, tmp_path) -> None:
    def fake_execute(prompt, platform, session_id, workdir=None, health_check_delay=5.0):
        del prompt, workdir, health_check_delay
        return {"session_id": session_id, "active_platform": platform, "status": "started"}

    monkeypatch.setattr(delegation_agent, "execute_with_fallback", fake_execute)
    monkeypatch.setitem(sys.modules, "delegation_agent", types.ModuleType("delegation_agent"))

    response = _run_local_tool(
        "local_delegate_quick",
        {"goal": "Report status", "platform": "opencode", "workdir": str(tmp_path)},
    )

    assert response["result"]["status"] == "started"


def test_honcho_search_is_query_safe_and_bounded(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"content": ("memory detail " * 1000) + str(i)} for i in range(8)]

    def fake_post(url, json, headers, timeout):
        del url, headers
        captured["query"] = json["query"]
        captured["limit"] = json["limit"]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)

    response = _run_local_tool("local_honcho", {"query": "", "limit": 99})
    excerpts = response["result"]["excerpts"]

    assert captured["query"]
    assert int(captured["limit"]) <= 5
    assert int(captured["timeout"]) <= 5
    assert len(excerpts) <= 5
    assert sum(len(item) for item in excerpts) <= 3000


def test_web_extract_falls_back_when_configured_plugin_is_disabled(monkeypatch) -> None:
    fake = types.ModuleType("tools.web_tools")

    async def web_extract_tool(urls):
        del urls
        return json.dumps(
            {
                "success": False,
                "error": "web.extract_backend is set to 'tavily', but its plugin is disabled in config",
            },
        )

    fake.web_extract_tool = web_extract_tool
    fake.web_search_tool = lambda query, limit: "{}"
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake)
    monkeypatch.setattr(
        bridge_tools,
        "_basic_web_extract",
        lambda urls: {"result": {"success": True, "fallback": True, "urls": urls}},
    )

    response = bridge_tools._run_web_tool("web_extract", {"urls": ["https://example.com"]})

    assert response["result"]["fallback"] is True


def test_delegate_status_reads_completed_backend_output(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    session_id = "status-contract-test"
    monkeypatch.setenv("SORA_DELEGATION_RUN_ROOT", str(tmp_path / "runs"))
    prepared = delegation_agent._prepare_delegation_run_files("opencode", session_id)
    log = Path(prepared["log_path"])
    status = Path(prepared["status_path"])
    log.write_text("STATUS_TOOL_COMPLETE\n")
    status.write_text("0\n")
    delegation_agent._record_delegation(
        {
            **prepared,
            "session_id": session_id,
            "active_platform": "opencode",
            "tmux_window": "del-status-contract-test",
        },
    )
    monkeypatch.setattr(delegation_agent, "_tmux_window_active", lambda _: False)
    try:
        response = _run_local_tool(
            "local_delegate_status",
            {"sessionId": session_id, "platform": "opencode"},
        )
    finally:
        delegation_agent._ACTIVE_DELEGATIONS.pop(session_id, None)

    assert response["result"]["status"] == "completed"
    assert "STATUS_TOOL_COMPLETE" in response["result"]["output_tail"]


def test_delegate_status_rejects_path_traversal_inputs() -> None:
    bad_session = _run_local_tool(
        "local_delegate_status",
        {"sessionId": "../../etc/passwd", "platform": "opencode"},
    )
    bad_platform = _run_local_tool(
        "local_delegate_status",
        {"sessionId": "safe-session", "platform": "../../etc"},
    )

    assert bad_session["error"] == "invalid delegation session_id"
    assert bad_platform["error"] == "unsupported sandboxed delegation platform"
