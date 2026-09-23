#!/usr/bin/env python3
"""One serialized worker owns synchronization, accounting and core restarts."""
import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

import config
import stats
from status import collect_status

ROOT = Path('/opt/singbox-sync')
CORE = Path('/opt/singbox/config')
CONTAINER = 'xboard-singbox'


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        os.chmod(temporary, 0o600)
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_env(path):
    env = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip()
    url = urlsplit(env['PANEL_URL'])
    if url.scheme != 'https' or not url.netloc or url.username or url.query or url.fragment:
        raise ValueError('PANEL_URL 必须是 HTTPS 地址，不能带认证、查询参数或 fragment')
    if not env.get('PANEL_TOKEN'):
        raise ValueError('PANEL_TOKEN 不能为空')
    config.nodes(env['NODES'])
    return env


class Panel:
    def __init__(self, env):
        self.env = env
        self.session = requests.Session()

    def request(self, method, endpoint, **kwargs):
        # Do not log request URLs, panel responses or exceptions containing tokens.
        try:
            response = self.session.request(method, self.env['PANEL_URL'].rstrip('/') + endpoint,
                                            timeout=25, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise RuntimeError('XBoard 网络请求失败（敏感信息已省略）') from None
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f'XBoard HTTP {response.status_code}')
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError('XBoard 返回非 JSON 内容') from None
        if body is False or (isinstance(body, dict) and
                (body.get('success') is False or body.get('data') is False or
                 ('code' in body and str(body['code']) not in {'0', '200'}))):
            raise RuntimeError('XBoard 返回业务错误')
        return body

    def fetch(self, node, protocol, endpoint):
        return self.request('GET', '/api/v1/server/UniProxy/' + endpoint,
                            params={'node_id': node, 'node_type': protocol, 'token': self.env['PANEL_TOKEN']})

    def report(self, node, protocol, traffic):
        payload = {'token': self.env['PANEL_TOKEN'], 'node_id': int(node), 'node_type': protocol,
                   'status': collect_status(), 'metrics': {'kernel_status': True}}
        if traffic:
            payload['traffic'] = traffic
        self.request('POST', '/api/v2/server/report', json=payload)


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Core validation output can contain credentials; inspect manually when needed.
        raise RuntimeError(f'{args[0]} 命令失败，退出码 {result.returncode}')
    return result.stdout.strip()


def generation():
    state = json.loads(command('docker', 'inspect', '--format', '{{json .State}}', CONTAINER))
    if not state.get('Running'):
        raise RuntimeError('sing-box 容器未运行')
    return state['StartedAt']


def accumulate(state, current, core_generation):
    result = copy.deepcopy(state)
    previous = result.get('counters', {}) if result.get('generation') == core_generation else {}
    pending = result.setdefault('pending', {})
    for name, value in current.items():
        match = re.fullmatch(r'user>>>(\d+):(\d+)>>>traffic>>>(uplink|downlink)', name)
        if not match or value < 0:
            continue
        node, uid, direction = match.groups()
        old = previous.get(name, 0)
        delta = value - old if value >= old else value
        if delta:
            bucket = pending.setdefault(node, {}).setdefault(uid, [0, 0])
            bucket[0 if direction == 'uplink' else 1] += delta
    result['counters'] = current
    result['generation'] = core_generation
    return result


