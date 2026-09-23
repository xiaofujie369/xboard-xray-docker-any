"""Offline real-core test: node-scoped DNS selection, fallback, and block policies."""
import http.server
import json
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import config
from runtime_stats import receive, wait_port


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        position, labels = 12, []
        while packet[position]:
            length = packet[position]
            labels.append(packet[position + 1:position + 1 + length].decode())
            position += length + 1
        position += 1
        kind = struct.unpack('!H', packet[position:position + 2])[0]
        question = packet[12:position + 4]
        self.server.queries.append('.'.join(labels))
        answer = (b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + socket.inet_aton('127.0.0.1')) if kind == 1 else b''
        reply = packet[:2] + struct.pack('!HHHHH', 0x8180, 1, int(bool(answer)), 0, 0) + question + answer
        sock.sendto(reply, self.client_address)


class HTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'panel-route-success')
    def log_message(self, *args):
        pass


def request(port, domain, target_port):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(b'\x05\x01\x00')
        assert receive(sock, 2) == b'\x05\x00'
        encoded = domain.encode()
        sock.sendall(b'\x05\x01\x00\x03' + bytes([len(encoded)]) + encoded + struct.pack('!H', target_port))
        head = receive(sock, 4)
        assert head[:2] == b'\x05\x00'
        receive(sock, 6 if head[3] == 1 else 18)
        sock.sendall(b'GET / HTTP/1.0\r\nHost: ' + encoded + b'\r\n\r\n')
        body = b''
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                body += data
        except ConnectionResetError:
            pass
        return body


def main():
    servers, processes = [], []
    with tempfile.TemporaryDirectory() as folder:
        try:
            for _ in range(3):
                dns = socketserver.ThreadingUDPServer(('127.0.0.1', 0), DNSHandler)
                dns.queries = []
                servers.append(dns)
                threading.Thread(target=dns.serve_forever, daemon=True).start()
            backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HTTPHandler)
            servers.append(backend)
            threading.Thread(target=backend.serve_forever, daemon=True).start()
            dns_address = lambda n: f'udp://127.0.0.1:{servers[n].server_address[1]}'
            panels = {'1': {'server_port': 20981, 'listen_ip': '127.0.0.1', 'routes': [
                {'action': 'dns', 'match': ['*', '::/0'], 'action_value': dns_address(1)},
                {'action': 'dns', 'match': ['# 电商域名', '*.taobao.test', 'unsupported:invalid'], 'action_value': dns_address(0)},
                {'action': 'direct', 'match': ['full:allowed.blocked.test']},
                {'action': 'block', 'match': ['blocked.test']}]},
                '2': {'server_port': 20983, 'listen_ip': '127.0.0.1', 'routes': [
                    {'action': 'dns', 'match': ['*'], 'action_value': dns_address(2)}]}}
            secret = '12345678-1234-4234-8234-123456789abc'
            users = {'users': [{'id': 1, 'uuid': secret}]}
            generated = config.build([config.inbound(node, 'vless', panel, users) for node, panel in panels.items()],
                                     panels=panels)
            client = {'inbounds': [
                {'type': 'socks', 'tag': f'client-{n}', 'listen': '127.0.0.1', 'listen_port': port}
                for n, port in [(1, 20982), (2, 20984)]],
                'outbounds': [{'type': 'vless', 'tag': f'out-{n}', 'server': '127.0.0.1',
                               'server_port': port, 'uuid': secret} for n, port in [(1, 20981), (2, 20983)]],
                'route': {'rules': [{'inbound': [f'client-{n}'], 'outbound': f'out-{n}'} for n in (1, 2)]}}
            for name, data in [('server', generated), ('client', client)]:
                path = Path(folder) / (name + '.json')
                path.write_text(json.dumps(data))
                subprocess.run([sys.argv[1], 'check', '-c', str(path)], check=True)
                processes.append(subprocess.Popen([sys.argv[1], 'run', '-c', str(path)],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_port(20981)
            wait_port(20982)
            wait_port(20984)
            for port, domain, dns_index in [(20982, 'www.taobao.test', 0),
                                            (20982, 'overseas.test', 1),
                                            (20984, 'www.taobao.test', 2),
                                            (20982, 'allowed.blocked.test', 1)]:
                assert b'panel-route-success' in request(port, domain, backend.server_port), domain
                assert domain in servers[dns_index].queries, (domain, dns_index)
            assert 'www.taobao.test' not in servers[1].queries, 'default DNS overrode specific policy'
            assert b'panel-route-success' not in request(20982, 'blocked.test', backend.server_port)
            print('PASS real core: specific DNS, default DNS, node isolation, direct exception, block')
        finally:
            for process in processes:
                process.terminate()
                process.wait(timeout=10)
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
