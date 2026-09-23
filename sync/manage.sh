#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo xbs'; exit 1; }
compose() { docker compose -f /opt/singbox/docker-compose.yml "$@"; }
case "${1:-help}" in
  init)
    systemctl stop xboard-singbox.service
    /opt/singbox-sync/venv/bin/python /opt/singbox-sync/agent.py --init
    systemctl enable --now xboard-singbox.service
    ;;
  sync)
    systemctl stop xboard-singbox.service
    trap 'systemctl start xboard-singbox.service' EXIT
    /opt/singbox-sync/venv/bin/python /opt/singbox-sync/agent.py --once
    ;;
  start) compose up -d; systemctl start xboard-singbox.service ;;
  stop) systemctl stop xboard-singbox.service; compose stop ;;
  status) systemctl status xboard-singbox.service --no-pager; compose ps ;;
  logs) journalctl -u xboard-singbox.service -n 100 --no-pager ;;
  core-logs) compose logs --tail 100 ;;
  check) compose run --rm --no-deps singbox check -c /etc/sing-box/config.json ;;
  edit) "${EDITOR:-nano}" /opt/singbox-sync/.env ;;
  *) echo 'xbs init | sync | start | stop | status | logs | core-logs | check | edit' ;;
esac
