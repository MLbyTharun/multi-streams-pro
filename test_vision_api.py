import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from PIL import Image

import scene_config
import vision_api


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(content="DONE", tokens=3):
    body = {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": tokens}}
    return FakeResponse(json.dumps(body).encode())


class VisionApiTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(scene_config, API_BASE_URL="https://x/v1/", API_KEY="k", MODEL_NAME="m")
        self.config.start()
        self.image = Image.new("RGB", (800, 400), "red")

    def tearDown(self):
        self.config.stop()

    def test_sends_openai_compatible_payload(self):
        with patch.object(vision_api.urllib.request, "urlopen", return_value=reply(" DONE\n")) as urlopen:
            result = vision_api.complete("prompt", [("Camera 1, frame 0", self.image)])
        self.assertEqual(result, ("DONE", 3))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://x/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer k")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "m")
        parts = body["messages"][0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "prompt"})
        self.assertEqual(parts[1], {"type": "text", "text": "Camera 1, frame 0"})
        self.assertTrue(parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_no_auth_header_when_key_empty(self):
        with patch.object(scene_config, "API_KEY", ""), patch.dict("os.environ", {}, clear=True):
            with patch.object(vision_api.urllib.request, "urlopen", return_value=reply()) as urlopen:
                vision_api.complete("prompt", [])
        self.assertIsNone(urlopen.call_args.args[0].get_header("Authorization"))

    def test_missing_configuration_raises(self):
        with patch.object(scene_config, "API_BASE_URL", ""), patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "API_BASE_URL"):
                vision_api.complete("prompt", [])

    def test_environment_fills_empty_config(self):
        env = {"SCENE_API_BASE_URL": "https://env/v1", "SCENE_MODEL_NAME": "env-model"}
        with patch.multiple(scene_config, API_BASE_URL="", MODEL_NAME=""), patch.dict("os.environ", env):
            with patch.object(vision_api.urllib.request, "urlopen", return_value=reply()) as urlopen:
                vision_api.complete("prompt", [])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://env/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["model"], "env-model")

    def test_retries_then_raises_on_server_error(self):
        error = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
        with patch.object(vision_api.urllib.request, "urlopen", side_effect=error) as urlopen, \
                patch.object(vision_api.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "503"):
                vision_api.complete("prompt", [])
        self.assertEqual(urlopen.call_count, scene_config.MAX_RETRIES + 1)

    def test_client_error_is_not_retried(self):
        error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"bad key"))
        with patch.object(vision_api.urllib.request, "urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "401"):
                vision_api.complete("prompt", [])
        self.assertEqual(urlopen.call_count, 1)

    def test_malformed_response_raises(self):
        with patch.object(vision_api.urllib.request, "urlopen", return_value=FakeResponse(b'{"choices":[]}')):
            with self.assertRaises(RuntimeError):
                vision_api.complete("prompt", [])


if __name__ == "__main__":
    unittest.main()
