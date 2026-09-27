import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tests.common import ROOT, fixture, fingerprint


async def exercise(command, args, env, cwd):
    params = StdioServerParameters(command=command, args=args, env=env, cwd=str(cwd))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == 'conductor-status'
            tools = (await session.list_tools()).tools
            assert [tool.name for tool in tools] == ['conductor_status']
            assert tools[0].input_schema['additionalProperties'] is False
            assert tools[0].annotations.read_only_hint is True
            good = await session.call_tool('conductor_status', {})
            data = json.loads(good.content[0].text)
            assert data == good.structured_content
            for arguments in ({'sql': 'DELETE FROM sessions'}, {'db_path': '/not-allowed'}, {'command': 'anything'}):
                bad = await session.call_tool('conductor_status', arguments)
                assert bad.is_error
                assert 'Unknown tool or unexpected arguments' in bad.content[0].text
            unknown = await session.call_tool('not_a_tool', {})
            assert unknown.is_error
            return good.is_error, data


def server_args(profile):
    # Same shape as the shipped config.yaml: no --locked (the adjacent server.py.lock is honoured
    # by `uv run --script` regardless), no env var needed for profile discovery.
    return ['run', '--no-project', '--script', str(profile / 'skills/conductor-status/scripts/server.py')]


class WireTests(unittest.TestCase):
    def _env(self, base, **extra):
        env = {'PATH': os.environ['PATH'], 'HOME': str(base), 'PYTHONDONTWRITEBYTECODE': '1'}
        # Keep the package cache outside the synthetic home, never credentials.
        if os.environ.get('UV_CACHE_DIR'):
            env['UV_CACHE_DIR'] = os.environ['UV_CACHE_DIR']
        env.update(extra)
        return env

    def test_initialize_list_call_redaction_readonly_and_optin(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            profile = base / 'profile with spaces'
            shutil.copytree(ROOT / 'skills', profile / 'skills')
            (profile / 'local').mkdir()
            database = base / 'fixture.db'
            db = fixture(database)
            try:
                before = fingerprint(database)
                config = profile / 'local/conductor-status.json'
                config.write_text(json.dumps({'db_path': str(database)}))
                env = self._env(base)
                args = server_args(profile)
                error, data = asyncio.run(exercise('uv', args, env, base))
                self.assertFalse(error)
                self.assertNotIn('recent_events', data['sessions'][0])
                config.write_text(json.dumps({'db_path': str(database), 'include_events': True}))
                error, data = asyncio.run(exercise('uv', args, env, base))
                self.assertFalse(error)
                text = data['sessions'][0]['recent_events'][0]['text']
                self.assertIn('[REDACTED]', text)
                self.assertNotIn('synthetic-sensitive-value', json.dumps(data))
                self.assertEqual(before, fingerprint(database))
                self.assertEqual(list((profile / 'cache/scratch').iterdir()), [])
                config.write_text(json.dumps({'db_path': str(base / 'missing.db')}))
                error, data = asyncio.run(exercise('uv', args, env, base))
                self.assertTrue(error)
                self.assertFalse(data['ok'])
                self.assertNotIn('sessions', data)
                self.assertFalse((base / 'missing.db').exists())
            finally:
                db.close()

    def test_profile_discovery_without_env_and_mismatch_guard(self):
        # No CONDUCTOR_STATUS_HOME: the server must find its profile from its own file location.
        # A CONDUCTOR_STATUS_HOME pointing at a DIFFERENT directory (the ${HERMES_HOME}-expands-to-
        # launch-profile trap) must be a clean tool error, never a crash or a silent misread.
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            profile = base / 'renamed-profile'
            shutil.copytree(ROOT / 'skills', profile / 'skills')
            (profile / 'local').mkdir()
            database = base / 'fixture.db'
            db = fixture(database)
            try:
                (profile / 'local/conductor-status.json').write_text(json.dumps({'db_path': str(database)}))
                args = server_args(profile)
                error, data = asyncio.run(exercise('uv', args, self._env(base), base))
                self.assertFalse(error)
                self.assertEqual(data['session_count'], 1)
                matching = self._env(base, CONDUCTOR_STATUS_HOME=str(profile))
                error, data = asyncio.run(exercise('uv', args, matching, base))
                self.assertFalse(error)
                wrong = self._env(base, CONDUCTOR_STATUS_HOME=str(base / 'some-other-home'))
                error, data = asyncio.run(exercise('uv', args, wrong, base))
                self.assertTrue(error)
                self.assertFalse(data['ok'])
                self.assertNotIn('sessions', data)
            finally:
                db.close()
