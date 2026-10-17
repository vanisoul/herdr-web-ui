"""Isolated tests; no real configuration, service or Herdr session is changed."""
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
TEMPLATE = Path(__file__).with_name("Caddyfile").read_text()


class DeploymentTests(unittest.TestCase):
    def test_unit_path_escaping(self):
        self.assertEqual(configure.unit_arg('/tmp/100%/with space'), '"/tmp/100%%/with space"')
        for value in ('/tmp/$HOME', '/tmp/new\nline', '/tmp/\x00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure.unit_arg(value)

    def test_env_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "env"
            values = {"HERDR_WEB_TOKEN": "test-only-token-" * 3, "NAME": 'Herdr "LAN" 測試'}
            configure.atomic_write(path, configure.env_text(values))
            self.assertEqual(configure.read_env(path), values)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_system_service_runs_as_owner_and_fails_closed(self):
        tools = {name: Path("/opt/tools") / name for name in ("bun", "python")}
        unit = configure.render_service(Path("/srv/new checkout"), Path("/home/test"), tools, "test")
        for expected in ("User=test", "WantedBy=multi-user.target", "WorkingDirectory=/srv/new checkout", "guard.py", "/home/test/.config/herdr-web-ui/lan.env"):
            self.assertIn(expected, unit)
        self.assertIn("bind 0.0.0.0", TEMPLATE)
        self.assertIn("reverse_proxy 127.0.0.1:7317", TEMPLATE)

    def test_configure_preserves_credentials_and_validates_before_replacing(self):
        with tempfile.TemporaryDirectory() as temporary, socket.socket(socket.AF_UNIX) as server:
            home, repo = Path(temporary) / "home", Path(temporary) / "checkout"
            (repo / "dist").mkdir(parents=True)
            (repo / "dist/index.html").write_text("fixture")
            (repo / "deploy/lan").mkdir(parents=True)
            (repo / "deploy/lan/Caddyfile").write_text(TEMPLATE)
            config = home / ".config/herdr-web-ui"
            config.mkdir(parents=True)
            (config / "devices.json").write_text("device-fixture")
            socket_path = Path(temporary) / "herdr.sock"
            server.bind(str(socket_path))
            args = SimpleNamespace(host="192.168.10.20", tls_hostname="herdr-home.admin.vanisoul.com", herdr_socket=str(socket_path),
                                   **{name + "_bin": sys.executable for name in ("bun", "node", "herdr")})
            with patch.object(configure, "__file__", str(repo / "deploy/lan/configure.py")), \
                 patch.object(configure.Path, "home", return_value=home), \
                 patch.object(configure.Path, "is_file", return_value=True), \
                 patch.object(configure.os, "getuid", return_value=1000), \
                 patch.object(configure.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="test")), \
                 patch.object(configure.subprocess, "check_output", return_value="99.0.0"), \
                 patch.object(configure.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
                configure.configure(args)
                token = configure.read_env(config / "lan.env")["HERDR_WEB_TOKEN"]
                self.assertEqual(len(token), 64)
                self.assertNotIn(token, output.getvalue())
                self.assertNotIn(token, (config / "Caddyfile").read_text())
                self.assertIn("https://192.168.10.20:8443, https://herdr-home.admin.vanisoul.com:8443", (config / "Caddyfile").read_text())
                self.assertEqual(json.loads((config / "deployment.json").read_text())["tls_hostname"], args.tls_hostname)
                configure.configure(args)
                self.assertEqual(configure.read_env(config / "lan.env")["HERDR_WEB_TOKEN"], token)
                self.assertEqual((config / "devices.json").read_text(), "device-fixture")
                self.assertEqual(json.loads((config / "deployment.json").read_text())["repo"], str(repo))
                before = (config / "lan.env").read_bytes()
                run.side_effect = subprocess.CalledProcessError(1, "caddy")
                with self.assertRaises(subprocess.CalledProcessError):
                    configure.configure(args)
                self.assertEqual((config / "lan.env").read_bytes(), before)

    def test_caddyfile_optional_tls_hostname(self):
        self.assertIn("https://192.168.10.20:8443 {", configure.render_caddyfile(TEMPLATE, "192.168.10.20"))
        rendered = configure.render_caddyfile(TEMPLATE, "192.168.10.20", "Herdr-Home.Admin.Vanisoul.com")
        self.assertIn("https://192.168.10.20:8443, https://herdr-home.admin.vanisoul.com:8443 {", rendered)
        self.assertNotIn("@TLS_SITE@", rendered)
        for name in ("172.22.88.35", "*.example.com", "host:8443", "https://host", "host\n}", "-host.example", "host..example", "a" * 64 + ".example", "a." * 127 + "a"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                configure.render_caddyfile(TEMPLATE, "192.168.10.20", name)

    def test_guard(self):
        for host, token, accepted in [("127.0.0.1", "x" * 64, True), ("0.0.0.0", "x" * 64, False), ("127.0.0.1", "", False)]:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("guard.py"))], env={"HOST": host, "HERDR_WEB_TOKEN": token}, capture_output=True)
            self.assertEqual(result.returncode == 0, accepted)


if __name__ == "__main__":
    unittest.main()
