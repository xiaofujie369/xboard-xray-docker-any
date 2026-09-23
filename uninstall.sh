#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash uninstall.sh'; exit 1; }
systemctl disable --now xboard-singbox.service || true
if [[ -f /opt/singbox/docker-compose.yml ]]; then
  docker compose -f /opt/singbox/docker-compose.yml down
fi
rm -f /etc/systemd/system/xboard-singbox.service /usr/local/bin/xbs
systemctl daemon-reload
echo '服务已卸载，配置、证书、统计状态保留于 /opt/singbox 和 /opt/singbox-sync'
