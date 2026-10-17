# Linux 內網 HTTPS 部署

這個目錄只提供部署工具，沒有修改上游 APP 本體。適用於另一台已有 Herdr 的 Linux 主機：clone fork、建置 APP、產生本機設定，再用 user systemd 啟動。

```text
內網瀏覽器 → https://主機IP:8443 → Caddy → http://127.0.0.1:7317 → Herdr
```

Caddy 負責 HTTPS 與來源網段限制；herdr-web-ui 使用原生 Token 或裝置配對驗證。只有 APP 的 HTTPS port 對指定內網開放，後端只監聽 loopback。此流程不建立公網通道，也不修改主機防火牆。

## 1. 準備工具與 Herdr

以要執行 Herdr 的一般使用者操作，不要以 root 執行本目錄的 Python 程式。

| 工具 | 最低版本／條件 |
| --- | --- |
| Linux、systemd user manager | `systemctl --user` 可以正常操作 |
| Herdr | 0.9.0+，且服務已執行中 |
| Bun | 1.4+；目前專案指定 1.4.2 |
| Node | 18+，供終端 sidecar 使用 |
| Python | 3.10+，只用標準函式庫 |
| Caddy | 建議目前穩定版 Caddy 2 |
| Git | 可存取這個 fork；SSH clone 需有 GitHub SSH key |

