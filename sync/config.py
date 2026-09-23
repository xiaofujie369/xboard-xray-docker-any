"""Translate XBoard UniProxy responses to sing-box 1.12 configuration."""
import base64
import copy
import json
import uuid
import panel_routes


def obj(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def unwrap(value):
    return value.get('data', value) if isinstance(value, dict) else value


def nodes(value):
    result = []
    aliases = {'ss': 'shadowsocks', 'hy2': 'hysteria', 'hysteria2': 'hysteria'}
    for item in value.split(','):
        node, protocol = item.strip().split(':', 1)
        protocol = aliases.get(protocol.lower(), protocol.lower())
        if not node.isdecimal() or int(node) <= 0:
            raise ValueError('节点 ID 必须是正整数')
        if protocol not in {'vless', 'anytls', 'hysteria', 'tuic', 'shadowsocks'}:
            raise ValueError(f'不支持的协议: {protocol}')
        node = str(int(node))
        if node in [n for n, _ in result]:
            raise ValueError('节点 ID 不能重复')
        result.append((node, protocol))
    return result


def users(response):
    value = unwrap(response)
    if isinstance(value, dict):
        value = value.get('users')
    if not isinstance(value, list):
        raise ValueError('user API 必须返回用户数组，拒绝把错误响应当成空用户')
    result = []
    seen = set()
    credentials = set()
    for user in value:
        uid = str(int(user['id']))
        secret = str(user.get('uuid') or user.get('password') or '')
        if int(uid) <= 0 or not secret or uid in seen or secret in credentials:
            raise ValueError('无效或重复的用户 ID / 凭据')
        seen.add(uid)
        credentials.add(secret)
        result.append((uid, secret))
    return sorted(result, key=lambda u: int(u[0]))


def tls_config(server, node, protocol):
    settings = obj(server.get('tls_settings'))
    mode = server.get('tls', 0)
    if str(mode) == '2':
        if protocol != 'vless':
            raise ValueError('本版本仅 VLESS 支持 Reality')
        host = settings.get('server_name') or settings.get('serverName')
        key = settings.get('private_key') or settings.get('privateKey')
        short = settings.get('short_id', settings.get('shortId', settings.get('short_ids')))
        if not host or not key or short is None:
            raise ValueError('Reality 缺少 server_name / private_key / short_id')
        if isinstance(short, str):
            short = [s.strip() for s in short.split(',')]
        return {'enabled': True, 'server_name': host, 'reality': {
            'enabled': True, 'handshake': {'server': host, 'server_port': int(settings.get('server_port', 443))},
            'private_key': key, 'short_id': short}}
    if protocol == 'vless' and str(mode) != '1':
        return None
    result = {'enabled': True}
    host = settings.get('server_name') or server.get('server_name')
    if host:
        result['server_name'] = host
    # Node-specific files avoid choosing another node's certificate implicitly.
    result['certificate_path'] = f'/etc/sing-box/certs/{node}/fullchain.pem'
    result['key_path'] = f'/etc/sing-box/certs/{node}/privkey.pem'
    if settings.get('alpn'):
        result['alpn'] = settings['alpn']
    elif protocol == 'tuic':
        result['alpn'] = ['h3']
    return result


def inbound(node, protocol, response, user_response):
    server = unwrap(response)
    if not isinstance(server, dict):
        raise ValueError('config API 必须返回对象')
    port = int(server.get('server_port', server.get('port', 0)))
    if not 1 <= port <= 65535:
        raise ValueError('节点端口无效')
    if protocol == 'hysteria' and int(server.get('version', 2)) != 2:
        raise ValueError('仅支持 Hysteria2，请在面板设置 version=2')
    if protocol == 'tuic' and int(server.get('version', 5)) != 5:
        raise ValueError('仅支持 TUIC v5')
    if server.get('decryption') not in (None, '', 'none'):
        raise ValueError('sing-box 不支持 Xray VLESS encryption')
    if any(server.get(k) for k in ('custom_routes', 'custom_outbounds')):
        raise ValueError('Xray 自定义出站/路由请迁移至本地 sing-box routes.json；面板 routes 路由组可自动转换')
    parsed = users(user_response)
    # No inbound is safer than a zero-user Shadowsocks falling back to single-user mode.
    if not parsed:
        return None
    result = {'type': 'hysteria2' if protocol == 'hysteria' else protocol,
              'tag': f'node-{node}', 'listen': server.get('listen_ip') or '::',
              'listen_port': port, 'users': []}
    for uid, secret in parsed:
        user = {'name': f'{node}:{uid}'}
        if protocol in {'vless', 'tuic'}:
            user['uuid'] = str(uuid.UUID(secret))
        if protocol != 'vless':
            user['password'] = secret
        if protocol == 'vless' and server.get('flow') not in (None, '', 'none'):
            if server['flow'] != 'xtls-rprx-vision':
                raise ValueError('VLESS flow 只支持 xtls-rprx-vision')
            user['flow'] = server['flow']
        result['users'].append(user)
    if protocol == 'shadowsocks':
        if server.get('plugin'):
            raise ValueError('不支持服务端 Shadowsocks 插件')
        method = server.get('cipher') or server.get('method')
        allowed = {'aes-128-gcm', 'aes-192-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305',
                   '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'}
        if method not in allowed:
            raise ValueError(f'不支持的多用户 SS 加密: {method}')
        result['method'] = method
        if method.startswith('2022-'):
            size = 16 if '128' in method else 32
            key = server.get('server_key', '')
            if len(base64.b64decode(key, validate=True)) != size:
                raise ValueError('SS2022 server_key 长度错误')
            result['password'] = key
            # XBoard Helper::uuidToBase64: base64(substr(uuid, 0, key_length)).
            for user, (_, secret) in zip(result['users'], parsed):
                uuid.UUID(secret)
                user['password'] = base64.b64encode(secret[:size].encode()).decode()
        return result
    result['tls'] = tls_config(server, node, protocol)
    if result['tls'] is None:
        del result['tls']
    if protocol == 'vless':
        network = server.get('network') or 'tcp'
        settings = obj(server.get('networkSettings') or server.get('network_settings'))
        if network == 'tcp':
            if obj(settings.get('header')).get('type', 'none') != 'none':
                raise ValueError('不支持 TCP HTTP header 伪装')
        elif network in {'ws', 'grpc', 'httpupgrade'}:
            transport = {'type': network}
            if network == 'grpc':
                transport['service_name'] = settings.get('serviceName', settings.get('service_name', ''))
            else:
                transport['path'] = settings.get('path', '/')
                if network == 'httpupgrade' and settings.get('host'):
                    transport['host'] = settings['host']
            result['transport'] = transport
        else:
            raise ValueError(f'不支持 VLESS transport: {network}')
    elif protocol == 'hysteria':
        for key in ('up_mbps', 'down_mbps'):
            if int(server.get(key) or 0) > 0:
                result[key] = int(server[key])
        if server.get('obfs'):
            if server['obfs'] != 'salamander' or not server.get('obfs-password'):
                raise ValueError('Hysteria2 obfs 必须是 salamander 且包含 obfs-password')
            result['obfs'] = {'type': 'salamander', 'password': server['obfs-password']}
    elif protocol == 'tuic':
        for key in ('congestion_control', 'auth_timeout', 'zero_rtt_handshake', 'heartbeat'):
            if key in server and server[key] is not None:
                result[key] = server[key]
    elif protocol == 'anytls' and server.get('padding_scheme'):
        value = server['padding_scheme']
        result['padding_scheme'] = value.splitlines() if isinstance(value, str) else value
    return result


def build(inbounds, local=None, panels=None):
    active = [i for i in inbounds if i]
    occupied = set()
    for item in active:
        # Conservative: reject shared port even across distinct addresses/protocols.
        port = item['listen_port']
        if port == 10086 or port in occupied:
            raise ValueError(f'节点端口冲突: {port}')
        occupied.add(port)
    result = {'log': {'level': 'info', 'timestamp': True}, 'inbounds': active,
              'outbounds': [{'type': 'direct', 'tag': 'direct'}],
              'route': {'final': 'direct'},
              'experimental': {'v2ray_api': {'listen': '127.0.0.1:10086', 'stats': {
                  'enabled': True, 'users': [u['name'] for i in active for u in i['users']]}}}}
    if local:
        local = copy.deepcopy(local)
        if set(local) - {'outbounds', 'route', 'dns'}:
            raise ValueError('routes.json 只接受 outbounds / route / dns')
        for key in ('route', 'dns'):
            if key in local:
                result[key] = local[key]
        result['outbounds'] += local.get('outbounds', [])
    panel_routes.apply(result, panels or {})
    return result
