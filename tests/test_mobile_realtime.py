from __future__ import annotations

import base64
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mobile_realtime import create_mobile_realtime_app


class FakeBridge:
    instances: list["FakeBridge"] = []

    def __init__(self, **kwargs) -> None:
        self.output = kwargs["output_source"]
        self.on_event = kwargs["on_event"]
        self.api_key = kwargs["api_key"]
        self.context_id = kwargs["context_id"]
        self.profile = kwargs["user_profile"]
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

    def feed_audio(self, value: bytes) -> None:
        self.audio.append(value)

    async def send_text(self, value: str) -> None:
        self.text.append(value)

    def feed_video_frame(self, value: bytes, mime_type: str) -> None:
        self.video.append((value, mime_type))


class TestMobileRealtimeTransport(unittest.TestCase):
    def setUp(self) -> None:
        FakeBridge.instances.clear()
        self.app = create_mobile_realtime_app(
            internal_token="internal-test-token",
            bridge_factory=FakeBridge,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

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
                    "providerKey": "p" * 32,
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
        self.assertEqual(bridge.audio, [pcm_in])
        self.assertEqual(bridge.text, ["hello"])
        self.assertEqual(bridge.video, [(frame, "image/jpeg")])
        self.assertTrue(bridge.profile.is_tool_allowed("web_search"))
        self.assertFalse(bridge.profile.is_tool_allowed("not_permitted"))
        self.assertTrue(bridge.disconnected)

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
