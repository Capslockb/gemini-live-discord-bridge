from __future__ import annotations

import asyncio
import base64
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mobile_realtime import MobileAudioOutput, OutboundMux, _receive_loop, create_mobile_realtime_app


class FakeBridge:
    instances: list["FakeBridge"] = []

    def __init__(self, **kwargs) -> None:
        self.output = kwargs["output_source"]
        self.on_event = kwargs["on_event"]
        self.api_key = kwargs["api_key"]
        self.context_id = kwargs["context_id"]
        self.profile = kwargs["user_profile"]
        self.output_echo_guard = bool(kwargs.get("output_echo_guard"))
        self.echo_updates: list[bool] = []
        self.audio: list[bytes] = []
        self.text: list[str] = []
        self.video: list[tuple[bytes, str]] = []
        self.disconnected = False
        self._user_disconnect = False
        self.__class__.instances.append(self)

    async def connect(self) -> None:
        self.on_event({"kind": "session.ready", "contextId": self.context_id})
        self.output.feed(b"\x01\x00" * 16)

    async def disconnect(self) -> None:
        self.disconnected = True

    def set_output_echo_guard(self, enabled: bool) -> None:
        self.output_echo_guard = enabled
        self.echo_updates.append(enabled)

    def feed_audio(self, value: bytes) -> None:
        self.audio.append(value)

    async def send_text(self, value: str) -> None:
        self.text.append(value)

    def feed_video_frame(self, value: bytes, mime_type: str) -> None:
        self.video.append((value, mime_type))


class TestMobileRealtimeTransport(unittest.TestCase):
    def setUp(self) -> None:
        FakeBridge.instances.clear()
        self.server_gemini_key = "server-gemini-test-key"
        self.gemini_key_patch = mock.patch(
            "mobile_realtime.GEMINI_API_KEY",
            self.server_gemini_key,
            create=True,
        )
        self.gemini_key_patch.start()
        self.app = create_mobile_realtime_app(
            internal_token="internal-test-token",
            bridge_factory=FakeBridge,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.gemini_key_patch.stop()

    def test_websocket_requires_internal_auth(self):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/v1/realtime"):
                pass
        self.assertEqual(caught.exception.code, 4401)

    def test_audio_controls_transcripts_and_camera_share_one_context(self):
        headers = {"Authorization": "Bearer internal-test-token"}
        with self.client.websocket_connect("/v1/realtime", headers=headers) as ws:
            ws.send_json(
                {
                    "type": "session.start",
                    "contextId": "ctx-mobile-1",
                    "allowedTools": ["web_search", "not_permitted"],
                    "peerName": "user",
                }
            )
            ready = ws.receive_json()
            pcm_out = ws.receive_bytes()
            self.assertEqual(ready["kind"], "session.ready")
            self.assertEqual(ready["contextId"], "ctx-mobile-1")
            self.assertTrue(pcm_out)

            pcm_in = b"\x10\x00" * 160
            ws.send_bytes(pcm_in)
            ws.send_json({"type": "mic.mute"})
            ws.send_bytes(pcm_in)
            ws.send_json({"type": "mic.unmute"})
            ws.send_json({"type": "text.send", "text": "hello"})
            frame = b"jpeg-test-frame"
            ws.send_json(
                {
                    "type": "camera.frame",
                    "data": base64.b64encode(frame).decode("ascii"),
                }
            )
            ws.send_json({"type": "session.end"})

        bridge = FakeBridge.instances[-1]
        self.assertEqual(bridge.context_id, "ctx-mobile-1")
        self.assertEqual(bridge.api_key, self.server_gemini_key)
        self.assertEqual(bridge.audio, [pcm_in])
        self.assertEqual(bridge.text, ["hello"])
        self.assertEqual(bridge.video, [(frame, "image/jpeg")])
        self.assertTrue(bridge.profile.is_tool_allowed("web_search"))
        self.assertFalse(bridge.profile.is_tool_allowed("not_permitted"))
        self.assertFalse(bridge.output_echo_guard)
        self.assertTrue(bridge.disconnected)

    def test_audio_route_controls_echo_guard(self):
        class ScriptedSocket:
            def __init__(self):
                self.messages = [
                    {"type": "websocket.receive", "text": json.dumps({"type": "audio.route", "speakerphone": True})},
                    {"type": "websocket.receive", "text": json.dumps({"type": "audio.route", "speakerphone": False})},
                    {"type": "websocket.disconnect"},
                ]

            async def receive(self):
                return self.messages.pop(0)

        bridge = FakeBridge(
            output_source=MobileAudioOutput(OutboundMux()),
            on_event=lambda event: None,
            api_key="p" * 32,
            context_id="ctx-route",
            user_profile=None,
            output_echo_guard=False,
        )
        output = MobileAudioOutput(OutboundMux())

        asyncio.run(_receive_loop(ScriptedSocket(), bridge, output))

        self.assertEqual(bridge.echo_updates, [True, False])
        self.assertFalse(bridge.output_echo_guard)

    def test_canonical_playback_interrupted_frame_keeps_session_alive(self):
        headers = {"Authorization": "Bearer internal-test-token"}
        with self.client.websocket_connect("/v1/realtime", headers=headers) as ws:
            ws.send_json(
                {
                    "type": "session.start",
                    "contextId": "ctx-mobile-interrupt",
                    "providerKey": "p" * 32,
                }
            )
            self.assertEqual(ws.receive_json()["kind"], "session.ready")
            self.assertTrue(ws.receive_bytes())
            ws.send_json({"type": "playback.interrupted"})
            ws.send_json({"type": "text.send", "text": "session survived"})
            ws.send_json({"type": "session.end"})

        bridge = FakeBridge.instances[-1]
        self.assertEqual(bridge.text, ["session survived"])

    def test_malformed_session_never_echoes_provider_value(self):
        headers = {"Authorization": "Bearer internal-test-token"}
        with self.client.websocket_connect("/v1/realtime", headers=headers) as ws:
            ws.send_json(
                {
                    "type": "session.start",
                    "contextId": "invalid context",
                    "providerKey": "sensitive-test-value",
                }
            )
            error = ws.receive_json()
        self.assertEqual(error["kind"], "session.error")
        self.assertNotIn("sensitive-test-value", str(error))


if __name__ == "__main__":
    unittest.main()
