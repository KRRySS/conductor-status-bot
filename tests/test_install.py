"""Opt-in integration with an installed Hermes checkout. Never touches real homes.

Every scenario below drives the REAL Hermes code paths (installer, config loader, MCP discovery,
tool dispatch) in a throwaway HOME. Set HERMES_SOURCE and HERMES_PYTHON to enable.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from tests.common import ROOT, fixture, fingerprint

# Runs inside the Hermes venv: start every configured MCP server through Hermes' own client,
# report what it registered, call the tool through Hermes' dispatcher, print JSON, shut down.
_HERMES_DRIVE = r'''
import json, logging, sys
from hermes_constants import set_hermes_home_override
profile = sys.argv[1]
if profile != "-":
    set_hermes_home_override(profile)          # multiplex shape: os.environ HERMES_HOME stays the launch home
log_lines = []
class _Cap(logging.Handler):
    def emit(self, r): log_lines.append(r.getMessage())
logging.getLogger().addHandler(_Cap()); logging.getLogger().setLevel(logging.INFO)
from tools.mcp_tool_discovery import discover_mcp_tools
from model_tools import handle_function_call
names = discover_mcp_tools()
out = {"registered": sorted(names), "log": [l for l in log_lines if "registered" in l or "failed" in l or "conductor" in l.lower()]}
if names:
    res = handle_function_call(names[0], {}, task_id=None)
    out["result"] = json.loads(res) if isinstance(res, str) else res
print("@@RESULT@@" + json.dumps(out, default=str))
try:
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    shutdown_mcp_servers()
except Exception:
    pass
'''
DOCTOR = 'skills/conductor-status/scripts/doctor.py'


def _payload_tool_json(result):
    """Hermes wraps MCP results; dig out the server's JSON document."""
    if isinstance(result, dict):
        for key in ("result", "structuredContent", "content"):
            if key in result:
                val = result[key]
                if isinstance(val, str):
                    try:
                        return json.loads(val)
                    except ValueError:
                        pass
                if isinstance(val, dict):
                    return val
                if isinstance(val, list) and val and isinstance(val[0], dict) and "text" in val[0]:
                    return json.loads(val[0]["text"])
        if "ok" in result:
            return result
    if isinstance(result, str):
        return json.loads(result)
    raise AssertionError(f"unrecognised tool result shape: {result!r}"[:500])


