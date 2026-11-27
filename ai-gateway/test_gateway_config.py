import json
import os
import tempfile
import unittest
from unittest import mock

import gateway_config


class GatewayConfigTests(unittest.TestCase):
    def test_blank_ui_secrets_keep_existing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "gateway-config.json")
            initial = {
                "mqtt_servers": [{"name": "primary", "host": "127.0.0.1", "port": 1884, "topic": "FMO/RAW", "password": "mqtt-secret"}],
                "providers": {"chat": {"provider": "dashscope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "api_key_env": "DASHSCOPE_API_KEY", "api_key": "model-secret"}},
            }
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(initial, stream)
            with mock.patch.object(gateway_config, "DEFAULT_GATEWAY_CONFIG_PATH", path):
                gateway_config.merge_and_save({
                    "mqtt_servers": [{"name": "primary", "host": "127.0.0.1", "port": 1884, "topic": "FMO/RAW", "password": ""}],
                    "providers": {"chat": {"provider": "dashscope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max", "api_key_env": "DASHSCOPE_API_KEY"}},
                }, target_path=path)
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved["mqtt_servers"][0]["password"], "mqtt-secret")
            self.assertEqual(saved["providers"]["chat"]["api_key"], "model-secret")

    def test_rejects_non_bailian_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "gateway-config.json")
            with self.assertRaisesRegex(ValueError, "only Alibaba"):
                gateway_config.merge_and_save({"providers": {"chat": {
                    "provider": "other", "base_url": "https://example.com/v1",
                    "model": "other-model", "api_key_env": "OTHER_API_KEY",
                }}}, target_path=path)

    def test_accepts_bailian_workspace_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "gateway-config.json")
            gateway_config.merge_and_save({"providers": {"chat": {
                "provider": "dashscope",
                "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-plus", "api_key_env": "DASHSCOPE_API_KEY",
            }}}, target_path=path)


if __name__ == "__main__":
    unittest.main()
