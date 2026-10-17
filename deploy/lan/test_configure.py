"""Isolated deployment-generator tests: no real services, state or Herdr session."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("configure", Path(__file__).with_name("configure.py"))
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class DeploymentTests(unittest.TestCase):
    def test_network_validation(self):
        self.assertEqual(configure.validate_network("192.168.10.20", ["192.168.10.0/24"], 8443, 7317), ["192.168.10.0/24"])
        for host, allow, port, backend in [
            ("0.0.0.0", ["192.168.10.0/24"], 8443, 7317),
            ("8.8.8.8", ["192.168.10.0/24"], 8443, 7317),
            ("192.168.10.20", ["0.0.0.0/0"], 8443, 7317),
            ("192.168.10.20", ["::/0"], 8443, 7317),
            ("192.168.10.20", [], 8443, 7317),
            ("192.168.10.20", ["192.168.10.0/24"], 443, 7317),
            ("192.168.10.20", ["192.168.10.0/24"], 7317, 7317),
        ]:
            with self.subTest(host=host, allow=allow, port=port), self.assertRaises(ValueError):
                configure.validate_network(host, allow, port, backend)

    def test_unit_path_escaping(self):
        self.assertEqual(configure.unit_arg('/tmp/100%/with space'), '"/tmp/100%%/with space"')
        for value in ('/tmp/$HOME', '/tmp/new\nline', '/tmp/\x00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure.unit_arg(value)

    def test_env_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "env"
            values = {"HERDR_WEB_TOKEN": "test-only-token-" * 3, "NAME": 'Herdr "LAN" 測試', "PATH": "/tmp/with space"}
            configure.atomic_write(path, configure.env_text(values))
            self.assertEqual(configure.read_env(path), values)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("# legacy\nHOST=127.0.0.1\nHERDR_WEB_APP_NAME=Herdr LAN\n")
            self.assertEqual(configure.read_env(path)["HERDR_WEB_APP_NAME"], "Herdr LAN")

    def test_units_follow_checkout_and_fail_closed(self):
        tools = {name: Path("/opt/tools") / name for name in ("bun", "node", "herdr", "python", "caddy")}
        units = configure.render_units(Path("/srv/new checkout"), Path("/home/test"), tools)
        self.assertIn('WorkingDirectory=/srv/new checkout', units["herdr-web-ui.service"])
        self.assertIn("guard.py", units["herdr-web-ui.service"])
        self.assertIn("gateway.env", units["herdr-web-ui-https.service"])
        self.assertNotIn("lan.env", units["herdr-web-ui-https.service"])
        self.assertIn("/srv/new checkout/deploy/lan/Caddyfile", units["herdr-web-ui-https.service"])

    def test_configure_preserves_credentials_and_validates_before_replacing(self):
        with tempfile.TemporaryDirectory() as temporary, socket.socket(socket.AF_UNIX) as server:
            home = Path(temporary) / "home"
            repo = Path(temporary) / "checkout"
            (repo / "dist").mkdir(parents=True)
            (repo / "dist/index.html").write_text("fixture")
            config = home / ".config/herdr-web-ui"
            config.mkdir(parents=True)
            (config / "devices.json").write_text("device-fixture")
            socket_path = Path(temporary) / "herdr.sock"
            server.bind(str(socket_path))
            args = SimpleNamespace(host="192.168.10.20", allow=["192.168.10.0/24"], https_port=8443,
                                   backend_port=7317, herdr_socket=str(socket_path),
                                   **{name + "_bin": sys.executable for name in ("bun", "node", "herdr", "caddy")})
            with patch.object(configure, "__file__", str(repo / "deploy/lan/configure.py")), \
                 patch.object(configure.Path, "home", return_value=home), \
                 patch.object(configure.subprocess, "check_output", return_value="99.0.0"), \
                 patch.object(configure.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
                configure.configure(args)
                token = configure.read_env(config / "lan.env")["HERDR_WEB_TOKEN"]
                self.assertEqual(len(token), 64)
                self.assertNotIn(token, output.getvalue())
                self.assertNotIn(token, (config / "gateway.env").read_text())
                configure.configure(args)
                self.assertEqual(configure.read_env(config / "lan.env")["HERDR_WEB_TOKEN"], token)
                self.assertEqual((config / "devices.json").read_text(), "device-fixture")
                self.assertEqual(json.loads((config / "deployment.json").read_text())["repo"], str(repo))
                before = (config / "lan.env").read_bytes()
                run.side_effect = subprocess.CalledProcessError(1, "caddy")
                with self.assertRaises(subprocess.CalledProcessError):
                    configure.configure(args)
                self.assertEqual((config / "lan.env").read_bytes(), before)

    def test_guard(self):
        guard = Path(__file__).with_name("guard.py")
        for host, token, accepted in [("127.0.0.1", "x" * 64, True), ("0.0.0.0", "x" * 64, False), ("127.0.0.1", "", False)]:
            result = subprocess.run([sys.executable, str(guard)], env={"HOST": host, "HERDR_WEB_TOKEN": token}, capture_output=True)
            self.assertEqual(result.returncode == 0, accepted)


if __name__ == "__main__":
    unittest.main()
