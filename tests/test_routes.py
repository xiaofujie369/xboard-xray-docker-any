import copy
import json
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import agent
import config
import panel_routes as routes

USERS = {'users': [{'id': 1, 'uuid': '12345678-1234-4234-8234-123456789abc'}]}


def build(groups, local=None):
    server = {'server_port': 20001, 'routes': groups}
    return config.build([config.inbound('1', 'vless', server, USERS)], local, {'1': server})


class RouteTests(unittest.TestCase):
    def test_screenshot_dns(self):
        with warnings.catch_warnings(record=True) as caught:
            result = build([{'action': 'dns', 'match': ['# 常用电商类', 'taobao.com', '*.taobao.com',
                'tmall.com', '*.tmall.com', 'jd.com', '*.jd.com'],
                'action_value': '223.5.5.5,119.29.29.29,2400:3200::1,2402:4e00::'}])
        self.assertEqual(len(caught), 1)
        self.assertEqual(result['dns']['rules'][0]['domain_suffix'], ['taobao.com', 'tmall.com', 'jd.com'])
        self.assertEqual(result['dns']['servers'][3]['server'], '2400:3200::1')
        self.assertEqual(result['dns']['rules'][0]['inbound'], ['node-1'])
        self.assertEqual(result['route']['rules'][0], {'inbound': ['node-1'], 'action': 'resolve'})

    def test_default_dns_after_specific_even_if_first(self):
        result = build([
            {'action': 'dns', 'match': ['*', '0.0.0.0/0', '::/0'], 'action_value': '1.1.1.1'},
            {'action': 'dns', 'match': ['*.taobao.com'], 'action_value': '223.5.5.5'}])
        rules = result['dns']['rules']
        self.assertIn('domain_suffix', rules[0])
        self.assertEqual(rules[-1], {'inbound': ['node-1'], 'action': 'route', 'server': 'xbs-1-dns-0-0'})

    def test_node_isolation(self):
        group = [{'action': 'dns', 'match': ['*'], 'action_value': '1.1.1.1'}]
        a = {'server_port': 20001, 'routes': group}
        b = {'server_port': 20002, 'routes': group}
        result = config.build([config.inbound('1', 'vless', a, USERS), config.inbound('2', 'vless', b, USERS)],
                              panels={'1': a, '2': b})
        self.assertEqual([r['inbound'] for r in result['dns']['rules']], [['node-1'], ['node-2']])
        self.assertNotEqual(result['dns']['rules'][0]['server'], result['dns']['rules'][1]['server'])
        self.assertTrue(result['dns']['independent_cache'])

    def test_block_ip_and_domain_are_or(self):
        result = build([{'action': 'block', 'match': ['full:bad.test', '10.0.0.0/8', 'geoip:private']}])
        rules = result['route']['rules']
        self.assertEqual(len(rules), 3)
        self.assertEqual({r['action'] for r in rules}, {'reject'})
        self.assertTrue(all(r['inbound'] == ['node-1'] for r in rules))

    def test_terminal_order(self):
        result = build([{'action': 'direct', 'match': ['full:allowed.test']},
                        {'action': 'block', 'match': ['*.test']}])
        self.assertEqual([r['action'] for r in result['route']['rules']], ['route', 'reject'])

    def test_empty_not_catchall(self):
        result = build([{'action': 'block', 'match': ['# comment', '// comment', '; comment', '']}])
        self.assertNotIn('rules', result['route'])

    def test_various_matchers_and_json(self):
        match, wildcard = routes.matchers(json.dumps(['full:a.test', 'keyword:shop', 'regexp:^a', '*.foo.test',
            'ad*.test', '2001:db8::/32']))
        self.assertFalse(wildcard)
        self.assertEqual(len(match), 5)  # both regex patterns share a field
        self.assertIn({'domain_suffix': ['foo.test']}, match)

    def test_proxy_reference_local_outbound(self):
        result = build([{'action': 'proxy', 'match': ['*.test'], 'action_value': 'relay'}],
                       {'outbounds': [{'type': 'direct', 'tag': 'relay'}]})
        self.assertEqual(result['route']['rules'][0]['outbound'], 'relay')
        with self.assertWarns(UserWarning):
            result = build([{'action': 'proxy', 'match': ['*'], 'action_value': 'missing'}])
        self.assertNotIn('rules', result['route'])

    def test_dns_address_forms(self):
        for address, kind in [('223.5.5.5', 'udp'), ('2400:3200::1', 'udp'),
                              ('udp://[::1]:5353', 'udp'), ('tcp://1.1.1.1:5353', 'tcp'),
                              ('tls://dns.example.com', 'tls'), ('https://dns.example.com/dns-query', 'https')]:
            with self.subTest(address=address):
                self.assertEqual(routes.dns_server(address, 'test')['type'], kind)
        self.assertEqual(routes.dns_server('udp://[::1]:5353', 'test')['server_port'], 5353)
        self.assertEqual(routes.dns_addresses('DNS: 1.1.1.1，8.8.8.8; 2400:3200::1'),
                         ['1.1.1.1', '8.8.8.8', '2400:3200::1'])

    def test_invalid_does_not_echo_secret(self):
        with warnings.catch_warnings(record=True) as caught:
            result = build([{'action': 'dns', 'match': ['*'], 'action_value': 'https://secret:token@dns.test'}])
        self.assertNotIn('token', str(caught[0].message))
        self.assertIn('节点 1 第 1 条路由', str(caught[0].message))
        self.assertNotIn('dns', result)

    def test_unsupported_match_skipped(self):
        for match in ('geosite:cn', 'geoip:cn', 'unsupported:foo'):
            with self.subTest(match=match), self.assertWarns(UserWarning):
                result = build([{'action': 'block', 'match': [match]}])
                self.assertNotIn('rules', result['route'])

    def test_invalid_item_preserves_valid_siblings(self):
        with self.assertWarns(UserWarning):
            result = build([{'action': 'dns', 'match': ['good.test', 'invalid:prefix', '*.valid.test'],
                             'action_value': '1.1.1.1'}])
        self.assertEqual(result['dns']['rules'][0]['domain_suffix'], ['good.test', 'valid.test'])

    def test_invalid_rule_does_not_drop_other_rules(self):
        with self.assertWarns(UserWarning):
            result = build([{'action': 'unknown', 'match': ['*']},
                            {'action': 'block', 'match': ['bad.test']}])
        self.assertEqual(result['route']['rules'][0]['action'], 'reject')

    def test_bad_first_dns_uses_valid_second(self):
        with self.assertWarns(UserWarning):
            result = build([{'action': 'dns', 'match': ['*'],
                             'action_value': 'https://secret:token@bad.test,1.1.1.1'}])
        self.assertEqual(result['dns']['rules'][0]['server'], 'xbs-1-dns-0-1')
        self.assertEqual(len(result['dns']['servers']), 2)

    def test_malformed_list_keeps_inbound(self):
        with self.assertWarns(UserWarning):
            result = build('not json')
        self.assertEqual(len(result['inbounds']), 1)
        self.assertNotIn('rules', result['route'])

    def test_local_settings_preserved(self):
        local = {'dns': {'servers': [{'type': 'local', 'tag': 'mine'}], 'final': 'mine'},
                 'route': {'rules': [{'domain': ['local.test'], 'action': 'reject'}], 'final': 'direct'}}
        original = copy.deepcopy(local)
        result = build([{'action': 'dns', 'match': ['*'], 'action_value': '1.1.1.1'}], local)
        self.assertEqual(local, original)
        self.assertEqual(result['dns']['final'], 'mine')
        self.assertEqual(result['route']['rules'][-1]['domain'], ['local.test'])

    def test_empty_users_do_not_leave_routes(self):
        result = config.build([None], panels={'1': {'routes': [{'action': 'dns', 'match': ['*'],
                                                               'action_value': '1.1.1.1'}]}})
        self.assertNotIn('dns', result)

    def test_agent_carries_panel_routes_to_compiler(self):
        worker = object.__new__(agent.Agent)
        worker.env = {'NODES': '1:vless'}
        worker.panel = Mock()
        worker.panel.fetch.side_effect = [{'data': {'server_port': 20001,
            'routes': [{'action': 'block', 'match': ['*.bad.test']}]}}, USERS]
        with patch.object(agent, 'ROOT', Path(__file__).parent):
            self.assertEqual(worker.desired()['route']['rules'][0]['action'], 'reject')


if __name__ == '__main__':
    unittest.main()
