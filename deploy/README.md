# 部署與 Gateway 後端 TLS

一般內網安裝請先參考 [Ubuntu 內網部署](lan/README.md)。本文件記錄 `herder-home.vanisoul.com` 的 Gateway 配套與 Caddy 憑證簽發、CA 匯出流程。

## 連線路徑

```text
herder-home.vanisoul.com
  → Cloudflare A 記錄：158.179.186.45（proxied = true）
  → NetBird reverse proxy：HTTPS → 172.22.88.50（保留 Host）
  → traefik-gateway / HTTPRoute
  → herder-home Service / EndpointSlice
  → HTTPS → 172.22.88.35:8443（Caddy）
  → HTTP → 127.0.0.1:7317（Web UI）
```

外部 TLS 由 Cloudflare／NetBird／Gateway 的既有設定處理；Gateway 到 Caddy 的 TLS 則由 `BackendTLSPolicy` 驗證，兩者的信任鏈不同。瀏覽器透過公開網址使用時，不需要安裝 Caddy CA；直接連內網 IP 的裝置仍需信任它。Web UI 原本的 Token／裝置授權維持不變。

## 1. 讓 Caddy 簽發 DNS 名稱憑證

原本的 Caddy 憑證只有 `172.22.88.35` 的 IP SAN。`BackendTLSPolicy.validation.hostname` 依 Gateway API 規範不能使用 IP，因此新增 DNS 名稱憑證，並保留原本的 IP 入口。

在執行 Caddy 的主機，以原本執行 Herdr 的一般使用者操作：

```bash
cd "$HOME/repo/github/vanisoul/herdr-web-ui"
python3 deploy/lan/configure.py \
  --host 172.22.88.35 \
  --tls-hostname herder-home.vanisoul.com
```

若 PATH 上的 Bun 版本不足，請加上 `--bun-bin /完整路徑/bun`，指定實際服務使用的 Bun（至少 1.4.0）。本次主機使用：

```bash
python3 deploy/lan/configure.py \
  --host 172.22.88.35 \
  --tls-hostname herder-home.vanisoul.com \
  --bun-bin "$HOME/.local/share/mise/installs/bun/1.4.2/bin/bun"
```

重新產生設定會保留既有 Token 與配對資料。日後重跑也要傳入 `--tls-hostname`，否則會回到僅提供 IP 入口的設定。若原本有指定 `--herdr-socket` 或其他工具路徑，重跑時也要沿用。

產生的 Caddy site addresses 應為：

```caddyfile
https://172.22.88.35:8443, https://herder-home.vanisoul.com:8443 {
    bind 0.0.0.0
    tls internal
    reverse_proxy 127.0.0.1:7317 {
        header_up -Tailscale-User-Login
        header_up -Tailscale-Funnel-Request
    }
}
```

`tls internal` 使用原本的 Caddy Local Authority，不向公開 CA 申請憑證，也不需要先變更公網 DNS。不要刪除 Caddy 資料目錄或重新建立 CA。

先以暫存檔驗證，再安裝並重啟；`admin off` 的設定不能使用 `caddy reload`。重啟會短暫中斷 Web UI 連線，但不會重啟 Herdr 或 Web UI 程序。

```bash
sudo install -m 644 "$HOME/.config/herdr-web-ui/Caddyfile" /etc/caddy/Caddyfile.herder-home
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile.herder-home --adapter caddyfile
sudo install -m 644 /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-herder-home
sudo install -m 644 /etc/caddy/Caddyfile.herder-home /etc/caddy/Caddyfile
sudo systemctl restart caddy
systemctl is-active caddy
```

如需回復，將 `/etc/caddy/Caddyfile.before-herder-home` 安裝回 `/etc/caddy/Caddyfile`，再重啟 Caddy。

## 2. 匯出既有 CA 的公開憑證

官方 Caddy systemd 服務的根憑證位於：

```text
/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
```

只匯出公開的 `root.crt`；不要複製 `root.key`、`intermediate.key`、站台私密金鑰或 `lan.env` 到 Kubernetes 或 Git。

```bash
sudo install -o "$(id -un)" -g "$(id -gn)" -m 644 \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  "$HOME/.config/herdr-web-ui/root.crt"
openssl x509 -in "$HOME/.config/herdr-web-ui/root.crt" \
  -noout -subject -dates -fingerprint -sha256
```

透過可信管道傳遞此公開憑證並核對指紋。若 Caddy 根 CA 未變，葉憑證自動續期時不必更新 Kubernetes ConfigMap。

## 3. 驗證憑證、SNI 與原本 IP 入口

不要使用 `-k`；以下同時驗證信任鏈與 DNS 名稱，`--resolve` 讓測試不依賴公網 DNS：

