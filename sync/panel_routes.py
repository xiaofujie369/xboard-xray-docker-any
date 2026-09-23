"""Compile XBoard route groups, keeping every rule scoped to its node."""
import ipaddress
import json
import re
import warnings
from urllib.parse import urlsplit


class RouteError(RuntimeError):
    """Safe operational message: never include raw panel values or credentials."""


def entries(value):
    if value in (None, ''):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise RouteError('面板 routes 必须是数组或 JSON 数组')
    return value


def matchers(value, dns=False):
    if isinstance(value, str):
        value = json.loads(value) if value.lstrip().startswith('[') else value.splitlines()
    if not isinstance(value, list):
        raise RouteError('路由 match 必须是数组或多行文本')
    groups = {}
    wildcard = False
    for line in value:
        if not isinstance(line, str):
            raise RouteError('路由匹配项必须是文本')
        for item in line.splitlines():
            item = item.strip()
            if not item or item.startswith(('#', '//', ';')):
                continue
            if item in ('*', '*.*') or (dns and item in ('0.0.0.0/0', '::/0')):
                wildcard = True
                continue
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError:
                network = None
            if network is not None:
                if dns:
                    raise RouteError('DNS 路由请使用域名匹配；IP 网段无法转换为查询域名')
                field, argument = 'ip_cidr', str(network)
            elif item.startswith('geoip:private') and item == 'geoip:private' and not dns:
                field, argument = 'ip_is_private', True
            elif item.startswith(('geosite:', 'geoip:')):
                raise RouteError('sing-box 1.12 不支持旧 GeoIP/Geosite；请改用域名/IP 或本地 rule-set:标签')
            elif item.startswith(('regexp:', 'keyword:', 'full:', 'domain:', 'rule-set:')):
                prefix, argument = item.split(':', 1)
                field = {'regexp': 'domain_regex', 'keyword': 'domain_keyword', 'full': 'domain',
                         'domain': 'domain_suffix', 'rule-set': 'rule_set'}[prefix]
                if not argument:
                    raise RouteError('路由匹配项不能为空')
            elif '*' in item and not (item.startswith('*.') and item.count('*') == 1):
                field, argument = 'domain_regex', '^' + re.escape(item).replace(r'\*', '.*') + '$'
            else:
                field, argument = 'domain_suffix', item.removeprefix('*.').removeprefix('.')
            if field in ('domain', 'domain_suffix'):
                try:
                    argument = argument.rstrip('.').encode('idna').decode('ascii').lower()
                except UnicodeError:
                    raise RouteError('路由域名格式无效') from None
                if not argument or not re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)*', argument):
                    raise RouteError('路由域名格式无效或包含不支持的匹配前缀')
            if field == 'ip_is_private':
                groups[field] = True
            else:
                group = groups.setdefault(field, [])
                if argument not in group:
                    group.append(argument)
    # Separate match families are OR, not sing-box's domain AND IP combination.
    return [{field: argument} for field, argument in groups.items()], wildcard


def dns_addresses(value):
    if isinstance(value, str):
        value = re.sub(r'^\s*DNS\s*[:：]\s*', '', value, flags=re.I)
        value = re.split(r'[,，;；\s]+', value)
    if not isinstance(value, list):
        raise RouteError('DNS action_value 必须是地址文本或数组')
    result = []
    for address in value:
        if not isinstance(address, str):
            raise RouteError('DNS 地址必须是文本')
        address = address.strip()
        if address and address not in result:
            result.append(address)
    if not result:
        raise RouteError('DNS 路由缺少服务器地址')
    return result


def dns_server(address, tag):
    if address in ('local', 'localhost'):
        return {'type': 'local', 'tag': tag}
    try:
        ip = ipaddress.ip_address(address.strip('[]'))
    except ValueError:
        ip = None
    if ip is not None:
        return {'type': 'udp', 'tag': tag, 'server': str(ip)}
    parsed = urlsplit(address if '://' in address else 'udp://' + address)
    if parsed.scheme not in ('udp', 'tcp', 'tls', 'https', 'quic'):
        raise RouteError('DNS 地址仅支持 IP、local、udp/tcp/tls/https/quic URL')
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RouteError('DNS 地址格式无效')
    if parsed.path not in ('', '/') and parsed.scheme != 'https':
        raise RouteError('只有 HTTPS DNS 地址支持路径')
    result = {'type': parsed.scheme, 'tag': tag, 'server': parsed.hostname}
    try:
        port = parsed.port
    except ValueError:
        raise RouteError('DNS 端口格式无效；IPv6 带端口请使用 [地址]:端口') from None
    if port is not None:
        if not 1 <= port <= 65535:
            raise RouteError('DNS 端口超出有效范围')
        result['server_port'] = port
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        result['domain_resolver'] = 'xbs-system-dns'
    if parsed.scheme in ('tls', 'https', 'quic'):
        result['tls'] = {'enabled': True}
    if parsed.scheme == 'https':
        result['path'] = parsed.path or '/dns-query'
    return result


