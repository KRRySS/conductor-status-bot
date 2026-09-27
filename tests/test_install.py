"""Opt-in integration with an installed Hermes checkout. Never touches real homes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml
from tests.common import ROOT, fixture, fingerprint
from tests.test_wire import exercise


@unittest.skipUnless(os.environ.get('HERMES_SOURCE') and os.environ.get('HERMES_PYTHON'),
                     'set HERMES_SOURCE and HERMES_PYTHON to test the real Hermes installer')
class InstallTests(unittest.TestCase):
    def test_cli_install_update_relocation_and_native_mcp_config(self):
        source = Path(os.environ['HERMES_SOURCE']).resolve()
        python = Path(os.environ['HERMES_PYTHON']).absolute()  # preserve venv symlink
        with tempfile.TemporaryDirectory(prefix='conductor-install-test-') as folder:
            base = Path(folder)
            home = base / 'synthetic home'
            home.mkdir()
            payload = base / 'payload'
            shutil.copytree(ROOT, payload, ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv', '.test-artifacts'))
            tmp = base / 'tmp'
            tmp.mkdir()
            env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'HERMES_HOME': str(home / '.hermes'),
                   'TMPDIR': str(tmp), 'PYTHONPATH': str(source), 'PYTHONDONTWRITEBYTECODE': '1',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
            if os.environ.get('UV_CACHE_DIR'):
                env['UV_CACHE_DIR'] = os.environ['UV_CACHE_DIR']
            def run(code, *args):
                result = subprocess.run([str(python), '-c', code, *args], env=env, cwd=base,
                                        text=True, capture_output=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            # This guard checks Hermes' own resolver before any installer write.
            check = run('from hermes_cli.profiles import _get_profiles_root; print(_get_profiles_root())')
            self.assertEqual(Path(check.strip()), home / '.hermes/profiles')
            cli = 'from hermes_cli.main import main; main()'
            run(cli, 'profile', 'install', str(payload), '--name', 'portable-status', '-y')
            profile = home / '.hermes/profiles/portable-status'
            self.assertTrue((profile / 'skills/conductor-status/scripts/server.py').is_file())
            self.assertFalse((profile / 'tests').exists())
            self.assertFalse((profile / 'auth.json').exists())
            database = base / 'synthetic.db'
            db = fixture(database)
            try:
                before = fingerprint(database)
                (profile / 'local').mkdir(exist_ok=True)
                local = profile / 'local/conductor-status.json'
                local.write_text(json.dumps({'db_path': str(database), 'include_events': True}))
                (profile / 'memories/sentinel.txt').write_text('synthetic user-owned sentinel')
                config = profile / 'config.yaml'
                original = config.read_text() + '\n# synthetic local customization\n'
                config.write_text(original)
                # Poison shipped file, then prove update restores it and preserves user data.
                (profile / 'skills/conductor-status/scripts/privacy.py').write_text('obsolete')
                manifest = payload / 'distribution.yaml'
                manifest.write_text(manifest.read_text().replace('version: 0.1.0', 'version: 0.1.1'))
                run(cli, 'profile', 'update', 'portable-status', '-y')
                self.assertEqual(config.read_text(), original)
                self.assertEqual(local.read_text(), json.dumps({'db_path': str(database), 'include_events': True}))
                self.assertEqual((profile / 'memories/sentinel.txt').read_text(), 'synthetic user-owned sentinel')
                self.assertIn('0.1.1', (profile / 'distribution.yaml').read_text())
                self.assertNotEqual((profile / 'skills/conductor-status/scripts/privacy.py').read_text(), 'obsolete')
                # Rename the entire HOME and Hermes root, then use the real native config loader.
                moved = base / 'relocated home'
                home.rename(moved)
                profile = moved / '.hermes/profiles/portable-status'
                env.update(HOME=str(moved), HERMES_HOME=str(profile))
                resolved = run('import json; from tools.mcp_tool_config import _load_mcp_config, _build_safe_env; c=_load_mcp_config()["conductor_status"]; c["env"]=_build_safe_env(c.get("env")); print(json.dumps(c))')
                cfg = json.loads(resolved.strip().splitlines()[-1])
                self.assertEqual(cfg['args'][-1], str(profile / 'skills/conductor-status/scripts/server.py'))
                self.assertEqual(cfg['env']['CONDUCTOR_STATUS_HOME'], str(profile))
                error, data = asyncio.run(exercise(cfg['command'], cfg['args'], cfg['env'], base))
                self.assertFalse(error)
                self.assertEqual(data['session_count'], 1)
                self.assertIn('[REDACTED]', data['sessions'][0]['recent_events'][0]['text'])
                self.assertEqual(before, fingerprint(database))
                # Installer's exact GitHub shorthand normalization, no network/credentials.
                run("from pathlib import Path; from unittest.mock import patch; from types import SimpleNamespace; from hermes_cli.profile_distribution import _git_clone; "
                    "p=patch('hermes_cli.git_credentials.run_git_with_credential_fallback', return_value=SimpleNamespace(returncode=0,stderr='')); "
                    "m=p.start(); _git_clone('github.com/KRRySS/conductor-status-bot', Path('unused')); "
                    "assert m.call_args.args[0][4] == 'https://github.com/KRRySS/conductor-status-bot'; p.stop()")
            finally:
                db.close()
