#!/usr/bin/env bash
# Invoked under systemd by update.sh --core, with operation.lock already held.
set -euo pipefail
umask 077
cd "$(dirname "$(readlink -f "$0")")"
[[ $EUID -eq 0 && ${INVOCATION_ID:-} != '' ]] || { echo '请运行 bash update.sh --core'; exit 1; }
python=/opt/singbox-sync/venv/bin/python
compose() { docker compose -f /opt/singbox/docker-compose.yml "$@"; }
[[ -f /opt/singbox/config/config.json ]] || { echo '请先完成 xbs init'; exit 1; }
# Build and test while the old core and accounting worker continue to run.
docker build -t xboard-singbox:1.12.25-xbs1 .
"$python" -m pip install -r requirements.txt
backup=$(mktemp -d /opt/singbox-sync/upgrade-XXXXXXXX)
mkdir "$backup/program"
cp /opt/singbox-sync/*.py "$backup/program/"
cp /usr/local/bin/xbs "$backup/xbs"
cp /etc/systemd/system/xboard-singbox.service "$backup/service"
cp /opt/singbox/config/config.json "$backup/config.json"
if [[ -f /opt/singbox-sync/limits.json ]]; then cp /opt/singbox-sync/limits.json "$backup/limits.json"; fi
compose config --format json > "$backup/compose.json"
old_image=$(docker inspect --format '{{.Image}}' xboard-singbox)
"$python" - "$backup/compose.json" "$old_image" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); data = json.loads(p.read_text())
data['services']['singbox']['image'] = sys.argv[2]
data['services']['singbox'].pop('build', None)
p.write_text(json.dumps(data))
PY
success=false
old_generation=$(docker inspect --format '{{.State.StartedAt}}' xboard-singbox)
finish() {
  result=$?
  trap - EXIT
  if [[ $success != true ]]; then
    echo '升级失败，恢复旧程序、核心镜像和配置；统计状态保留最新进度。'
    cp "$backup/program/"*.py /opt/singbox-sync/
    cp "$backup/xbs" /usr/local/bin/xbs
    cp "$backup/service" /etc/systemd/system/xboard-singbox.service
    cp "$backup/config.json" /opt/singbox/config/config.json
    cp "$backup/compose.json" /opt/singbox/docker-compose.yml
    if [[ -f "$backup/limits.json" ]]; then cp "$backup/limits.json" /opt/singbox-sync/limits.json
    else rm -f /opt/singbox-sync/limits.json; fi
    current_generation=$(docker inspect --format '{{.State.StartedAt}}' xboard-singbox 2>/dev/null || true)
    if [[ $current_generation != "$old_generation" ]]; then
      compose up -d --no-build --force-recreate || echo '旧核心恢复失败，请立即检查 Docker'
    fi
  fi
  systemctl daemon-reload
  systemctl enable --now xboard-singbox.service
  exit "$result"
}
trap finish EXIT
systemctl stop xboard-singbox.service
old_generation=$(docker inspect --format '{{.State.StartedAt}}' xboard-singbox)
# Snapshot config only after the worker stops, so the rollback matches its last update.
cp /opt/singbox/config/config.json "$backup/config.json"
install -m 600 sync/*.py requirements.txt /opt/singbox-sync/
install -m 755 sync/manage.sh /usr/local/bin/xbs
install -m 644 systemd/xboard-singbox.service /etc/systemd/system/
install -m 600 docker-compose.yml Dockerfile .dockerignore /opt/singbox/
install -d -m 700 /opt/singbox/core
install -m 600 core/* /opt/singbox/core/
if [[ ! -f /opt/singbox-sync/limits.json ]]; then install -m 600 limits.example.json /opt/singbox-sync/limits.json; fi
# Agent validates first, saves accounting before recreation, and retains failed reports.
"$python" /opt/singbox-sync/agent.py --upgrade-core
success=true
echo "核心与同步程序升级成功。备份：$backup"
