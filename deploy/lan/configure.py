"""Generate configuration for the official Caddy service and one Web UI service."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import pwd
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
            if line.strip() and not line.lstrip().startswith("#"):
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


def render_service(repo, home, tools, user):
    q = unit_arg
    return f"""[Unit]
Description=Herdr Web UI
After=network.target

[Service]
Type=simple
User={user}
Environment={q('HOME=' + str(home))}
WorkingDirectory={unit_path(repo)}
EnvironmentFile={unit_path(home / '.config/herdr-web-ui/lan.env')}
ExecStartPre={q(tools['python'])} {q(repo / 'deploy/lan/guard.py')}
ExecStart={q(tools['bun'])} run server/index.ts
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


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


def render_caddyfile(template, host, tls_hostname=None):
    extra_site = ""
    if tls_hostname:
        name = tls_hostname.lower()
        if len(name) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", name):
            raise ValueError("--tls-hostname 請填寫有效的 DNS 名稱，不含連接埠或萬用字元")
        try:
            ipaddress.ip_address(name)
        except ValueError:
            pass
        else:
            raise ValueError("--tls-hostname 必須是 DNS 名稱，不可使用 IP")
        extra_site = f", https://{name}:8443"
    return template.replace("@HOST@", host).replace("@TLS_SITE@", extra_site)


def configure(args):
    if os.getuid() == 0:
        raise ValueError("請以執行 Herdr 的一般使用者執行此步驟，不要使用 sudo")
    host = str(ipaddress.IPv4Address(args.host))
    if ipaddress.ip_address(host).is_unspecified or ipaddress.ip_address(host).is_multicast:
        raise ValueError("--host 請填寫實際用來連線的主機 IPv4")
    home, repo = Path.home(), Path(__file__).resolve().parents[2]
    config = home / ".config/herdr-web-ui"
    tools = {name: executable(name, getattr(args, name + "_bin")) for name in ("bun", "node", "herdr")}
    tools["python"] = Path(sys.executable).resolve()
    for name, version in (("bun", (1, 4, 0)), ("node", (18, 0, 0)), ("herdr", (0, 9, 0))):
        require_version(tools[name], version)
    if not Path("/usr/bin/caddy").is_file():
        raise ValueError("請先依 README 安裝官方 Caddy APT 套件")
    socket = Path(args.herdr_socket).expanduser().resolve()
    if not socket.is_socket():
        raise ValueError("請先啟動 Herdr，或指定正確的 --herdr-socket")
    if not (repo / "dist/index.html").is_file():
        raise ValueError("請先執行 bun install --frozen-lockfile 與 bun run build")
    service = render_service(repo, home, tools, pwd.getpwuid(os.getuid()).pw_name)
    values = read_env(config / "lan.env")
    token = values.get("HERDR_WEB_TOKEN")
    if token is not None and len(token) < 32:
        raise ValueError("既有 Token 過短，請先更新；不會自動覆寫密碼")
    values.update({
        "HOST": "127.0.0.1", "PORT": "7317", "HERDR_SOCKET": str(socket),
        "HERDR_WEB_STATE_DIR": str(config), "HERDR_WEB_HERDR_BIN": str(tools["herdr"]),
        "HERDR_WEB_TOKEN": token if token is not None else secrets.token_hex(32),
        "HERDR_WEB_APP_NAME": values.get("HERDR_WEB_APP_NAME", "Herdr LAN"),
        "HERDR_WEB_AUTO_UPDATE": "0", "HERDR_WEB_TELEMETRY": "0", "DO_NOT_TRACK": "1",
        "PATH": os.pathsep.join(dict.fromkeys([str(tools[n].parent) for n in ("bun", "node", "herdr")] + [str(home / ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"])),
    })
    config.mkdir(parents=True, exist_ok=True)
    config.chmod(0o700)
    caddyfile = render_caddyfile((repo / "deploy/lan/Caddyfile").read_text(), host, args.tls_hostname)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "Caddyfile"
        path.write_text(caddyfile)
        subprocess.run(["/usr/bin/caddy", "adapt", "--config", str(path), "--adapter", "caddyfile"],
                       stdout=subprocess.DEVNULL, check=True)
    atomic_write(config / "lan.env", env_text(values))
    atomic_write(config / "Caddyfile", caddyfile, 0o644)
    atomic_write(config / "herdr-web-ui.service", service, 0o644)
    deployment = {"host": host, "https_port": 8443, "backend_port": 7317, "repo": str(repo),
                  "ca_file": str(config / "root.crt"), "tls_hostname": args.tls_hostname}
    atomic_write(config / "deployment.json", json.dumps(deployment, indent=2) + "\n")
    print(f"已產生部署設定；密碼與配對資料保留，首次部署則建立隨機密碼。\n入口：https://{host}:8443/")
    print("請依 README 將 Caddyfile 與 service 安裝到系統，並啟動兩個服務。")


def main():
    parser = argparse.ArgumentParser(description="產生 Caddy 與 Herdr Web UI 的系統服務設定")
    parser.add_argument("--host", required=True, help="用來連線的主機 IPv4")
    parser.add_argument("--tls-hostname", help="額外的 HTTPS DNS 名稱，由 Caddy 內部 CA 簽發憑證")
    parser.add_argument("--herdr-socket", default=os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock")))
    for name in ("bun", "node", "herdr"):
        parser.add_argument(f"--{name}-bin")
    try:
        configure(parser.parse_args())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"設定失敗：{error}\n")


if __name__ == "__main__":
    main()
