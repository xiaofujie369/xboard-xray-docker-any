#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash update.sh'; exit 1; }
[[ -f /opt/singbox-sync/.env ]] || { echo '请先安装'; exit 1; }
if [[ ${1:-} != --worker ]]; then
  [[ ${1:-} == '' || ${1:-} == --core ]] || { echo '用法：bash update.sh [--core]'; exit 2; }
  systemd-run --unit=xbs-upgrade --collect /bin/bash "$PWD/update.sh" --worker "${1:-python}"
  echo '升级任务已提交，SSH 断开不影响执行。查看结果：journalctl -u xbs-upgrade -f'
  exit 0
fi
exec 9>/opt/singbox-sync/operation.lock
flock -n 9 || { echo '另一个管理/升级任务正在执行'; exit 1; }
if [[ ${2:-} == --core ]]; then
  exec /bin/bash "$PWD/upgrade-core.sh"
fi
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
