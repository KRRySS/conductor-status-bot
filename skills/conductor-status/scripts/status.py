"""SQLite reader. Source files are only opened as bytes, never by SQLite.

A stable capture is a best-effort consistency check, not an atomic live backup.
Busy sources are refused after bounded retries. Only the disposable copy is recovered.
"""
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


def stamp():
    return datetime.now().astimezone().isoformat()


def read_db(path):
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute("""SELECT s.id,s.title,s.status,s.agent_type,s.model,
            w.directory_name AS workspace,w.branch,w.manual_status,w.derived_status
            FROM sessions s JOIN workspaces w ON s.workspace_id=w.local_id
            WHERE s.is_hidden=0 AND w.state!='archived'
            ORDER BY julianday(s.updated_at) DESC LIMIT 101""").fetchall()
        visible = db.execute("""SELECT count(*) FROM sessions s JOIN workspaces w
            ON s.workspace_id=w.local_id WHERE s.is_hidden=0 AND w.state!='archived'""").fetchone()[0]
        result = []
        for row in rows[:100]:
            item = dict(row)
            item['recent_events'] = []
            for msg in db.execute('SELECT role,content,created_at FROM session_messages WHERE session_id=? ORDER BY julianday(created_at) DESC LIMIT 40', (row['id'],)):
                try:
                    event = json.loads(msg['content'])
                except (ValueError, TypeError):
                    event = {'text': msg['content']}
                if not isinstance(event, dict):
                    continue
                text = event.get('result') or event.get('text')
                if not text:
                    message = event.get('message')
                    body = message.get('content', []) if isinstance(message, dict) else []
                    text = body if isinstance(body, str) else '\n'.join(str(b.get('text', '')) for b in (body if isinstance(body, list) else []) if isinstance(b, dict) and b.get('type') == 'text')
                if text:
                    item['recent_events'].append({'role': msg['role'], 'time': msg['created_at'], 'type': event.get('type'), 'is_error': event.get('is_error'), 'text': str(text)})
                if len(item['recent_events']) >= 4:
                    break
            result.append(item)
        return {'ok': True, 'read_at': stamp(), 'session_count': len(result),
                'counts': {'all_sessions': db.execute('SELECT count(*) FROM sessions').fetchone()[0], 'visible_sessions': visible},
                'sessions_truncated': visible > len(result), 'events_are_partial': True,
                'sessions': result}

def signature(path):
    try:
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    except FileNotFoundError:
        return None


def capture_stable(path):
    # Never open a live database as immutable or omit its WAL.
    files = [path, Path(str(path) + '-wal'), Path(str(path) + '-journal')]
    before = [signature(p) for p in files]
    if before[0] is None:
        raise FileNotFoundError('Source database missing')
    if before[2] and before[2][2]:
        raise RuntimeError('Rollback journal present; refusing raw snapshot')
    if sum(s[2] for s in before if s) > 256 * 1024 * 1024:
        raise RuntimeError('Source exceeds 256 MiB capture limit')
    first = [p.read_bytes() if s is not None else None for p, s in zip(files, before)]
    middle = [signature(p) for p in files]
    second = [p.read_bytes() if s is not None else None for p, s in zip(files, middle)]
    after = [signature(p) for p in files]
    if before != middle or middle != after or first != second:
        raise RuntimeError('Source changed during capture; refusing inconsistent snapshot')
    return first, stamp()


def private_snapshot(path, scratch):
    data, captured_at = capture_stable(path)
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='conductor-status-', dir=scratch) as folder:
        copy = Path(folder) / 'snapshot.db'
        copy.write_bytes(data[0])
        if data[1] is not None:
            Path(str(copy) + '-wal').write_bytes(data[1])
        # Rebuild SHM / recover WAL only on the disposable copy. No writes to Conductor.
        with closing(sqlite3.connect(copy.as_uri() + '?mode=rw', uri=True)) as db:
            check = db.execute('PRAGMA quick_check').fetchall()
            if check != [('ok',)]:
                raise RuntimeError('Private snapshot failed integrity check')
        result = read_db(copy)
        result['source'] = 'verified private DB+WAL copy; Conductor source read-only'
        result['captured_at'] = captured_at
        return result



def snapshot(path, scratch):
    errors = []
    for attempt in range(3):
        try:
            result = private_snapshot(Path(path).expanduser().resolve(), scratch)
            result.update(read_method='stable_private_copy', attempts=attempt + 1)
            return result
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            # No local paths, SQL fragments or arbitrary exception text in model output.
            errors.append({'type': type(exc).__name__,
                           'sqlite_errorname': getattr(exc, 'sqlite_errorname', None)})
            if attempt < 2:
                time.sleep(0.5)
    return {'ok': False, 'read_at': stamp(), 'errors': errors,
            'hint': 'Could not capture/read Conductor. Check local db_path, permissions, schema, size limit or a busy source.'}
