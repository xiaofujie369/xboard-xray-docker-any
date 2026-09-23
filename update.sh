#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash update.sh'; exit 1; }
[[ -f /opt/singbox-sync/.env ]] || { echo '请先安装'; exit 1; }
restart_service=false
if systemctl is-active --quiet xboard-singbox.service; then
  restart_service=true
fi
systemctl stop xboard-singbox.service
trap 'if [[ $restart_service == true ]]; then systemctl start xboard-singbox.service; fi' EXIT
install -m 600 sync/*.py requirements.txt /opt/singbox-sync/
install -m 755 sync/manage.sh /usr/local/bin/xbs
install -m 644 systemd/xboard-singbox.service /etc/systemd/system/
/opt/singbox-sync/venv/bin/pip install -r /opt/singbox-sync/requirements.txt
systemctl daemon-reload
echo '同步程序已更新。此脚本不升级核心镜像；核心版本迁移需单独验证。'