def apply(result, panels):
    """Apply after local settings are loaded so proxy actions may use local outbounds."""
    active = {item['tag'] for item in result['inbounds']}
    outbounds = {item['tag'] for item in result['outbounds'] if 'tag' in item}
    traffic_rules, resolvers, dns_rules, dns_defaults, resolving_nodes = [], [], [], [], []
    for node, server in panels.items():
        inbound = f'node-{node}'
        if inbound not in active:
            continue
        raw = server.get('routes', server.get('route_rules', server.get('routeRules')))
        for index, rule in enumerate(entries(raw)):
            try:
                if not isinstance(rule, dict):
                    raise RouteError('路由必须是对象')
                action = str(rule.get('action', '')).lower().strip()
                if action not in ('block', 'reject', 'direct', 'proxy', 'dns'):
                    raise RouteError('不支持的面板路由动作')
                matches, wildcard = matchers(rule.get('match', []), dns=action == 'dns')
                if not matches and not wildcard:
                    continue  # Empty/comment-only groups never become match-all rules.
                scope = {'inbound': [inbound]}
                if action == 'dns':
                    addresses = dns_addresses(rule.get('action_value'))
                    tags = []
                    for number, address in enumerate(addresses):
                        tag = f'xbs-{node}-dns-{index}-{number}'
                        resolvers.append(dns_server(address, tag))
                        tags.append(tag)
                    if len(tags) > 1:
                        warnings.warn(f'节点 {node} DNS 路由 {index + 1}: 多个 DNS 地址已导入，'
                                      '当前使用列表第一个；sing-box 1.12 不提供该列表的自动故障转移', stacklevel=2)
                    dns_rules.extend({**scope, **match, 'action': 'route', 'server': tags[0]} for match in matches)
                    if wildcard:
                        dns_defaults.append({**scope, 'action': 'route', 'server': tags[0]})
                    if inbound not in resolving_nodes:
                        resolving_nodes.append(inbound)
                else:
                    target = {'action': 'reject'} if action in ('block', 'reject') else {
                        'action': 'route', 'outbound': 'direct' if action == 'direct' else str(rule.get('action_value', '')).strip()}
                    if 'outbound' in target and target['outbound'] not in outbounds:
                        raise RouteError('proxy 路由引用的出站不存在，请在本地 routes.json 定义同名出站')
                    traffic_rules.extend({**scope, **match, **target} for match in ([{}] if wildcard else matches))
            except (ValueError, TypeError, RouteError) as error:
                # Preserve safe, specific compiler errors, never echo raw panel payloads.
                detail = str(error) if isinstance(error, RouteError) else '字段格式无效'
                raise RouteError(f'节点 {node} 第 {index + 1} 条路由: {detail}') from None
    if resolvers:
        dns = result.setdefault('dns', {})
        existing = dns.setdefault('servers', [])
        if any(str(item.get('tag', '')).startswith('xbs-') for item in existing):
            raise RouteError('本地 DNS 标签不能使用保留前缀 xbs-')
        existing.extend([{'type': 'local', 'tag': 'xbs-system-dns'}, *resolvers])
        dns['rules'] = dns_rules + dns_defaults + dns.get('rules', [])
        dns.setdefault('final', 'xbs-system-dns')
        dns['independent_cache'] = True
        result.setdefault('route', {}).setdefault('default_domain_resolver', dns['final'])
    if traffic_rules or resolving_nodes:
        route = result.setdefault('route', {})
        resolve = [{'inbound': [node], 'action': 'resolve'} for node in resolving_nodes]
        # Resolve first for DNS policies to affect actual dialing and IP match rules.
        # Preserve terminal policy order (e.g. a direct exception before a block).
        route['rules'] = resolve + traffic_rules + route.get('rules', [])
