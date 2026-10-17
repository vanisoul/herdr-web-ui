# Ubuntu 內網部署

透過內網 Admin Gateway 存取 `herdr-home.admin.vanisoul.com` 的後端憑證與 CA 匯出流程，請參考 [Gateway 後端 TLS](../README.md)。

完成後有兩個系統服務，重開機會自動啟動：

| 服務 | 監聽 | 功能 |
| --- | --- | --- |
| `caddy.service` | `0.0.0.0:8443` | HTTPS，轉送至 Web UI |
| `herdr-web-ui.service` | `127.0.0.1:7317` | 原生 HTTP、登入與裝置授權 |

任何能連到主機 IP 的裝置都能開啟登入頁，使用功能仍須 Token 或已配對裝置授權。

以下以 Ubuntu 24.04 為例。請用執行 Herdr 的一般使用者操作，主機需已有執行中的 Herdr 0.9.0+，且具備 sudo 權限。Herdr 本身需由你原有的方式啟動。

## 1. 安裝 Bun 與官方 Caddy 套件

```bash
sudo apt update
sudo apt install -y git python3 nodejs unzip curl gnupg \
  debian-keyring debian-archive-keyring apt-transport-https

curl -fsSL https://bun.sh/install | bash -s "bun-v1.4.2"
export PATH="$HOME/.bun/bin:$PATH"

curl -1fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo chmod 644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
sudo systemctl stop caddy
```

## 2. Clone 並建置 Web UI

```bash
mkdir -p "$HOME/repo/github/vanisoul"
git clone --branch feat/portable-lan-deploy \
  git@github.com:vanisoul/herdr-web-ui.git \
  "$HOME/repo/github/vanisoul/herdr-web-ui"
cd "$HOME/repo/github/vanisoul/herdr-web-ui"

bun install --frozen-lockfile
bun run build
```

## 3. 指定主機 IP，安裝服務設定

將範例的 `192.168.10.20` 換成這台主機的固定內網 IPv4。此步驟會偵測目前專案與工具路徑，建立登入密碼；重跑時保留既有密碼與配對資料。

```bash
python3 deploy/lan/configure.py --host 192.168.10.20

sudo install -m 644 "$HOME/.config/herdr-web-ui/Caddyfile" /etc/caddy/Caddyfile
sudo install -m 644 "$HOME/.config/herdr-web-ui/herdr-web-ui.service" /etc/systemd/system/herdr-web-ui.service
sudo caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable caddy herdr-web-ui
sudo systemctl restart herdr-web-ui caddy
```

若 Herdr 使用非預設 socket，在 `configure.py` 命令加上 `--herdr-socket /完整路徑/herdr.sock`。主機如有防火牆，需允許裝置連到 TCP 8443；不要將它轉送到公網。

## 4. 取得 HTTPS 憑證並登入

Caddy 第一次啟動後會建立內部 CA。將公開憑證複製給目前使用者：

```bash
sudo install -o "$(id -un)" -g "$(id -gn)" -m 644 \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  "$HOME/.config/herdr-web-ui/root.crt"
openssl x509 -in "$HOME/.config/herdr-web-ui/root.crt" -noout -fingerprint -sha256
```

透過 SSH/SFTP 或可信管道，將 `~/.config/herdr-web-ui/root.crt` 傳到使用裝置，核對指紋後安裝並信任。iPhone 需在「一般 → 關於本機 → 憑證信任設定」啟用完整信任；Android 在系統設定的「安裝憑證」匯入 CA。只傳送這個公開憑證，不要傳送 CA 私密金鑰或密碼檔。

在主機終端查看登入 Token：

```bash
python3 -c 'import sys; sys.path.insert(0,"deploy/lan"); from configure import read_env; from pathlib import Path; print(read_env(Path.home()/".config/herdr-web-ui/lan.env")["HERDR_WEB_TOKEN"])'
```

開啟 **`https://主機IP:8443/`**，確認沒有憑證警告，展開 **Have an access token instead?**，輸入 Token 並按 **Unlock**。登入後也可在 **Settings → Phone & devices** 產生配對碼，授權其他裝置。

## 5. 確認完成

```bash
systemctl is-enabled caddy herdr-web-ui
systemctl is-active caddy herdr-web-ui
PYTHONDONTWRITEBYTECODE=1 python3 deploy/lan/verify.py
```

兩個服務都應顯示 `enabled`、`active`。以後重開機由 systemd 自動啟動，直接開啟 **`https://主機IP:8443/`** 即可；日常重啟使用 `sudo systemctl restart herdr-web-ui caddy`。

[Caddy 官方安裝文件](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)
