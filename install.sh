#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash install.sh'; exit 1; }
command -v apt-get >/dev/null || { echo '安装器支持 Debian / Ubuntu'; exit 1; }
command -v docker >/dev/null || { echo '请先安装 Docker Engine 和 Compose plugin'; exit 1; }
docker compose version >/dev/null
if [[ -f /opt/singbox-sync/agent.py ]]; then
  echo '已有安装，请使用 bash update.sh'; exit 1
fi
apt-get update
apt-get install -y python3 python3-venv ca-certificates
install -d -m 700 /opt/singbox/config/certs /opt/singbox-sync
install -m 600 docker-compose.yml Dockerfile /opt/singbox/
install -m 600 .dockerignore /opt/singbox/
install -m 600 sync/*.py requirements.txt /opt/singbox-sync/
install -m 755 sync/manage.sh /usr/local/bin/xbs
install -m 644 systemd/xboard-singbox.service /etc/systemd/system/
if [[ ! -f /opt/singbox-sync/.env ]]; then
  read -rp 'XBoard HTTPS 地址: ' panel
  read -rsp 'XBoard 通讯密钥: ' token; echo
  read -rp '节点列表，例如 101:anytls,102:hysteria2,103:tuic,104:vless,105:ss: ' nodes
  printf 'PANEL_URL=%s\nPANEL_TOKEN=%s\nNODES=%s\nINTERVAL=60\n' "$panel" "$token" "$nodes" > /opt/singbox-sync/.env
fi
python3 -m venv /opt/singbox-sync/venv
/opt/singbox-sync/venv/bin/pip install -r /opt/singbox-sync/requirements.txt
docker compose -f /opt/singbox/docker-compose.yml build
systemctl daemon-reload
echo '安装完成。TLS 节点先放好证书：'
echo '/opt/singbox/config/certs/<节点ID>/fullchain.pem'
echo '/opt/singbox/config/certs/<节点ID>/privkey.pem'
echo 'VLESS Reality 和 SS 不需要证书。准备好后运行：xbs init'
