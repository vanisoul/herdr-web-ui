"""Generate a Linux user-systemd deployment without starting or stopping services."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key] = json.loads(value) if value.startswith('"') else value
    return values


def env_text(values):
    return "".join(f"{key}={json.dumps(str(value), ensure_ascii=False)}\n" for key, value in values.items())


def unit_path(value):
    value = str(value)
    if any(c in value for c in "\n\r\x00$"):
        raise ValueError("部署路徑不可包含換行、NUL 或 $ 字元")
    return value.replace("%", "%%")


def unit_arg(value):
    return json.dumps(unit_path(value), ensure_ascii=False)


def validate_network(host, allow, https_port, backend_port):
    address = ipaddress.IPv4Address(host)
    private = [ipaddress.ip_network(c) for c in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not any(address in network for network in private):
        raise ValueError("--host 必須是 RFC 1918 內網 IPv4 位址")
    networks = [ipaddress.ip_network(c, strict=True) for c in allow]
    if not networks or any(n.version != 4 or not any(n.subnet_of(p) for p in private) for n in networks):
        raise ValueError("--allow 必須明確指定 RFC 1918 內網 IPv4 CIDR")
    if any(not 1024 <= port <= 65535 for port in (https_port, backend_port)) or https_port == backend_port:
        raise ValueError("兩個 port 必須不同，且介於 1024 到 65535")
    return [str(n) for n in networks]


def render_units(repo, home, tools):
    config = home / ".config/herdr-web-ui"
    data = home / ".local/share/herdr-web-ui"
    q = unit_arg
    common = "Restart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=true\n"
    app = f"""[Unit]
Description=Herdr Web UI (password protected loopback backend)
After=network.target

[Service]
Type=simple
WorkingDirectory={unit_path(repo)}
EnvironmentFile={unit_path(config / 'lan.env')}
ExecStartPre={q(tools['python'])} {q(repo / 'deploy/lan/guard.py')}
ExecStart={q(tools['bun'])} run server/index.ts
{common}
[Install]
WantedBy=default.target
"""
    gateway = f"""[Unit]
Description=Herdr Web UI LAN HTTPS gateway
After=network.target herdr-web-ui.service
Wants=herdr-web-ui.service

[Service]
Type=simple
EnvironmentFile={unit_path(config / 'gateway.env')}
ExecStart={q(tools['caddy'])} run --config {q(repo / 'deploy/lan/Caddyfile')} --adapter caddyfile
{common}PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={q(data / 'tls-data')} {q(config / 'caddy')}

