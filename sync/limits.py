"""Validate and expand locally managed per-authenticated-user session limits."""
import json

DEFAULTS = dict(max_tcp=128, max_udp=64, max_total=192, new_per_second=20, burst=40)


def policy(path):
    if not path.exists():
        return None  # Keep Python-only upgrades compatible with the original core.
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) - {'enabled', 'scope', 'defaults', 'users'}:
        raise ValueError('limits.json 字段无效')
    if not isinstance(data.get('enabled', True), bool):
        raise ValueError('limits.enabled 必须是布尔值')
    if not data.get('enabled', True):
        return None
    scope = data.get('scope', 'user')
    if scope not in {'user', 'node_user'}:
        raise ValueError('limits.scope 必须是 user 或 node_user')

    def values(raw, base):
        if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
            raise ValueError('limits 限额字段无效')
        merged = {**base, **raw}
        if any(type(v) is not int or not 0 <= v <= 1000000 for v in merged.values()):
            raise ValueError('limits 限额必须为 0 至 1000000 的整数')
        if bool(merged['new_per_second']) != bool(merged['burst']):
            raise ValueError('new_per_second 和 burst 必须同时为零或同时为正数')
        return merged

    defaults = values(data.get('defaults', {}), DEFAULTS)
    users = data.get('users', {})
    if not isinstance(users, dict):
        raise ValueError('limits.users 必须是对象')
    import re
    pattern = r'[0-9]+' if scope == 'user' else r'[0-9]+:[0-9]+'
    if any(not re.fullmatch(pattern, key) for key in users):
        raise ValueError('limits.users 键必须是用户 ID，node_user 模式使用 节点ID:用户ID')
    return {'scope': scope, 'defaults': defaults,
            'users': {key: values(value, defaults) for key, value in users.items()}}
