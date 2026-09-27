import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.common import SCRIPTS, fixture, fingerprint
sys.path.insert(0, str(SCRIPTS))
import status
from privacy import sanitize


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'fixture.db'
        self.db = fixture(self.path)
        self.addCleanup(self.db.close)
        self.scratch = self.folder / 'scratch'

    def test_wal_capture_readonly_cleanup(self):
        before = fingerprint(self.path)
        self.assertGreater(Path(str(self.path) + '-wal').stat().st_size, 0)
        result = status.snapshot(self.path, self.scratch)
        self.assertTrue(result['ok'])
        self.assertEqual(result['session_count'], 1)
        self.assertIn('Synthetic result', result['sessions'][0]['recent_events'][0]['text'])
        self.assertEqual(result['read_method'], 'stable_private_copy')
        self.assertEqual(before, fingerprint(self.path))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_source_never_opened_by_sqlite(self):
        original = sqlite3.connect
        def guarded(database, *args, **kwargs):
            self.assertNotIn(self.path.as_uri(), str(database))
            self.assertIn('snapshot.db', str(database))
            return original(database, *args, **kwargs)
        with patch.object(status.sqlite3, 'connect', side_effect=guarded):
            self.assertTrue(status.snapshot(self.path, self.scratch)['ok'])

    def test_filters_counts_and_labels(self):
        self.db.execute("INSERT INTO sessions SELECT 'hidden',title,status,agent_type,model,workspace_id,1,updated_at FROM sessions")
        self.db.execute("INSERT INTO workspaces VALUES('a','archived','branch','done','done','archived')")
        self.db.execute("INSERT INTO sessions VALUES('archived','hidden','idle','test','test','a',0,'2026-01-01')")
        self.db.commit()
        result = status.snapshot(self.path, self.scratch)
        self.assertEqual(result['counts'], {'all_sessions': 3, 'visible_sessions': 1})
        self.assertEqual(result['sessions'][0]['manual_status'], 'backlog')
        self.assertEqual(result['sessions'][0]['derived_status'], 'in-progress')

    def test_corrupt_copy_failure_cleanup(self):
        broken = self.folder / 'corrupt.db'
        broken.write_bytes(b'not a SQLite database')
        before = broken.read_bytes()
        with patch.object(status.time, 'sleep'):
            result = status.snapshot(broken, self.scratch)
        self.assertFalse(result['ok'])
        self.assertEqual(broken.read_bytes(), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_bytes_changed_despite_stable_metadata_rejected(self):
        original = Path.read_bytes
        calls = 0
        def changing(path):
            nonlocal calls
            data = original(path)
            if path == self.path:
                calls += 1
                if calls == 2:
                    return data + b'changed'
            return data
        with patch.object(Path, 'read_bytes', changing):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                status.capture_stable(self.path)

    def test_retry_then_success(self):
        original = status.private_snapshot
        with patch.object(status, 'private_snapshot', side_effect=[RuntimeError('busy'), original(self.path, self.scratch)]), patch.object(status.time, 'sleep'):
            result = status.snapshot(self.path, self.scratch)
        self.assertTrue(result['ok'])
        self.assertEqual(result['attempts'], 2)

    def test_changed_source_rejected(self):
        with patch.object(status, 'signature', side_effect=[(1,1,1), None, None, (2,1,1), None, None, (3,1,1), None, None]):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                status.capture_stable(self.path)

    def test_journal_rejected(self):
        Path(str(self.path) + '-journal').write_bytes(b'synthetic journal')
        with self.assertRaisesRegex(RuntimeError, 'Rollback journal'):
            status.capture_stable(self.path)

    def test_capture_size_limit(self):
        with patch.object(status, 'signature', side_effect=[(1,1,257*1024*1024), None, None]):
            with self.assertRaisesRegex(RuntimeError, 'limit'):
                status.capture_stable(self.path)

    def test_missing_or_wrong_schema_is_error_not_empty(self):
        wrong = self.folder / 'wrong.db'
        sqlite3.connect(wrong).close()
        for path in (wrong, self.folder / 'missing.db'):
            with self.subTest(path=path.name), patch.object(status.time, 'sleep'):
                result = status.snapshot(path, self.scratch)
            self.assertFalse(result['ok'])
            self.assertNotIn('sessions', result)
            self.assertNotIn(str(self.folder), json.dumps(result))
        self.assertFalse((self.folder / 'missing.db').exists())

    def test_bounded_sessions_and_events(self):
        self.db.executemany("INSERT INTO sessions VALUES(?, 'synthetic', 'idle','test','test','w',0,'2026-01-02')", [(str(i),) for i in range(105)])
        self.db.commit()
        result = status.snapshot(self.path, self.scratch)
        self.assertEqual(result['session_count'], 100)
        self.assertEqual(result['counts']['visible_sessions'], 106)
        self.assertTrue(result['sessions_truncated'])


class PrivacyTests(unittest.TestCase):
    def test_obvious_secret_patterns_recursive(self):
        tokens = ['ghp_'+'a'*30, 'sk-'+'b'*30, 'xoxb-'+'c'*20,
                  'eyJabc.def.ghi', 'synthetic-sensitive-value']
        raw = {'title': tokens[0], 'nested': [{'text': ' '.join(tokens[1:4])}],
               'text': 'password="synthetic-sensitive-value" Bearer synthetic-sensitive-value',
               'path': '/Users/example/project', 'url': 'https://user:pass@example.invalid/'}
        clean = json.dumps(sanitize(raw))
        for token in tokens:
            self.assertNotIn(token, clean)
        self.assertNotIn('/Users/example', clean)
        self.assertNotIn('user:pass', clean)

    def test_private_key_before_truncation(self):
        raw = '-----BEGIN PRIVATE KEY-----\n' + 'SENSITIVE'*1000 + '\n-----END PRIVATE KEY-----'
        self.assertEqual(sanitize(raw), '[REDACTED PRIVATE KEY]')
        self.assertTrue(sanitize('a'*6100).endswith('[TRUNCATED]'))

    def test_plain_text_not_secret_guarantee(self):
        self.assertEqual(sanitize('Synthetic task done'), 'Synthetic task done')
