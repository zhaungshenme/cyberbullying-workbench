from dataclasses import FrozenInstanceError
from email.message import Message
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gateway as module
from gateway import GatewayError, Handler, NetworkConfig, allowed_source


class NetworkConfigTests(unittest.TestCase):
    def test_defaults_preserve_existing_network_policy(self):
        config = NetworkConfig.load({})
        self.assertEqual((config.bind_address, config.client_address, config.port),
                         ("192.168.0.1", "192.168.0.129", 8444))
        self.assertTrue(allowed_source(config.client_address, "/infer", config))
        self.assertFalse(allowed_source("127.0.0.1", "/infer", config))

    def test_configured_source_replaces_default_and_only_one_source_is_allowed(self):
        config = NetworkConfig.load({"CB_GATEWAY_BIND_ADDRESS": "10.12.0.2",
            "CB_GATEWAY_CLIENT_ADDRESS": "10.12.0.3", "CB_GATEWAY_PORT": "9443"})
        self.assertEqual((config.bind_address, config.port), ("10.12.0.2", 9443))
        for path in ("/infer", "/health"):
            self.assertTrue(allowed_source("10.12.0.3", path, config))
            self.assertFalse(allowed_source("192.168.0.129", path, config))
            self.assertFalse(allowed_source("10.12.0.4", path, config))
        self.assertTrue(allowed_source("127.0.0.1", "/health", config))
        self.assertFalse(allowed_source("127.0.0.1", "/infer", config))
        self.assertFalse(allowed_source("10.12.0.3", "/infer?token=x", config))

    def test_invalid_bind_and_client_addresses_rejected(self):
        invalid = ("0.0.0.0", "224.0.0.1", "239.255.255.250", "255.255.255.255",
                   "::", "::1", "::ffff:10.12.0.3", "example.invalid", "10.12.0.3/24",
                   "10.12.0.3,10.12.0.4", "10.12.0.300", "010.12.0.3", " 10.12.0.3", "")
        for field in ("BIND", "CLIENT"):
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(
                        GatewayError, "invalid_gateway_" + field.lower() + "_address"):
                    NetworkConfig.load({"CB_GATEWAY_" + field + "_ADDRESS": value})

    def test_single_host_loopback_is_an_explicit_valid_ipv4_configuration(self):
        config = NetworkConfig.load({"CB_GATEWAY_BIND_ADDRESS": "127.0.0.1",
                                     "CB_GATEWAY_CLIENT_ADDRESS": "127.0.0.1"})
        self.assertTrue(allowed_source("127.0.0.1", "/infer", config))
        self.assertFalse(allowed_source("127.0.0.2", "/infer", config))

    def test_port_bounds_and_invalid_values(self):
        for value in ("1", "65535"):
            self.assertEqual(NetworkConfig.load({"CB_GATEWAY_PORT": value}).port, int(value))
        for value in ("0", "65536", "-1", "+8444", "8444.0", " 8444", "", "８４４４", "True", "9" * 5000):
            with self.subTest(value=value), self.assertRaisesRegex(GatewayError, "invalid_gateway_port"):
                NetworkConfig.load({"CB_GATEWAY_PORT": value})
        with self.assertRaisesRegex(GatewayError, "invalid_gateway_port"):
            NetworkConfig(port=True)

    def test_environment_changes_do_not_change_frozen_startup_policy(self):
        with patch.dict(os.environ, {"CB_GATEWAY_CLIENT_ADDRESS": "10.12.0.3"}):
            config = NetworkConfig.load()
            with patch.dict(os.environ, {"CB_GATEWAY_CLIENT_ADDRESS": "10.12.0.4"}):
                self.assertTrue(allowed_source("10.12.0.3", "/infer", config))
                self.assertFalse(allowed_source("10.12.0.4", "/infer", config))
        with self.assertRaises(FrozenInstanceError):
            config.client_address = "10.12.0.4"

    def test_import_does_not_validate_or_apply_environment(self):
        environment = {**os.environ, "CB_GATEWAY_BIND_ADDRESS": "0.0.0.0",
                       "CB_GATEWAY_CLIENT_ADDRESS": "untrusted.invalid", "CB_GATEWAY_PORT": "invalid"}
        result = subprocess.run([sys.executable, "-c", "import gateway"],
            cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)


class RequestSourceTests(unittest.TestCase):
    """Exercise request refusal without binding a socket or starting a service."""
    def handler(self, address, path, forwarded):
        handler = object.__new__(Handler)
        handler.client_address = (address, 49152)
        handler.path = path
        handler.headers = Message()
        handler.headers["X-Forwarded-For"] = forwarded
        handler.headers["Forwarded"] = "for=" + forwarded
        handler.server = SimpleNamespace(network_config=NetworkConfig(client_address="10.12.0.3"),
                                          gateway=Mock())
        handler.respond = Mock()
        return handler

    def test_forwarded_headers_cannot_authorize_inference_from_another_source(self):
        handler = self.handler("10.12.0.4", "/infer", "10.12.0.3")
        handler.do_POST()
        handler.respond.assert_called_once_with(403, {"error": "source_or_path_not_allowed"})
        handler.server.gateway.infer.assert_not_called()

    def test_forwarded_headers_cannot_authorize_health_from_another_source(self):
        handler = self.handler("10.12.0.4", "/health", "10.12.0.3")
        handler.do_GET()
        handler.respond.assert_called_once_with(403, {"error": "source_or_path_not_allowed"})
        handler.server.gateway.health.assert_not_called()

    def test_health_uses_server_startup_policy_even_if_environment_changes(self):
        handler = self.handler("10.12.0.3", "/health", "10.12.0.4")
        handler.server.gateway.health.return_value = {"status": "ready"}
        with patch.dict(os.environ, {"CB_GATEWAY_CLIENT_ADDRESS": "10.12.0.4"}):
            handler.do_GET()
        handler.respond.assert_called_once_with(200, {"status": "ready"})


if __name__ == "__main__":
    unittest.main()
