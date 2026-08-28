"""Runtime regression tests for mutating sidecar route authentication."""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "bridge_http.py"
SECRET = "test-control-secret"
MUTATING_ROUTES = {
    "/stop": 200,
    "/say": 400,
    "/frame": 400,
    "/notify": 200,
}


class _Reader:
    def __init__(self, raw_request: bytes):
        header, _, body = raw_request.partition(b"\r\n\r\n")
        self._lines = [line + b"\r\n" for line in header.split(b"\r\n")]
        self._lines.append(b"\r\n")
        self._body = bytearray(body)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def readexactly(self, size: int) -> bytes:
        if len(self._body) < size:
            raise asyncio.IncompleteReadError(bytes(self._body), size)
        result = bytes(self._body[:size])
        del self._body[:size]
        return result


class _Writer:
    def __init__(self):
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    @property
    def status(self) -> int:
        status_line = bytes(self.buffer).split(b"\r\n", 1)[0]
        return int(status_line.split()[1])


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_bridge_http():
    stubs = {
        "numpy": _stub_module("numpy"),
        "bridge_config": _stub_module("bridge_config", VIDEO_MAX_BYTES=1024),
        "bridge_core": _stub_module("bridge_core", VoiceLiveBridge=object),
        "bridge_email": _stub_module(
            "bridge_email",
            _start_email_reminder_loop=lambda *_args, **_kwargs: None,
        ),
    }
    spec = importlib.util.spec_from_file_location(
        "_bridge_http_auth_test_target",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class TestMutatingRouteAuthentication(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge_http = _load_bridge_http()

    async def _request(self, route: str, presented_secret=None, plugin_secret=SECRET):
        headers = []
        if presented_secret is not None:
            headers.append(f"X-Api-Secret: {presented_secret}")
        raw = (
            f"GET {route} HTTP/1.1\r\n"
            + "\r\n".join(headers)
            + "\r\n\r\n"
        ).encode()

        plugin = _stub_module(
            "discord_voice_live",
            CONTROL_API_SECRET=plugin_secret,
        )
        notification = _stub_module(
            "notification",
            deliver=lambda **_kwargs: {"status": "ok"},
        )
        reader = _Reader(raw)
        writer = _Writer()
        with patch.dict(
            sys.modules,
            {
                "discord_voice_live": plugin,
                "discord_voice_live_bridge": None,
                "notification": notification,
            },
        ):
            await self.bridge_http.handle_http_request(reader, writer)
        return writer

    async def test_missing_incorrect_and_correct_secret_for_each_mutating_route(self):
        for route, expected_authorized_status in MUTATING_ROUTES.items():
            with self.subTest(route=route, credential="missing"):
                writer = await self._request(route, presented_secret=None)
                self.assertEqual(writer.status, 401)

            with self.subTest(route=route, credential="incorrect"):
                writer = await self._request(route, presented_secret="wrong")
                self.assertEqual(writer.status, 401)

            with self.subTest(route=route, credential="correct"):
                writer = await self._request(route, presented_secret=SECRET)
                self.assertEqual(writer.status, expected_authorized_status)

    async def test_missing_plugin_module_returns_controlled_500(self):
        reader = _Reader(b"GET /stop HTTP/1.1\r\nX-Api-Secret: anything\r\n\r\n")
        writer = _Writer()
        with patch.dict(
            sys.modules,
            {
                "discord_voice_live": None,
                "discord_voice_live_bridge": None,
            },
        ):
            await self.bridge_http.handle_http_request(reader, writer)
        self.assertEqual(writer.status, 500)
        self.assertIn(b"control secret unavailable", writer.buffer)

    async def test_empty_plugin_secret_returns_controlled_500(self):
        writer = await self._request(
            "/stop",
            presented_secret="anything",
            plugin_secret="",
        )
        self.assertEqual(writer.status, 500)
        self.assertIn(b"control secret not initialised", writer.buffer)


if __name__ == "__main__":
    unittest.main()
