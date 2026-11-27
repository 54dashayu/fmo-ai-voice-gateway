import unittest
import os
import tempfile

import service_control


class ServiceControlTest(unittest.TestCase):
    def setUp(self):
        service_control._LAST_RECONNECT_AT = 0.0

    def test_restarts_only_ai_mqtt_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "request")
            result = service_control.reconnect_voice_link(now=100, request_path=path)
            self.assertTrue(result["ok"])
            with open(path, encoding="ascii") as stream:
                self.assertEqual(stream.read(), "100\n")

    def test_rate_limits_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "request")
            service_control.reconnect_voice_link(now=100, request_path=path)
            with self.assertRaises(RuntimeError):
                service_control.reconnect_voice_link(now=110, request_path=path)


if __name__ == "__main__":
    unittest.main()
