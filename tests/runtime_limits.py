"""Real authenticated TCP/UDP admission, using loopback only."""
import json
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from runtime_stats import receive, wait_port


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while data := self.request.recv(4096):
                self.request.sendall(data)
        except OSError:
            pass


class UDPEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        sock.sendto(data, self.client_address)


def connect(port, user, target, udp=False):
    sock = socket.create_connection(('127.0.0.1', port), timeout=2)
    sock.sendall(b'\x00' + uuid.UUID(user).bytes + b'\x00' + (b'\x02' if udp else b'\x01')
                 + struct.pack('!H', target) + b'\x01' + socket.inet_aton('127.0.0.1'))
    return sock


def exchange(sock, udp=False, first=True):
    message = b'limit-test'
    sock.sendall((struct.pack('!H', len(message)) if udp else b'') + message)
    if first:
        assert receive(sock, 2) == b'\x00\x00'
    if udp:
        assert struct.unpack('!H', receive(sock, 2))[0] == len(message)
    assert receive(sock, len(message)) == message


def blocked(port, user, target, udp=False):
    with connect(port, user, target, udp) as sock:
        try:
            exchange(sock, udp)
        except (OSError, RuntimeError):
            return
    raise AssertionError('session bypassed configured limit')


def main(executable):
    tcp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo)
    tcp.daemon_threads = True
    udp = socketserver.ThreadingUDPServer(('127.0.0.1', 0), UDPEcho)
    for backend in (tcp, udp):
        threading.Thread(target=backend.serve_forever, daemon=True).start()
    users = [str(uuid.uuid4()), str(uuid.uuid4())]
    cfg = {'log': {'level': 'warn'}, 'inbounds': [
        {'type': 'vless', 'tag': f'node-{node}', 'listen': '127.0.0.1', 'listen_port': port,
         'users': [{'name': f'{node}:{i+7}', 'uuid': value} for i, value in enumerate(users)]}
        for node, port in [(1, 21981), (2, 21982)]],
        'outbounds': [{'type': 'direct', 'tag': 'direct'}],
        'route': {'final': 'direct', 'user_limits': {'scope': 'user',
            'defaults': {'max_tcp': 2, 'max_udp': 1, 'max_total': 3},
            'users': {'8': {'new_per_second': 1, 'burst': 2}}}}}
    live = []
    process = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(cfg))
            process = subprocess.Popen([executable, 'run', '-c', str(path)],
                                       stdout=subprocess.DEVNULL, stderr=None)
            wait_port(21981)
            for port in (21981, 21982):
                sock = connect(port, users[0], tcp.server_address[1]); live.append(sock); exchange(sock)
            blocked(21981, users[0], tcp.server_address[1])
            sock = connect(21981, users[0], udp.server_address[1], True); live.append(sock); exchange(sock, True)
            blocked(21982, users[0], udp.server_address[1], True)
            for _ in range(2):
                with connect(21981, users[1], tcp.server_address[1]) as other:
                    exchange(other)
            blocked(21982, users[1], tcp.server_address[1])
            exchange(live[0], first=False)
            live.pop(0).close()
            live.pop().close()
            time.sleep(0.5)
            with connect(21981, users[0], tcp.server_address[1]) as renewed:
                exchange(renewed)
            with connect(21982, users[0], udp.server_address[1], True) as renewed:
                exchange(renewed, True)
            time.sleep(1.1)
            with connect(21981, users[1], tcp.server_address[1]) as renewed:
                exchange(renewed)
            print('PASS TCP/UDP caps, cross-node identity, isolation, close/release, burst/refill')
    finally:
        for sock in live:
            sock.close()
        if process is not None:
            process.terminate(); process.wait(timeout=10)
        for backend in (tcp, udp):
            backend.shutdown(); backend.server_close()


if __name__ == '__main__':
    main(sys.argv[1])
