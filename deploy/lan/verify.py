"""Read-only live deployment checks; never send input to a Herdr pane."""
import http.client
import json
from pathlib import Path
import ssl
from configure import read_env

config = Path.home() / ".config/herdr-web-ui"
deployment = json.loads((config / "deployment.json").read_text())
host, port = deployment["host"], deployment["https_port"]
origin = f"https://{host}:{port}"
ca = Path(deployment["ca_file"])
env_file = config / "lan.env"
settings = read_env(env_file)
token = settings["HERDR_WEB_TOKEN"]
assert len(token) >= 32 and settings["HOST"] == "127.0.0.1"
assert env_file.stat().st_mode & 0o777 == 0o600
context = ssl.create_default_context(cafile=str(ca))


def request(path, method="GET", headers=None, body=None):
    conn = http.client.HTTPSConnection(host, port, context=context, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


for path in ("/api/session", "/api/agents", "/ws"):
    assert request(path)[0] == 401
assert request("/api/session", headers={"X-Forwarded-For": "127.0.0.1", "Tailscale-User-Login": "forged@example.invalid"})[0] == 401
health = json.loads(request("/api/health")[2])
assert health["ok"] and health["auth"]["required"] and health["herdr"]["terminal_attach"]
assert request("/api/session", headers={"Authorization": "Bearer invalid-smoke-token"})[0] == 401
status, headers, _ = request("/api/auth", "POST", {"Origin": origin, "Content-Type": "application/json"}, json.dumps({"token": token}))
assert status == 204
cookie_header = headers["Set-Cookie"]
assert all(flag in cookie_header for flag in ("HttpOnly", "Secure", "SameSite=Strict"))
cookie = cookie_header.split(";", 1)[0]
assert request("/api/session", headers={"Cookie": cookie})[0] == 200
assert request("/api/session", headers={"Authorization": f"Bearer {token}"})[0] == 200

# Only establish a socket: no subscriptions, pane attachments or terminal input.
conn = http.client.HTTPSConnection(host, port, context=context, timeout=10)
try:
    conn.request("GET", "/ws", headers={"Origin": origin, "Cookie": cookie,
        "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
    assert conn.getresponse().status == 101
finally:
    conn.close()
for path in ("/manifest.webmanifest", "/sw.js"):
    assert request(path)[0] == 200
assert request("/api/auth", "DELETE", {"Origin": origin, "Cookie": cookie})[0] == 204
print("通過：TLS 與 IP 驗證、匿名拒絕、偽造標頭拒絕、密碼登入、Secure Cookie、WebSocket、PWA 資源")
