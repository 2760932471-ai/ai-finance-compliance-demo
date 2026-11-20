"""Versioned local persistence. No uploaded content is executable."""
import json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
DB = DATA / 'audit.sqlite3'

def now(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
def uid(): return uuid.uuid4().hex[:16]
@contextmanager
def connect():
    c=sqlite3.connect(DB, timeout=30); c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=30000')
        with c:yield c
    finally:c.close()
def init():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))')
def put(kind, obj):
    with connect() as c:
        c.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body', (kind,obj['id'],json.dumps(obj,ensure_ascii=False)))
    return obj
def get(kind, id):
    with connect() as c: row=c.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,id)).fetchone()
    if not row: raise KeyError('记录不存在')
    return json.loads(row['body'])
def all_items(kind):
    with connect() as c: rows=c.execute('SELECT body FROM objects WHERE kind=? ORDER BY rowid DESC',(kind,)).fetchall()
    return [json.loads(r['body']) for r in rows]
def related(kind,key,value): return [x for x in all_items(kind) if x.get(key)==value]

init()
