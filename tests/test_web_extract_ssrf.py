from __future__ import annotations

import socket

import pytest

import bridge_tools


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
    ],
)
def test_public_url_validator_rejects_non_public_literal_addresses(url: str) -> None:
    with pytest.raises(ValueError, match="public"):
        bridge_tools._validate_public_http_url(url)


def test_public_url_validator_rejects_mixed_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge_tools.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.20", 443)),
        ],
    )

    with pytest.raises(ValueError, match="public"):
        bridge_tools._validate_public_http_url("https://example.com/")


def test_public_fetch_revalidates_redirect_before_second_request(monkeypatch) -> None:
    calls: list[str] = []

    def fake_getaddrinfo(host, port, **kwargs):
        address = "8.8.8.8" if host == "example.com" else host
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    def fake_request(parsed, address):
        calls.append(f"{parsed.geturl()}@{address}")
        return 302, {"location": "http://127.0.0.1/admin"}, b""

    monkeypatch.setattr(bridge_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(bridge_tools, "_request_public_http_once", fake_request)

    with pytest.raises(ValueError, match="public"):
        bridge_tools._fetch_public_http("https://example.com/start")

    assert calls == ["https://example.com/start@8.8.8.8"]


def test_public_url_validator_rejects_credentials_and_nonstandard_ports(monkeypatch) -> None:
    monkeypatch.setattr(
        bridge_tools.socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))],
    )

    with pytest.raises(ValueError, match="credentials"):
        bridge_tools._validate_public_http_url("https://user:pass@example.com/")
    with pytest.raises(ValueError, match="port"):
        bridge_tools._validate_public_http_url("https://example.com:8443/")
