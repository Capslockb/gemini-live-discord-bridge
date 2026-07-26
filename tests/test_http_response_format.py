"""Regression tests for byte-correct sidecar HTTP response framing."""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "bridge_http.py"


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
        "_bridge_http_response_test_target",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class TestHttpResponseFormatting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge_http = _load_bridge_http()

    def _assert_framed(self, body: str, expected_value):
        response = self.bridge_http._format_response(200, body)
        header_bytes, separator, payload = response.partition(b"\r\n\r\n")
        self.assertEqual(separator, b"\r\n\r\n")

        header_lines = header_bytes.decode("ascii").split("\r\n")
        self.assertEqual(header_lines[0], "HTTP/1.1 200 OK")
        headers = {
            key.lower(): value.strip()
            for key, value in (line.split(":", 1) for line in header_lines[1:])
        }

        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["connection"], "close")
        self.assertEqual(int(headers["content-length"]), len(payload))
        self.assertEqual(payload, body.encode("utf-8"))
        self.assertEqual(json.loads(payload.decode("utf-8")), expected_value)

    def test_ascii_body(self):
        value = {"status": "ok"}
        self._assert_framed(json.dumps(value), value)

    def test_default_ascii_escaped_json(self):
        value = {"text": "Bună 👋"}
        body = json.dumps(value)
        self.assertTrue(body.isascii())
        self._assert_framed(body, value)

    def test_direct_unicode_json_body(self):
        value = {"text": "Bună 👋"}
        body = '{"text":"Bună 👋"}'
        self._assert_framed(body, value)

    def test_ensure_ascii_false_json(self):
        value = {"text": "Bună 👋"}
        body = json.dumps(value, ensure_ascii=False)
        self.assertFalse(body.isascii())
        self._assert_framed(body, value)

    def test_status_reason_mapping_is_preserved(self):
        response = self.bridge_http._format_response(413, "{}")
        self.assertTrue(response.startswith(b"HTTP/1.1 413 PAYLOAD TOO LARGE\r\n"))


if __name__ == "__main__":
    unittest.main()