@unittest.skipUnless(os.environ.get('HERMES_SOURCE') and os.environ.get('HERMES_PYTHON'),
                     'set HERMES_SOURCE and HERMES_PYTHON to test the real Hermes installer')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(os.environ['HERMES_SOURCE']).resolve()
        self.python = Path(os.environ['HERMES_PYTHON']).absolute()  # preserve venv symlink
        self.tmpdir = tempfile.TemporaryDirectory(prefix='conductor-install-test-')
        self.base = Path(self.tmpdir.name)
        self.home = self.base / 'synthetic home'
        self.home.mkdir()
        self.payload = self.base / 'payload'
        shutil.copytree(ROOT, self.payload, ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv', '.test-artifacts'))
        tmp = self.base / 'tmp'
        tmp.mkdir()
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.home), 'HERMES_HOME': str(self.home / '.hermes'),
                    'TMPDIR': str(tmp), 'PYTHONPATH': str(self.source), 'PYTHONDONTWRITEBYTECODE': '1',
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        if os.environ.get('UV_CACHE_DIR'):
            self.env['UV_CACHE_DIR'] = os.environ['UV_CACHE_DIR']
        # Guard: Hermes' own resolver must point inside the throwaway home before any write.
        check = self.hermes('from hermes_cli.profiles import _get_profiles_root; print(_get_profiles_root())')
        self.assertEqual(Path(check.strip()), self.home / '.hermes/profiles')
        self.cli = 'from hermes_cli.main import main; main()'
        self.database = self.base / 'synthetic.db'
        self.db = fixture(self.database)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def hermes(self, code, *args, env=None, ok=True):
        result = subprocess.run([str(self.python), '-c', code, *args], env=env or self.env, cwd=self.base,
                                text=True, capture_output=True, timeout=240)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def drive(self, profile_override, env=None):
        """Start MCP through Hermes' client, call the tool through Hermes' dispatcher."""
        out = self.hermes(_HERMES_DRIVE, profile_override, env=env)
        line = next(l for l in out.splitlines() if l.startswith('@@RESULT@@'))
        return json.loads(line[len('@@RESULT@@'):])

    def doctor(self, profile):
        # System python on purpose: the doctor must work without Hermes' venv (stdlib only).
        return subprocess.run(['/usr/bin/python3', str(profile / DOCTOR)], text=True, capture_output=True)

    def apply_doctor_fix(self, doc_stdout):
        cmds = [l.strip() for l in doc_stdout.splitlines() if 'config set' in l or 'config unset' in l]
        self.assertTrue(cmds, doc_stdout)
        for c in cmds:
            self.hermes(self.cli, *shlex.split(c)[1:])  # drop the launcher path, keep "-p <name> config ..."

    def point_local_config(self, profile):
        (profile / 'local').mkdir(exist_ok=True)
        (profile / 'local/conductor-status.json').write_text(json.dumps({'db_path': str(self.database), 'include_events': True}))

    def assert_registered_and_working(self, driven, expected_tool='mcp__conductor_status__conductor_status'):
        self.assertEqual(driven['registered'], [expected_tool], driven['log'])
        self.assertTrue(any('registered 1 tool(s)' in l for l in driven['log']), driven['log'])
        data = _payload_tool_json(driven['result'])
        self.assertTrue(data.get('ok'), data)
        self.assertIn('read_at', data)
        self.assertEqual(data['counts']['visible_sessions'], 1)
        return data

    # --- Test 1: fresh install under a DIFFERENT name; `hermes -p` shape (HERMES_HOME = profile) ---
    def test_1_fresh_install_custom_name(self):
        self.hermes(self.cli, 'profile', 'install', str(self.payload), '--name', 'ct-test', '-y')
        profile = self.home / '.hermes/profiles/ct-test'
        self.assertTrue((profile / 'skills/conductor-status/scripts/launch.py').is_file())
        self.assertFalse((profile / 'tests').exists())
        self.assertFalse((profile / 'auth.json').exists())
        self.point_local_config(profile)
        # The shipped config names the manifest's default profile dir; under --name the doctor
        # must detect that and print the fix (Hermes cannot rewrite a preserved config for us).
        doc = self.doctor(profile)
        self.assertEqual(doc.returncode, 1, doc.stdout)
        self.assertIn('launch.py path is', doc.stdout)
        self.apply_doctor_fix(doc.stdout)
        self.assertEqual(self.doctor(profile).returncode, 0)
        driven = self.drive('-', env=dict(self.env, HERMES_HOME=str(profile)))
        self.assert_registered_and_working(driven)

    # --- Test 2: update on a profile carrying the PRESERVED 0.1.0 config; Test 5: no duplicate ---
    def test_2_update_from_010_config_and_5_no_duplicate(self):
        self.hermes(self.cli, 'profile', 'install', str(self.payload), '-y')
        profile = self.home / '.hermes/profiles/conductor-status-bot'
        # Regress config.yaml to the exact 0.1.0 shape (what real 0.1.x installs still carry).
        (profile / 'config.yaml').write_text(
            'mcp_servers:\n  conductor_status:\n    command: uv\n    args:\n      - run\n      - --no-project\n'
            '      - --locked\n      - --script\n      - ${HERMES_HOME}/skills/conductor-status/scripts/server.py\n'
            '    env:\n      PYTHONDONTWRITEBYTECODE: "1"\n      CONDUCTOR_STATUS_HOME: ${HERMES_HOME}\n'
            '    connect_timeout: 120\n    timeout: 120\n    sampling:\n      enabled: false\n'
            'model:\n  default: synthetic-model\n  provider: synthetic\n')
        (profile / 'skills/conductor-status/scripts/privacy.py').write_text('obsolete')
        (profile / 'memories/sentinel.txt').write_text('synthetic user-owned sentinel')
        manifest = self.payload / 'distribution.yaml'
        current = re.search(r'^version: (\S+)$', manifest.read_text(), re.M).group(1)
        manifest.write_text(manifest.read_text().replace(f'version: {current}', f'version: {current}.post1'))
        self.hermes(self.cli, 'profile', 'update', 'conductor-status-bot', '-y')
        cfg_text = (profile / 'config.yaml').read_text()
        self.assertIn('${HERMES_HOME}', cfg_text, 'config must be preserved on update')
        self.assertIn('synthetic-model', cfg_text, 'model block must survive')
        self.assertEqual((profile / 'memories/sentinel.txt').read_text(), 'synthetic user-owned sentinel')
        self.assertNotEqual((profile / 'skills/conductor-status/scripts/privacy.py').read_text(), 'obsolete')
        self.assertTrue((profile / 'skills/conductor-status/scripts/launch.py').is_file())
        # The doctor must name every 0.1.0 problem and print the fix with a full launcher path.
        doc = self.doctor(profile)
        self.assertEqual(doc.returncode, 1, doc.stdout)
        for marker in ('${HERMES_HOME}', 'CONDUCTOR_STATUS_HOME', '--locked', 'launch.py'):
            self.assertIn(marker, doc.stdout)
        self.assertRegex(doc.stdout, r'(\S*\.local/bin/hermes|hermes) -p conductor-status-bot config set')
        self.apply_doctor_fix(doc.stdout)
        cfg_text = (profile / 'config.yaml').read_text()
        for gone in ('${HERMES_HOME}', 'CONDUCTOR_STATUS_HOME', '--locked'):
            self.assertNotIn(gone, cfg_text)
        self.assertIn('synthetic-model', cfg_text, 'model block must survive the fix')
        self.assertEqual(cfg_text.count('conductor_status:'), 1, 'exactly one server entry (test 5)')
        self.assertEqual(self.doctor(profile).returncode, 0)
        self.point_local_config(profile)
        driven = self.drive('-', env=dict(self.env, HERMES_HOME=str(profile)))
        data = self.assert_registered_and_working(driven)
        self.assertEqual(len(driven['registered']), 1, 'no duplicate server/tool (test 5)')
        self.assertIn('[REDACTED]', data['sessions'][0]['recent_events'][0]['text'])

    # --- Test 3: desktop/multiplex shape — launch home is DEFAULT, profile bound by override ---
    # --- Test 4: tool returns ok/read_at/counts through Hermes' dispatcher ---
    def test_3_multiplex_launch_home_is_default_and_4_tool_payload(self):
        self.hermes(self.cli, 'profile', 'install', str(self.payload), '-y')
        profile = self.home / '.hermes/profiles/conductor-status-bot'
        self.point_local_config(profile)
        before = fingerprint(self.database)
        # Relocate the whole HOME too (spaces in path), then drive with HERMES_HOME=<default home>.
        moved = self.base / 'relocated home'
        self.home.rename(moved)
        profile = moved / '.hermes/profiles/conductor-status-bot'
        env = dict(self.env, HOME=str(moved), HERMES_HOME=str(moved / '.hermes'))
        driven = self.drive(str(profile), env=env)
        data = self.assert_registered_and_working(driven)
        self.assertEqual(data['events_included'], True)
        self.assertEqual(before, fingerprint(self.database), 'source DB/WAL/SHM untouched')
        scratch = profile / 'cache/scratch'
        leftovers = [p for p in scratch.iterdir() if p.name.startswith('conductor-status-')] if scratch.exists() else []
        self.assertEqual(leftovers, [])

    # --- Installer's exact GitHub shorthand normalization, no network/credentials ---
    def test_github_shorthand_normalization(self):
        self.hermes("from pathlib import Path; from unittest.mock import patch; from types import SimpleNamespace; from hermes_cli.profile_distribution import _git_clone; "
                 "p=patch('hermes_cli.git_credentials.run_git_with_credential_fallback', return_value=SimpleNamespace(returncode=0,stderr='')); "
                 "m=p.start(); _git_clone('github.com/KRRySS/conductor-status-bot', Path('unused')); "
                 "assert m.call_args.args[0][4] == 'https://github.com/KRRySS/conductor-status-bot'; p.stop()")
