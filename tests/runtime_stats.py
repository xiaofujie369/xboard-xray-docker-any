"""Real transfers through all five protocols and their gRPC accounting API."""
import http.server
import base64
import json
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import config
import stats


def wait_port(port):
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f'port {port} did not start')


def receive(sock, count):
    data = b''
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise RuntimeError('unexpected EOF')
        data += part
    return data


def run_case(executable, protocol, fields):
    secret = '12345678-1234-4234-8234-123456789abc'
    inbound = config.inbound('1', protocol, {'server_port': 20981, 'listen_ip': '127.0.0.1', **fields},
                             {'users': [{'id': 7, 'uuid': secret}]})
    server = config.build([inbound])
    outbound = {'type': inbound['type'], 'server': '127.0.0.1', 'server_port': 20981}
    if protocol in ('vless', 'tuic'):
        outbound['uuid'] = secret
    if protocol != 'vless':
        outbound['password'] = secret
    if protocol == 'shadowsocks':
        outbound['method'] = inbound['method']
        outbound['password'] = inbound['users'][0]['password']
        if 'password' in inbound:
            outbound['password'] = inbound['password'] + ':' + outbound['password']
    if 'tls' in inbound:
        outbound['tls'] = {'enabled': True, 'server_name': 'example.com', 'insecure': True}
        if protocol == 'tuic':
            outbound['tls']['alpn'] = ['h3']
    client = {'inbounds': [{'type': 'socks', 'listen': '127.0.0.1', 'listen_port': 20982}],
              'outbounds': [outbound]}
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'xboard-singbox-test' * 1024)
        def log_message(self, *args):
            pass
    backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    processes = []
    try:
        with tempfile.TemporaryDirectory() as folder:
            if 'tls' in inbound:
                pair = subprocess.check_output([executable, 'generate', 'tls-keypair', 'example.com'], text=True)
                for field, pattern, name in (
                    ('certificate_path', r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', 'cert.pem'),
                    ('key_path', r'-----BEGIN (?:\w+ )?PRIVATE KEY-----.*?-----END (?:\w+ )?PRIVATE KEY-----', 'key.pem'),
                ):
                    path = Path(folder) / name
                    path.write_text(re.search(pattern, pair, re.S).group())
                    inbound['tls'][field] = str(path)
            for name, data in [('server', server), ('client', client)]:
                path = Path(folder) / (name + '.json')
                path.write_text(json.dumps(data))
                processes.append(subprocess.Popen([executable, 'run', '-c', str(path)],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_port(10086)
            wait_port(20982)
            with socket.create_connection(('127.0.0.1', 20982), timeout=5) as sock:
                sock.sendall(b'\x05\x01\x00')
                assert receive(sock, 2) == b'\x05\x00'
                sock.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', backend.server_port))
                header = receive(sock, 4)
                assert header[:2] == b'\x05\x00'
                receive(sock, 6 if header[3] == 1 else 18)
                sock.sendall(b'GET / HTTP/1.0\r\nHost: localhost\r\n\r\n')
                body = b''
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    body += chunk
                assert b'xboard-singbox-test' in body
            counters = stats.query()
            assert counters['user>>>1:7>>>traffic>>>uplink'] > 0
            assert counters['user>>>1:7>>>traffic>>>downlink'] >= 18 * 1024
            assert stats.query() == counters, 'read must not reset counters'
            print('PASS real transfer + gRPC accounting:', protocol, fields.get('cipher', ''))
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=10)
        backend.shutdown()
        backend.server_close()


def main():
    for protocol, fields in [
        ('vless', {}), ('anytls', {}), ('hysteria', {'version': 2}), ('tuic', {'version': 5}),
        ('shadowsocks', {'cipher': 'chacha20-ietf-poly1305'}),
        ('shadowsocks', {'cipher': '2022-blake3-aes-128-gcm', 'server_key': base64.b64encode(b'x' * 16).decode()}),
    ]:
        run_case(sys.argv[1], protocol, fields)


if __name__ == '__main__':
    main()
