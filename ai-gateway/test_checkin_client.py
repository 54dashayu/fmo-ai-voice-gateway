import io
import unittest
from unittest.mock import patch

import checkin_client


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CheckinClientTests(unittest.TestCase):
    def test_requires_token(self):
        with patch.object(checkin_client, "TOKEN", ""):
            with self.assertRaises(RuntimeError):
                checkin_client.create_session("测试")

    def test_create_session_uses_bearer_without_exposing_it(self):
        captured = {}

        def open_request(request, timeout):
            captured["authorization"] = request.headers.get("Authorization")
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response(b'{"ok":true,"session":{"id":"ai-test"}}')

        with patch.object(checkin_client, "TOKEN", "secret"), patch.object(checkin_client, "BASE_URL", "https://example.test/checkin"), patch.object(
            checkin_client.urllib.request, "urlopen", side_effect=open_request
        ):
            session = checkin_client.create_session("测试")
        self.assertEqual(session["id"], "ai-test")
        self.assertEqual(captured["authorization"], "Bearer secret")
        self.assertTrue(captured["url"].endswith("/sessions"))
        self.assertEqual(captured["timeout"], 8)


if __name__ == "__main__":
    unittest.main()
