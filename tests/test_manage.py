"""Run lifecycle shell code against fake services; never touch the host services."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bash'), 'Linux shell lifecycle tests')
class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'CALLS': str(self.root / 'calls'), 'AGENT_RESULT': '1'}
        self.script = self.root / 'manage.sh'
        self.script.write_text((PROJECT / 'sync/manage.sh').read_text().replace('$EUID', '0')
                               .replace('/opt/', str(self.root / 'opt') + '/'))
        self.make('systemctl', '#!/bin/bash\necho "$*" >> "$CALLS"\n[[ $1 != is-active ]]\n')
        self.make('systemd-run', '#!/bin/bash\necho "systemd-run $*" >> "$CALLS"\n')
        agent = self.root / 'opt/singbox-sync/venv/bin/python'
        agent.parent.mkdir(parents=True)
        agent.write_text('#!/bin/bash\necho "agent $*" >> "$CALLS"\nexit "$AGENT_RESULT"\n')
        agent.chmod(0o755)

    def make(self, name, contents):
        path = self.bin / name
        path.write_text(contents)
        path.chmod(0o755)

    def run_script(self, *args):
        result = subprocess.run(['bash', str(self.script), *args], env=self.env, capture_output=True)
        return result.returncode, (self.root / 'calls').read_text()

    def test_existing_init_failure_restores_daemon(self):
        config = self.root / 'opt/singbox/config/config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{}')
        code, calls = self.run_script('--worker', 'init')
        self.assertEqual(code, 1)
        self.assertIn('--once', calls)
        self.assertIn('enable --now xboard-singbox.service', calls)

    def test_first_init_failure_does_not_start_unconfigured_daemon(self):
        code, calls = self.run_script('--worker', 'init')
        self.assertEqual(code, 1)
        self.assertIn('--init', calls)
        self.assertNotIn('enable --now', calls)

    def test_first_init_success_enables_daemon(self):
        self.env['AGENT_RESULT'] = '0'
        code, calls = self.run_script('--worker', 'init')
        self.assertEqual(code, 0)
        self.assertIn('enable --now', calls)

    def test_public_command_is_owned_by_systemd(self):
        code, calls = self.run_script('sync')
        self.assertEqual(code, 0)
        self.assertIn('systemd-run --unit=xbs-operation', calls)
        self.assertNotIn('stop xboard', calls)

    def upgrade_fixture(self):
        work = self.root / 'project'
        work.mkdir()
        for name in ('sync', 'core'):
            shutil.copytree(PROJECT / name, work / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('docker-compose.yml', 'Dockerfile', '.dockerignore', 'requirements.txt', 'limits.example.json'):
            shutil.copy(PROJECT / name, work / name)
        shutil.copytree(PROJECT / 'systemd', work / 'systemd')
        script = work / 'upgrade-core.sh'
        text = (PROJECT / 'upgrade-core.sh').read_text().replace('$EUID', '0')
        for name in ('/opt/', '/usr/local/', '/etc/systemd/'):
            text = text.replace(name, str(self.root) + name)
        script.write_text(text)
        for name, content in [('opt/singbox-sync/agent.py', '# old'),
                              ('opt/singbox-sync/state.json', '{"checkpoint":42}'),
                              ('opt/singbox/config/config.json', '{"old":true}'),
                              ('usr/local/bin/xbs', '# old'),
                              ('etc/systemd/system/xboard-singbox.service', '# old')]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        interpreter = self.root / 'opt/singbox-sync/venv/bin/python'
        interpreter.write_text('#!/bin/bash\nif [[ $1 == -m ]]; then exit 0; fi\n'
                               'if [[ $1 == */agent.py ]]; then exit 1; fi\n'
                               f'exec "{sys.executable}" "$@"\n')
        self.make('docker', '#!/bin/bash\necho "docker $*" >> "$CALLS"\n'
                  'if [[ $1 == build ]]; then exit "${BUILD_RESULT:-0}"; fi\n'
                  'if [[ $1 == inspect ]]; then echo old-generation; fi\n'
                  'if [[ $* == *"config --format json"* ]]; then echo \'{"services":{"singbox":{"image":"old"}}}\'; fi\n')
        self.env['INVOCATION_ID'] = 'test'
        return script

    def test_failed_core_build_never_stops_daemon(self):
        script = self.upgrade_fixture()
        self.env['BUILD_RESULT'] = '1'
        result = subprocess.run(['bash', str(script)], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('stop xboard', (self.root / 'calls').read_text())

    def test_failed_validation_preserves_running_core_and_accounting(self):
        script = self.upgrade_fixture()
        result = subprocess.run(['bash', str(script)], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        calls = (self.root / 'calls').read_text()
        self.assertIn('stop xboard', calls)
        self.assertIn('enable --now', calls)
        self.assertNotIn('--force-recreate', calls)
        self.assertEqual((self.root / 'opt/singbox/config/config.json').read_text(), '{"old":true}')
        self.assertEqual((self.root / 'opt/singbox-sync/state.json').read_text(), '{"checkpoint":42}')


if __name__ == '__main__':
    unittest.main()
