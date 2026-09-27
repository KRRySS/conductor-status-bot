import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/conductor-status/scripts'
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA wal_autocheckpoint=0;
CREATE TABLE workspaces(local_id TEXT,directory_name TEXT,branch TEXT,manual_status TEXT,derived_status TEXT,state TEXT);
CREATE TABLE sessions(id TEXT,title TEXT,status TEXT,agent_type TEXT,model TEXT,workspace_id TEXT,is_hidden INTEGER,updated_at TEXT);
CREATE TABLE session_messages(session_id TEXT,role TEXT,content TEXT,created_at TEXT);
INSERT INTO workspaces VALUES('w','synthetic-workspace','demo-branch','backlog','in-progress','active');
INSERT INTO sessions VALUES('s','Synthetic task','idle','test','test','w',0,'2026-01-01');
"""


def fixture(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute('INSERT INTO session_messages VALUES(?,?,?,?)',
               ('s', 'assistant', json.dumps({'text': 'Synthetic result token=synthetic-sensitive-value'}), '2026-01-01'))
    db.commit()
    return db


def fingerprint(path):
    # Includes existence, bytes and mtime for DB, WAL, SHM, rollback journal.
    result = {}
    for suffix in ('', '-wal', '-shm', '-journal'):
        file = Path(str(path) + suffix)
        result[suffix] = (file.stat().st_mtime_ns, hashlib.sha256(file.read_bytes()).hexdigest()) if file.exists() else None
    return result
