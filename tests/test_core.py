import base64
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import agent
import config
import stats

UUID = '12345678-1234-4234-8234-123456789abc'
USERS = {'users': [{'id': 7, 'uuid': UUID}]}


class ConfigTests(unittest.TestCase):
    def test_aliases_and_duplicates(self):
        self.assertEqual(config.nodes('1:hy2,2:ss'), [('1', 'hysteria'), ('2', 'shadowsocks')])
        for value in ('1:vless,01:ss', '0:ss', '1:vmess'):
            with self.assertRaises(ValueError):
                config.nodes(value)

    def test_five_protocols(self):
        for protocol in ('anytls', 'hysteria', 'tuic', 'vless', 'shadowsocks'):
            with self.subTest(protocol=protocol):
                item = config.inbound('42', protocol, {'server_port': 443, 'cipher': 'aes-128-gcm'}, USERS)
                self.assertEqual(item['users'][0]['name'], '42:7')
                cfg = config.build([item])
                self.assertEqual(cfg['experimental']['v2ray_api']['stats']['users'], ['42:7'])
                if protocol in ('tuic', 'vless'):
                    self.assertEqual(item['users'][0]['uuid'], UUID)

    def test_reality(self):
        item = config.inbound('1', 'vless', {'server_port': 443, 'tls': 2, 'flow': 'xtls-rprx-vision',
            'tls_settings': {'server_name': 'example.com', 'private_key': 'test', 'short_id': 'abcd,1234'}}, USERS)
        self.assertEqual(item['tls']['reality']['short_id'], ['abcd', '1234'])
        self.assertNotIn('certificate_path', item['tls'])

    def test_empty_users_remove_inbound(self):
        for protocol in ('shadowsocks', 'anytls', 'hysteria', 'tuic', 'vless'):
            self.assertIsNone(config.inbound('1', protocol, {'server_port': 443}, {'users': []}))
        self.assertEqual(config.build([None])['inbounds'], [])

    def test_malformed_users_not_revocation(self):
        for response in ({'error': 'bad token'}, {'data': False}, None):
            with self.assertRaises(ValueError):
                config.users(response)

    def test_ss2022_matches_xboard(self):
        key = base64.b64encode(b'a' * 16).decode()
        item = config.inbound('1', 'shadowsocks', {'server_port': 443,
            'cipher': '2022-blake3-aes-128-gcm', 'server_key': key}, USERS)
        self.assertEqual(item['password'], key)
        self.assertEqual(base64.b64decode(item['users'][0]['password']), UUID[:16].encode())

    def test_port_and_route_fail_closed(self):
        item = config.inbound('1', 'vless', {'server_port': 443}, USERS)
        with self.assertRaises(ValueError):
            config.build([item, item])
        with self.assertRaises(ValueError):
            config.inbound('1', 'vless', {'server_port': 443, 'custom_routes': [{'action': 'block'}]}, USERS)

    def test_quic_specific_fields(self):
        item = config.inbound('1', 'hysteria', {'server_port': 443, 'version': 2,
            'obfs': 'salamander', 'obfs-password': 'secret', 'up_mbps': 100}, USERS)
        self.assertEqual(item['obfs'], {'type': 'salamander', 'password': 'secret'})
        with self.assertRaises(ValueError):
            config.inbound('1', 'tuic', {'server_port': 443, 'version': 4}, USERS)


