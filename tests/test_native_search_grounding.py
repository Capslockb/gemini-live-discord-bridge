from bridge_core import _append_google_search_tool


def test_native_google_search_is_registered_once_when_enabled() -> None:
    payload = {"tools": [{"functionDeclarations": [{"name": "web_search"}]}]}

    _append_google_search_tool(payload, enabled=True)
    _append_google_search_tool(payload, enabled=True)

    assert payload["tools"].count({"googleSearch": {}}) == 1
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "web_search"


def test_native_google_search_is_opt_in_to_preserve_live_model_quota(monkeypatch) -> None:
    monkeypatch.delenv("SORA_NATIVE_GOOGLE_SEARCH", raising=False)
    payload = {"tools": [{"functionDeclarations": [{"name": "web_search"}]}]}

    _append_google_search_tool(payload)

    assert not any("googleSearch" in tool for tool in payload["tools"])
