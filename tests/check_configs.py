"""Validate all generated inbounds with an actual sing-box executable.

Usage: python tests/check_configs.py /path/to/sing-box [--without-stats]
The second option is for upstream release binaries lacking with_v2ray_api.
"""
import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import config


def main():
    executable = sys.argv[1]
    skip_stats = '--without-stats' in sys.argv
    user = {'users': [{'id': 1, 'uuid': '12345678-1234-4234-8234-123456789abc'}]}
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        pair = subprocess.check_output([executable, 'generate', 'tls-keypair', 'example.com'], text=True)
        certificate = pair[pair.index('-----BEGIN CERTIFICATE-----'):pair.index('-----END CERTIFICATE-----') + len('-----END CERTIFICATE-----')]
        private_key = re.search(r'-----BEGIN (?:\w+ )?PRIVATE KEY-----.*?-----END (?:\w+ )?PRIVATE KEY-----', pair, re.S).group()
        (directory / 'cert.pem').write_text(certificate)
        (directory / 'key.pem').write_text(private_key)
        reality = subprocess.check_output([executable, 'generate', 'reality-keypair'], text=True)
        reality_key = reality.split('PrivateKey: ')[1].splitlines()[0].strip()
        variants = [
            ('anytls', {}), ('hysteria', {'version': 2, 'obfs': 'salamander', 'obfs-password': 'test'}),
            ('tuic', {'version': 5}), ('vless', {'tls': 2, 'flow': 'xtls-rprx-vision',
                'tls_settings': {'server_name': 'example.com', 'private_key': reality_key, 'short_id': 'abcd'}}),
            ('shadowsocks', {'cipher': 'chacha20-ietf-poly1305'}),
            ('shadowsocks', {'cipher': '2022-blake3-aes-128-gcm', 'server_key': base64.b64encode(b'x'*16).decode()}),
        ]
        for index, (protocol, fields) in enumerate(variants):
            inbound = config.inbound(str(index + 1), protocol, {'server_port': 20000 + index, **fields}, user)
            tls = inbound.get('tls', {})
            if 'certificate_path' in tls:
                tls['certificate_path'] = str(directory / 'cert.pem')
                tls['key_path'] = str(directory / 'key.pem')
            generated = config.build([inbound])
            if skip_stats:
                generated.pop('experimental')
            path = directory / 'config.json'
            path.write_text(json.dumps(generated))
            subprocess.run([executable, 'check', '-c', str(path)], check=True)
            print('PASS', protocol, fields.get('cipher', ''))


if __name__ == '__main__':
    main()