class AccountingTests(unittest.TestCase):
    def test_changed_panel_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'ROOT', Path(folder)):
            agent.atomic(Path(folder) / 'state.json', {'panel_url': 'https://old.example.com'})
            with self.assertRaises(RuntimeError):
                agent.Agent({'PANEL_URL': 'https://new.example.com'})

    def test_delta_and_restart(self):
        name = 'user>>>1:7>>>traffic>>>uplink'
        state = agent.accumulate({}, {name: 100}, 'a')
        state = agent.accumulate(state, {name: 150}, 'a')
        self.assertEqual(state['pending']['1']['7'], [150, 0])
        state = agent.accumulate(state, {name: 80}, 'b')
        self.assertEqual(state['pending']['1']['7'], [230, 0])

    def test_node_isolation(self):
        state = agent.accumulate({}, {'user>>>1:7>>>traffic>>>uplink': 10,
            'user>>>2:7>>>traffic>>>downlink': 20}, 'a')
        self.assertEqual(state['pending'], {'1': {'7': [10, 0]}, '2': {'7': [0, 20]}})

    def test_pending_survives_failure_and_no_double_collection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'ROOT', Path(folder)):
            worker = agent.Agent({'PANEL_URL': 'https://example.com', 'PANEL_TOKEN': 'test', 'NODES': '1:ss'})
            worker.panel.report = Mock(side_effect=RuntimeError('offline'))
            with patch.object(agent, 'generation', return_value='a'), patch.object(agent.stats, 'query',
                return_value={'user>>>1:7>>>traffic>>>uplink': 50}):
                with self.assertRaises(RuntimeError):
                    worker.report()
                worker = agent.Agent(worker.env)
                worker.panel.report = Mock()
                worker.report()
                worker.panel.report.assert_called_once_with('1', 'shadowsocks', {'7': [50, 0]})
                self.assertEqual(worker.state['pending'], {})

    def test_partial_success_not_retried(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'ROOT', Path(folder)):
            worker = agent.Agent({'PANEL_URL': 'https://example.com', 'NODES': '1:vless,2:tuic'})
            worker.state = {'node_types': {'1': 'vless', '2': 'tuic'},
                            'pending': {'1': {'7': [1, 0]}, '2': {'7': [2, 0]}}}
            worker.panel.report = Mock(side_effect=[None, RuntimeError('offline')])
            with self.assertRaises(RuntimeError):
                worker.send_pending()
            self.assertNotIn('1', worker.state['pending'])
            self.assertIn('2', worker.state['pending'])

    def test_protobuf_roundtrip(self):
        response = stats.Response()
        response.stat.add(name='user>>>1:7>>>traffic>>>uplink', value=2**40)
        decoded = stats.Response.FromString(response.SerializeToString())
        self.assertEqual(decoded.stat[0].value, 2**40)
        self.assertFalse(stats.Query.FromString(stats.Query(reset=False).SerializeToString()).reset)

    def test_panel_rejects_application_error(self):
        panel = agent.Panel({'PANEL_URL': 'https://example.com'})
        response = Mock(status_code=200)
        response.json.return_value = {'data': False}
        panel.session.request = Mock(return_value=response)
        with self.assertRaises(RuntimeError):
            panel.request('POST', '/report')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('ROOT', 'CORE'):
            patcher = patch.object(agent, name, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.worker = agent.Agent({'PANEL_URL': 'https://example.com', 'NODES': '1:ss'})
        self.worker.desired = Mock(return_value=config.build([]))
        self.worker.snapshot = Mock()
        self.worker.send_pending = Mock()
        self.worker.wait_ready = Mock()
        agent.atomic(self.root / 'config.json', '{"old":true}')

    def test_validation_failure_preserves_active(self):
        with patch.object(agent, 'command', side_effect=RuntimeError('invalid')):
            with self.assertRaises(RuntimeError):
                self.worker.sync()
        self.assertEqual((self.root / 'config.json').read_text(), '{"old":true}')
        self.worker.snapshot.assert_not_called()

    def test_failed_snapshot_blocks_restart(self):
        self.worker.snapshot.side_effect = RuntimeError('stats unavailable')
        with patch.object(agent, 'command') as run:
            with self.assertRaises(RuntimeError):
                self.worker.sync()
            self.assertEqual(run.call_count, 1)  # validation only
        self.assertEqual((self.root / 'config.json').read_text(), '{"old":true}')

    def test_start_failure_restores_old_config(self):
        self.worker.wait_ready.side_effect = [RuntimeError('not ready'), None]
        with patch.object(agent, 'command'):
            with self.assertRaises(RuntimeError):
                self.worker.sync()
        self.assertEqual((self.root / 'config.json').read_text(), '{"old":true}')

    def test_no_change_does_not_restart(self):
        with patch.object(agent, 'command'):
            self.assertTrue(self.worker.sync())
        with patch.object(agent, 'command') as run:
            self.assertFalse(self.worker.sync())
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