[Install]
WantedBy=default.target
"""
    return {"herdr-web-ui.service": app, "herdr-web-ui-https.service": gateway}


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".lan-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def executable(name, override=None):
    candidate = override or shutil.which(name)
    if not candidate or not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        raise ValueError(f"找不到 {name}；請先安裝，或以 --{name}-bin 指定執行檔")
    return Path(candidate).resolve()


def require_version(tool, minimum):
    output = subprocess.check_output([str(tool), "--version"], text=True)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    if not match or tuple(map(int, match.groups())) < minimum:
        raise ValueError(f"{tool.name} 版本過舊；至少需要 {'.'.join(map(str, minimum))}")


def configure(args):
    home = Path.home()
    repo = Path(__file__).resolve().parents[2]
    config = home / ".config/herdr-web-ui"
    data = home / ".local/share/herdr-web-ui"
    allow = validate_network(args.host, args.allow, args.https_port, args.backend_port)
    fallback = data / "bin/caddy"
    tools = {name: executable(name, getattr(args, name + "_bin")) for name in ("bun", "node", "herdr")}
    tools["caddy"] = executable("caddy", args.caddy_bin or (str(fallback) if fallback.is_file() else None))
    tools["python"] = Path(sys.executable).resolve()
    for name, version in (("bun", (1, 4, 0)), ("node", (18, 0, 0)), ("herdr", (0, 9, 0))):
        require_version(tools[name], version)
    socket = Path(args.herdr_socket).expanduser().resolve()
    if not socket.is_socket():
        raise ValueError("Herdr socket 不存在；請先啟動 Herdr，或指定正確的 --herdr-socket")
    if not (repo / "dist/index.html").is_file():
        raise ValueError("尚未建置；請先執行 bun install --frozen-lockfile 與 bun run build")
    units = render_units(repo, home, tools)
    values = read_env(config / "lan.env")
    token = values.get("HERDR_WEB_TOKEN")
    if token is not None and len(token) < 32:
        raise ValueError("既有 Token 過短，請先自行更新，不會自動覆寫既有密碼")
    values.update({
        "HOST": "127.0.0.1", "PORT": str(args.backend_port), "HERDR_SOCKET": str(socket),
        "HERDR_WEB_STATE_DIR": str(config), "HERDR_WEB_HERDR_BIN": str(tools["herdr"]),
        "HERDR_WEB_TOKEN": token if token is not None else secrets.token_hex(32),
        "HERDR_WEB_APP_NAME": values.get("HERDR_WEB_APP_NAME", "Herdr LAN"),
        "HERDR_WEB_AUTO_UPDATE": "0", "HERDR_WEB_TELEMETRY": "0", "DO_NOT_TRACK": "1",
        "PATH": os.pathsep.join(dict.fromkeys([str(tools[n].parent) for n in ("bun", "node", "herdr")] + [str(home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"])),
    })
    gateway = {
        "HERDR_LAN_HOST": args.host, "HERDR_LAN_HTTPS_PORT": str(args.https_port),
        "HERDR_LAN_BACKEND_PORT": str(args.backend_port), "HERDR_LAN_ALLOW": "127.0.0.0/8 " + " ".join(allow),
        "XDG_DATA_HOME": str(data / "tls-data"), "XDG_CONFIG_HOME": str(config / "caddy"),
    }
    for directory in (config, config / "caddy", data / "tls-data"):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    # Only non-secret gateway values are passed to Caddy's validation process.
    subprocess.run([str(tools["caddy"]), "adapt", "--config", str(repo / "deploy/lan/Caddyfile"),
                    "--adapter", "caddyfile", "--validate"], env={**os.environ, **gateway},
                   stdout=subprocess.DEVNULL, check=True)
    atomic_write(config / "lan.env", env_text(values))
    atomic_write(config / "gateway.env", env_text(gateway))
    deployment = {"host": args.host, "https_port": args.https_port, "backend_port": args.backend_port,
                  "allowed_cidrs": allow, "repo": str(repo),
                  "ca_file": str(data / "tls-data/caddy/pki/authorities/local/root.crt"),
                  "leaf_file": str(data / f"tls-data/caddy/certificates/local/{args.host}/{args.host}.crt")}
    atomic_write(config / "deployment.json", json.dumps(deployment, indent=2) + "\n")
    for name, content in units.items():
        atomic_write(home / ".config/systemd/user" / name, content)
    print(f"已產生部署設定：{repo}\nHTTPS 入口：https://{args.host}:{args.https_port}/")
    print("既有密碼與狀態已保留；首次部署會建立隨機密碼。密碼未顯示。")
    print("尚未啟動服務；接著執行 systemctl --user daemon-reload，並依 README 啟動兩個服務。")


def main():
    parser = argparse.ArgumentParser(description="產生內網 HTTPS 部署設定，不會自動啟動或停止服務")
    parser.add_argument("--host", required=True, help="此主機的固定內網 IPv4")
    parser.add_argument("--allow", action="append", required=True, help="允許的內網 CIDR，可重複指定")
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--backend-port", type=int, default=7317)
    parser.add_argument("--herdr-socket", default=os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock")))
    for name in ("bun", "node", "herdr", "caddy"):
        parser.add_argument(f"--{name}-bin")
    args = parser.parse_args()
    try:
        configure(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"設定失敗：{error}\n")


if __name__ == "__main__":
    main()