先安裝缺少的工具：[Bun](https://bun.sh/docs/installation)、[Node](https://nodejs.org/)、[Caddy](https://caddyserver.com/docs/install)。如果主機有 mise，可先執行 `mise install bun@1.4.2 node@24`，再以 `mise exec bun@1.4.2 node@24 -- bash` 進入使用這兩個版本的 shell。

確認工具與既有 Herdr：

```bash
bun --version
node --version
python3 --version
herdr --version
herdr status server
systemctl --user is-system-running
```

`systemctl` 若因其他服務而顯示 `degraded`，需另外確認原因，不代表一定不能使用本部署。若沒有 Herdr server，先在另一個終端啟動 `herdr`。部署工具不會停止、升級或自動啟動 Herdr。

### Caddy 不在 PATH 時

可以用 `--caddy-bin /絕對路徑/caddy` 指定。若已有 Docker，也可從官方 image 擷取執行檔，不需要常駐 container：

```bash
mkdir -p "$HOME/.local/share/herdr-web-ui/bin"
docker pull caddy:2
container=$(docker create caddy:2)
docker cp "$container:/usr/bin/caddy" "$HOME/.local/share/herdr-web-ui/bin/caddy"
docker rm "$container"
chmod 755 "$HOME/.local/share/herdr-web-ui/bin/caddy"
"$HOME/.local/share/herdr-web-ui/bin/caddy" version
```

擷取若失敗，請先處理錯誤並以 `docker rm "$container"` 清除暫存 container。部署工具會自動尋找上述執行檔位置。本例採用 native Caddy 執行檔，正式服務不依賴 Docker。

## 2. Clone fork 並建置

在部署分支尚未合併前，必須指定這個分支，不能只 clone `main`：

```bash
mkdir -p "$HOME/repo/github/vanisoul"
git clone --branch feat/portable-lan-deploy \
  git@github.com:vanisoul/herdr-web-ui.git \
  "$HOME/repo/github/vanisoul/herdr-web-ui"
cd "$HOME/repo/github/vanisoul/herdr-web-ui"

bun install --frozen-lockfile
bun run build
```

可 clone 到其他位置，部署工具會偵測目前 checkout 與工具的實際絕對路徑，不依賴帳號名稱、mise 安裝路徑或固定專案目錄。換位置、切換 Bun／Node／Herdr／Caddy 執行檔後，需重新執行設定步驟。

## 3. 指定這台主機的 IP 與允許網段

先用 `ip -br address` 找到內網 IPv4，最好在路由器設定 DHCP 保留或使用固定 IP。以下是**範例**，請換成新主機的 IP 與手機所在網段：

```bash
python3 deploy/lan/configure.py \
  --host 192.168.10.20 \
  --allow 192.168.10.0/24
```

跨多個內網網段時可重複使用 `--allow`：

```bash
python3 deploy/lan/configure.py \
  --host 192.168.10.20 \
  --allow 192.168.10.0/24 \
  --allow 192.168.20.0/24 \
  --https-port 8443 \
  --backend-port 7317
```

只接受 RFC 1918 內網 IPv4 與 CIDR；不接受所有來源或公開 IP。允許清單通常應包含主機自己的網段，讓主機本身也能透過 HTTPS 執行驗證。需另外在既有防火牆允許對應內網連到 HTTPS port；不要在路由器轉送到公網。

若不是預設 Herdr session，可加上：

```bash
--herdr-socket "$HOME/.config/herdr/sessions/SESSION/herdr.sock"
```

預設採用目前 shell 的 `HERDR_SOCKET_PATH`，沒有時使用 `~/.config/herdr/herdr.sock`。可用 `--bun-bin`、`--node-bin`、`--herdr-bin`、`--caddy-bin` 指定執行檔。

設定程式會：

- 確認工具版本、Herdr socket、APP 建置檔與 Caddy 設定有效。
- 在 `~/.config/herdr-web-ui/lan.env` 產生隨機 256-bit Token，權限 `600`；重新設定時保留既有 Token。
- 產生不含 Token 的 `gateway.env`，提供 Caddy 綁定 IP、port 與允許網段。
- 將目前 checkout 路徑與工具路徑寫入兩個 user systemd unit。
- 關閉自動更新與匿名使用統計，以固定 checkout 執行。
- 保留 APP 狀態、配對裝置與既有 CA；不自動啟動或停止任何服務。

空白或過短的既有 Token 會讓設定失敗，不會直接覆寫密碼。後端啟動前也會檢查 Token 與 loopback 綁定，避免未驗證的服務意外對外開放。

## 4. 啟動服務

```bash
systemctl --user daemon-reload
systemd-analyze --user verify \
  "$HOME/.config/systemd/user/herdr-web-ui.service" \
  "$HOME/.config/systemd/user/herdr-web-ui-https.service"
systemctl --user enable --now herdr-web-ui.service
systemctl --user enable --now herdr-web-ui-https.service
```

等待服務就緒後查看狀態：

```bash
systemctl --user status herdr-web-ui herdr-web-ui-https --no-pager
journalctl --user -u herdr-web-ui -u herdr-web-ui-https -n 30 --no-pager
```

若需要登出後繼續執行、並在開機時啟動 user service：

```bash
sudo loginctl enable-linger "$USER"
```

這是帳號層級設定。Herdr 本身仍須執行中，網頁才有工作階段可用；本流程不替 Herdr server 建立開機服務。主機網卡尚未取得指定 IP 時，Caddy 會啟動失敗並由 systemd 重試。

如果是重新部署或遷移，更新設定後要執行 `systemctl --user restart herdr-web-ui.service herdr-web-ui-https.service`；`enable --now` 不會重新啟動已在執行中的舊程序。

## 5. HTTPS、密碼與裝置授權

開啟 `https://你指定的IP:8443/`。Caddy 使用內部 CA 簽發 IP 憑證，首次需在使用裝置上安裝並信任這台主機的公開 CA 憑證。

公開 CA 檔案：

```text
~/.local/share/herdr-web-ui/tls-data/caddy/pki/authorities/local/root.crt
```

可從電腦透過 SSH/SFTP 取得，再由可信管道傳送至手機，例如：

```bash
scp USER@HOST:.local/share/herdr-web-ui/tls-data/caddy/pki/authorities/local/root.crt ./herdr-lan-root.crt
```

在主機查看憑證指紋，安裝時核對：

```bash
openssl x509 \
  -in "$HOME/.local/share/herdr-web-ui/tls-data/caddy/pki/authorities/local/root.crt" \
  -noout -fingerprint -sha256
```

- iPhone：安裝描述檔，再到「設定 → 一般 → 關於本機 → 憑證信任設定」啟用完整信任。
- Android：在系統設定搜尋「安裝憑證」，選擇 CA 憑證匯入；路徑依廠牌而異。
- 確認 HTTPS 沒有憑證警告後才輸入密碼，不要永久略過警告。
- 只傳送 `root.crt`，不要傳送 `root.key`、整個 CA 目錄或 `lan.env`。只在自己的裝置信任此 CA，不再使用時移除。

在自己的主機終端查看 Token（不要將結果貼到聊天或 Git）：

```bash
python3 -c 'import sys; sys.path.insert(0,"deploy/lan"); from configure import read_env; from pathlib import Path; print(read_env(Path.home()/".config/herdr-web-ui/lan.env")["HERDR_WEB_TOKEN"])'
```

登入頁展開 **Have an access token instead?**，在 **Access token** 輸入 Token，按 **Unlock**。也可以在已授權的裝置進入 **Settings → Phone & devices → Pair a device**，產生配對碼授權其他裝置。配對碼有效 10 分鐘、成功一次即失效；錯誤 5 次也會作廢。

Token 可作為初始登入與備援；日常手機使用自己的配對憑證，遺失時可以個別撤銷。修改 Token 不會撤銷配對裝置；需要時也要在裝置清單撤銷。具完整操作權限的裝置也能授權其他裝置，這不是只有唯一管理員可核准的多使用者權限系統。

手機信任憑證並登入後，iPhone Safari 選「分享 → 加入主畫面」，Android Chrome 選「⋮ → 安裝應用程式／加入主畫面」。瀏覽器與 PWA 的 Cookie 可能分開，需要時再登入或配對一次。

## 6. 驗證

下列單元測試只使用暫存目錄，不接觸既有 Herdr session 或真實設定：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/lan -p 'test_*.py' -v
bun test server/auth.test.ts server/access.test.ts
bun run typecheck
```

服務啟動就緒後，在主機以部署使用者執行唯讀檢查：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 deploy/lan/verify.py
```

它會檢查 TLS／IP、匿名拒絕、密碼登入、Cookie 安全旗標、WebSocket 與 PWA 資源；不附掛 pane、不向終端輸入指令。需要可用的 Node sidecar。

選用的行動版模擬檢查：

```bash
node node_modules/playwright-core/cli.js install chromium
node deploy/lan/verify-browser.mjs
```

這是 Chromium 的行動版模擬，不是實體手機或 Safari 實測。程式只對本機讀取的確切伺服器憑證使用 SPKI pin，不會對任意網站停用 TLS 驗證。

若有 Docker，可從**不在允許清單內**的 bridge 網段檢查來源限制，將下列 IP 換成主機 IP：

```bash
docker run --rm --network bridge \
  -v "$PWD/deploy/lan/verify-network.mjs:/verify.mjs:ro" \
  node:22-bookworm-slim node /verify.mjs 192.168.10.20 8443 7317
```

預期 HTTPS 回應 `403`，偽造 `X-Forwarded-For` 也無效，且不能直接連上後端。若部署允許清單涵蓋 Docker 網段，應改從另一個未授權網段測試。

## 7. 更新、遷移與停止

更新 checkout 後，先 `bun install --frozen-lockfile`、`bun run build` 並完成驗證，再重啟兩個服務。這個部署直接執行 `server/index.ts`，不使用上游自動更新 supervisor。

若變更 checkout 位置、IP、port、工具版本或 Herdr socket：

1. 在新位置完成建置。
2. 停止這兩個 Web UI 服務，不要停止 Herdr。
3. 以新位置的 `configure.py` 重新產生設定。
4. `systemctl --user daemon-reload`，再啟動兩個服務。
5. 驗證實際執行路徑、連線與授權都正常後，才刪除舊 checkout。

同一主機重新設定會保留密碼、配對狀態及 CA；不要刪除 `~/.config/herdr-web-ui` 或 `~/.local/share/herdr-web-ui/tls-data`。新主機則應建立自己的密碼與 CA，不要直接複製私密設定。IP 改變時手機的 APP 網址及登入狀態也會改變。

停止並取消自動啟動：

```bash
systemctl --user disable --now herdr-web-ui-https.service herdr-web-ui.service
```

此指令不停止 Herdr、不刪除密碼與配對狀態。linger 可能被其他 user service 使用，不應在未確認時直接關閉。

## 檔案配置

| 位置 | 用途 |
| --- | --- |
| `deploy/lan/Caddyfile` | 不含主機 IP 或密碼的 HTTPS 設定範本 |
| `deploy/lan/configure.py` | 依目前 checkout 與輸入參數產生部署設定 |
| `deploy/lan/guard.py` | 啟動前的安全檢查 |
| `~/.config/herdr-web-ui/lan.env` | 私密 Token、後端與工具設定，權限 600 |
| `~/.config/herdr-web-ui/gateway.env` | Caddy 的 IP、port、來源網段，不含 Token |
| `~/.config/herdr-web-ui/deployment.json` | 供驗證工具讀取的本機部署資訊 |
| `~/.config/systemd/user/herdr-web-ui*.service` | 產生的背景服務 unit |
| `~/.local/share/herdr-web-ui/tls-data/` | 本機 CA、憑證與私密金鑰 |

不要將產生的私密設定、CA 私密金鑰或 APP 狀態加入 Git。

參考：[上游安裝與安全說明](../../INSTALL.md)、[上游使用指南](../../docs/guide.md)、[Caddy 環境變數](https://caddyserver.com/docs/caddyfile/concepts#environment-variables)、[Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls)。
