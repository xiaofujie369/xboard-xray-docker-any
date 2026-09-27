#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo xbs'; exit 1; }
compose() { docker compose -f /opt/singbox/docker-compose.yml "$@"; }
case "${1:-help}" in
  init|sync)
    # The core may carry this SSH connection. systemd owns the entire operation.
    systemd-run --unit=xbs-operation --collect /usr/local/bin/xbs --worker "$1"
    echo '任务已提交，SSH 断开不影响执行。进度：journalctl -u xbs-operation -f'
    ;;
  --worker)
    [[ ${2:-} == init || ${2:-} == sync ]] || exit 2
    exec 9>/opt/singbox-sync/operation.lock
    flock -n 9 || { echo '另一个管理/升级任务正在执行'; exit 1; }
    resume=false
    if systemctl is-active --quiet xboard-singbox.service || [[ -f /opt/singbox/config/config.json ]]; then resume=true; fi
    trap 'if [[ $resume == true ]]; then systemctl enable --now xboard-singbox.service; fi' EXIT
    systemctl stop xboard-singbox.service
    if [[ $2 == init && ! -f /opt/singbox/config/config.json ]]; then
      /opt/singbox-sync/venv/bin/python /opt/singbox-sync/agent.py --init
    else
      /opt/singbox-sync/venv/bin/python /opt/singbox-sync/agent.py --once
    fi
    resume=true
    ;;
  start) compose up -d; systemctl enable --now xboard-singbox.service ;;
  stop) systemctl stop xboard-singbox.service; compose stop ;;
  status)
    systemctl status xboard-singbox.service --no-pager || true
    compose ps
    /opt/singbox-sync/venv/bin/python - <<'PY'
import json, time
from pathlib import Path
p = Path('/opt/singbox-sync/state.json')
if p.exists():
    state = json.loads(p.read_text())
    def age(value):
        return f'{max(0, int(time.time()) - value)} 秒前' if value else '未知（旧版本未记录）'
    print('最近采样:', age(state.get('last_snapshot')))
    for node in sorted(state.get('node_types', {})):
        traffic = state.get('pending', {}).get(node, {})
        print(f'节点 {node}: 最近成功上报 {age(state.get("last_report", {}).get(node))}; '
              f'待提交 {sum(sum(v) for v in traffic.values())} 字节')
PY
    ;;
  logs) journalctl -u xboard-singbox.service -n 100 --no-pager ;;
  core-logs) compose logs --tail 100 ;;
  check) compose run --rm --no-deps singbox check -c /etc/sing-box/config.json ;;
  edit) "${EDITOR:-nano}" /opt/singbox-sync/.env ;;
  limits) "${EDITOR:-nano}" /opt/singbox-sync/limits.json ;;
  *) echo 'xbs init | sync | start | stop | status | logs | core-logs | check | edit | limits' ;;
esac