class Agent:
    def __init__(self, env):
        self.env = env
        self.panel = Panel(env)
        self.state_path = ROOT / 'state.json'
        # Corrupt state must stop accounting, not silently reset checkpoints.
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        panel_url = env['PANEL_URL'].rstrip('/')
        if self.state.get('panel_url', panel_url) != panel_url:
            raise RuntimeError('面板地址变更：请先处理原面板的待上报流量，再迁移统计状态')
        self.state['panel_url'] = panel_url

    def snapshot(self):
        before = generation()
        current = stats.query()
        if generation() != before:
            raise RuntimeError('采样时核心发生重启，稍后重试')
        self.state = accumulate(self.state, current, before)
        mapping = self.state.setdefault('node_types', {})
        mapping.update(dict(config.nodes(self.env['NODES'])))
        atomic(self.state_path, self.state)

    def send_pending(self):
        mapping = self.state.get('node_types', {})
        failures = []
        targets = set(mapping) | set(self.state.get('pending', {}))
        for node in sorted(targets):
            traffic = self.state.get('pending', {}).get(node, {})
            if node not in dict(config.nodes(self.env['NODES'])) and not traffic:
                continue
            try:
                self.panel.report(node, mapping[node], traffic)
            except Exception:
                failures.append(node)
                continue
            self.state.setdefault('pending', {}).pop(node, None)
            atomic(self.state_path, self.state)
        if failures:
            raise RuntimeError('上报失败，流量已保存在本地等待重试；节点: ' + ','.join(failures))

    def report(self):
        self.snapshot()
        self.send_pending()

    def desired(self):
        inbounds = []
        panels = {}
        for node, protocol in config.nodes(self.env['NODES']):
            server = self.panel.fetch(node, protocol, 'config')
            user_response = self.panel.fetch(node, protocol, 'user')
            inbounds.append(config.inbound(node, protocol, server, user_response))
            panels[node] = config.unwrap(server)
        routes = ROOT / 'routes.json'
        return config.build(inbounds, json.loads(routes.read_text()) if routes.exists() else None, panels)

    def sync(self, initial=False):
        if initial and (CORE / 'config.json').exists():
            raise RuntimeError('已有核心配置，请使用 xbs start 或 xbs sync')
        desired = self.desired()
        text = json.dumps(desired, ensure_ascii=False, sort_keys=True, indent=2)
        active = CORE / 'config.json'
        # Include certificate contents so renewals trigger a reload even if paths stay identical.
        digest = hashlib.sha256(text.encode())
        for item in desired['inbounds']:
            tls = item.get('tls', {})
            for key in ('certificate_path', 'key_path'):
                if key in tls:
                    relative = Path(tls[key]).relative_to('/etc/sing-box')
                    digest.update((CORE / relative).read_bytes())
        fingerprint = digest.hexdigest()
        if active.exists() and active.read_text(encoding='utf-8') == text and self.state.get('config_hash') == fingerprint:
            return False
        candidate = CORE / 'candidate.json'
        atomic(candidate, text)
        command('docker', 'compose', '-f', '/opt/singbox/docker-compose.yml', 'run', '--rm', '--no-deps',
                'singbox', 'check', '-c', '/etc/sing-box/candidate.json')
        backup = active.read_text(encoding='utf-8') if active.exists() else None
        if not initial:
            # Record a final snapshot. Failed network reports do not block user revocation:
            # unsent deltas remain durably queued and are retried after restart.
            self.snapshot()
            try:
                self.send_pending()
            except RuntimeError:
                print('[sync] 上报未完成，已持久化的流量将在后续重试', flush=True)
        if backup is not None:
            atomic(CORE / 'config.previous.json', backup)
        atomic(active, text)
        try:
            if initial:
                command('docker', 'compose', '-f', '/opt/singbox/docker-compose.yml', 'up', '-d')
            else:
                command('docker', 'restart', CONTAINER)
            self.wait_ready()
        except Exception:
            if backup is not None:
                atomic(active, backup)
                command('docker', 'restart', CONTAINER)
                self.wait_ready()
            elif initial:
                command('docker', 'compose', '-f', '/opt/singbox/docker-compose.yml', 'stop')
                active.unlink(missing_ok=True)
            raise RuntimeError('新配置启动失败，已尝试恢复上一份配置') from None
        self.state['config_hash'] = fingerprint
        atomic(self.state_path, self.state)
        print('[sync] 配置已校验并应用', flush=True)
        return True

    @staticmethod
    def wait_ready():
        for _ in range(15):
            try:
                generation()
                stats.query()
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError('sing-box 统计 API 未就绪')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', default=str(ROOT / '.env'))
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    # Serialize CLI actions with the daemon; prevents duplicate billing and overlapping restarts.
    import fcntl
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'agent.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agent = Agent(load_env(args.env))
        if args.init:
            agent.sync(initial=True)
            return
        while True:
            failed = False
            try:
                updated = load_env(args.env)
                if updated['PANEL_URL'].rstrip('/') != agent.state['panel_url']:
                    raise RuntimeError('不允许直接切换面板地址，请先处理统计状态')
                agent.env = updated
                agent.panel = Panel(updated)
                agent.report()
            except Exception as exc:
                failed = True
                print(f'[report] {type(exc).__name__}: {safe_error(exc)}', flush=True)
            try:
                agent.sync()
            except Exception as exc:
                failed = True
                print(f'[sync] {type(exc).__name__}: {safe_error(exc)}', flush=True)
            if args.once:
                if failed:
                    raise SystemExit(1)
                return
            time.sleep(max(10, int(agent.env.get('INTERVAL', '60'))))


def safe_error(exc):
    # Only our fixed operational errors are safe to expose. Validation may include panel data.
    return str(exc) if isinstance(exc, RuntimeError) else '操作失败，请检查配置、证书和节点数据'


if __name__ == '__main__':
    main()
