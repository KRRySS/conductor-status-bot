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
            assert initialized.serverInfo.name == 'conductor-status'
            tools = (await session.list_tools()).tools
            assert [tool.name for tool in tools] == ['conductor_status']
            assert tools[0].inputSchema['additionalProperties'] is False
            assert tools[0].annotations.readOnlyHint is True
            good = await session.call_tool('conductor_status', {})
            data = json.loads(good.content[0].text)
            assert data == good.structuredContent
            for arguments in ({'sql': 'DELETE FROM sessions'}, {'db_path': '/not-allowed'}, {'command': 'anything'}):
                bad = await session.call_tool('conductor_status', arguments)
                assert bad.isError
                assert 'Unknown tool or unexpected arguments' in bad.content[0].text
            unknown = await session.call_tool('not_a_tool', {})
            assert unknown.isError
            return good.isError, data


class WireTests(unittest.TestCase):
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
                env = {'PATH': os.environ['PATH'], 'HOME': str(base),
                       'CONDUCTOR_STATUS_HOME': str(profile), 'PYTHONDONTWRITEBYTECODE': '1'}
                # Keep the package cache outside the synthetic home, never credentials.
                if os.environ.get('UV_CACHE_DIR'):
                    env['UV_CACHE_DIR'] = os.environ['UV_CACHE_DIR']
                args = ['run', '--no-project', '--locked', '--script', str(profile / 'skills/conductor-status/scripts/server.py')]
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
