from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


def load_script(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


configure = load_script("configure.py")
packager = load_script("build_package.py")


class PackagingTests(unittest.TestCase):
    def test_atomic_config_writes_keep_mode_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "gateway.env"
            env_path.write_text("FMO_AI_ENABLED=false\n", encoding="utf-8")
            os.chmod(env_path, 0o600)
            configure.write_env(env_path, {"FMO_KB_ENABLED": "false"})
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
            self.assertIn("FMO_KB_ENABLED=false", env_path.read_text(encoding="utf-8"))
            config_path = Path(directory) / "gateway-config.json"
            configure.write_json(config_path, {"knowledge": {"enabled": False}})
            self.assertFalse(json.loads(config_path.read_text())["knowledge"]["enabled"])
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_package_filter_rejects_credentials_and_runtime_data(self):
        self.assertFalse(packager.allowed(Path("ai-gateway/.env")))
        self.assertFalse(packager.allowed(Path("runtime/status.json")))
        self.assertFalse(packager.allowed(Path("capture.pcap")))
        self.assertTrue(packager.allowed(Path("ai-gateway/.env.example")))


if __name__ == "__main__":
    unittest.main()