```bash
curl --noproxy '*' \
  --cacert "$HOME/.config/herdr-web-ui/root.crt" \
  --resolve herder-home.vanisoul.com:8443:172.22.88.35 \
  -I https://herder-home.vanisoul.com:8443/

curl --noproxy '*' \
  --cacert "$HOME/.config/herdr-web-ui/root.crt" \
  -I https://172.22.88.35:8443/
```

兩者都應回傳 `200`。匿名讀取 `/api/session` 應回傳 `401`，確認入口沒有繞過 Web UI 授權。

## 4. 匯入 Kubernetes，套用 BackendTLSPolicy

以下從同一台主機操作；若在其他管理主機執行，先以可信管道傳送公開的 `root.crt`。

```bash
OPS_REPO="$HOME/repo/github/vanisoul/pev88-ops"
kubectl create configmap herder-home-ca \
  --namespace herder-home \
  --from-file=ca.crt="$HOME/.config/herdr-web-ui/root.crt" \
  --dry-run=client -o yaml \
  > "$OPS_REPO/06-k3s-cluster/herder-home/k8s/15-ca-configmap.yaml"

kubectl apply --dry-run=client --validate=true \
  -k "$OPS_REPO/06-k3s-cluster/herder-home/k8s"
kubectl apply -k "$OPS_REPO/06-k3s-cluster/herder-home/k8s"
```

`BackendTLSPolicy` 必須和 Service、CA ConfigMap 位於相同的 `herder-home` namespace，內容重點為：

```yaml
spec:
  targetRefs:
    - group: ""
      kind: Service
      name: herder-home
  validation:
    hostname: herder-home.vanisoul.com
    caCertificateRefs:
      - group: ""
        kind: ConfigMap
        name: herder-home-ca
```

ConfigMap 的資料鍵必須是 `ca.crt`。Service 的 `8443` 連接埠透過 EndpointSlice 對應 `172.22.88.35:8443`；Policy 的 hostname 提供後端 SNI 與憑證名稱驗證，不改寫 HTTP Host。

```bash
kubectl describe backendtlspolicy herder-home -n herder-home
kubectl describe httproute herder-home -n herder-home
curl --noproxy '*' \
  --resolve herder-home.vanisoul.com:443:172.22.88.50 \
  -I https://herder-home.vanisoul.com/
```

確認 Policy／Route 的 `Accepted`、相關 `ResolvedRefs` 條件成功，且 Gateway 回傳 `200`。這項測試不使用 `-k` 或跳過後端 TLS 驗證。

## 5. NetBird 與 Cloudflare

對應設定都在 `pev88-ops`：

| 設定 | 檔案 |
| --- | --- |
| Gateway、Service、EndpointSlice、CA 與 BackendTLSPolicy | `06-k3s-cluster/herder-home/k8s/` |
| NetBird 反向代理 | `07-netbird-deploy/reverse_proxy.tf` 的 `herder_home` |
| Cloudflare DNS | `08-cloudflare-dns/tokyo-dns.tf` 的 `herder_home` |

NetBird 沿用其他 Web 服務的 Gateway 目標 `172.22.88.50`，保留 Host，並限制來源國家為 `TW`；Web UI 自身負責登入授權。Cloudflare A 記錄使用 `158.179.186.45`、`proxied = true`、`ttl = 1`。

依各 Terraform 目錄既有的憑證載入流程執行 `terraform plan`，確認只有預期的新增資源後才執行 `terraform apply`。不要將其他既有差異一起套用。完成後再測試 `https://herder-home.vanisoul.com/`，以及登入後的 WebSocket 連線。

## 本次驗證紀錄

2026-10-17：沿用現有 Caddy 根 CA，新增 DNS 名稱憑證。使用匯出的 CA、未略過憑證驗證，確認 `herder-home.vanisoul.com:8443` 與原本 `172.22.88.35:8443` 都回傳 `200`，匿名 `/api/session` 回傳 `401`。這不代表 Kubernetes、NetBird 或 Cloudflare 已套用；仍需依上述步驟部署並驗證。

本次匯出根 CA 的 SHA-256 指紋：

```text
46:A2:53:9E:4D:9D:CA:9F:F7:32:6E:4F:35:4B:E4:01:28:3F:CF:E2:BE:6D:00:DB:6A:15:19:4B:53:AB:8B:07
```

參考：[Caddy tls](https://caddyserver.com/docs/caddyfile/directives/tls)、[Caddy admin](https://caddyserver.com/docs/caddyfile/options#admin)、[Gateway API BackendTLSPolicy](https://gateway-api.sigs.k8s.io/api-types/backendtlspolicy/)。
