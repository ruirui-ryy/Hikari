from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def uid():
    return secrets.token_hex(12)


def digest(text: str | bytes):
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uid() + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def within(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('路径超出允许目录')
    return path


class Config:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get('HIKARI_DATA', ROOT / 'data')).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / 'config.json'
        self.lock = threading.RLock()
        self.values = dict(model='deepseek-flash', base_url='https://api.deepseek.com', api_key='',
                           knowledge=str(self.home / 'knowledge'), max_tools=24, max_seconds=300,
                           scan_seconds=15, memory_idle_seconds=120, max_upload_mb=100, secure_cookie=False, username='', password='', port=18765)
        if self.path.exists():
            self.values.update(json.loads(self.path.read_text('utf-8')))
        root = Path(self.values['knowledge']).expanduser().resolve()
        if self.home.is_relative_to(root) or any(root.is_relative_to(self.home / part) for part in ['memory', 'generated', 'staging', 'backups', 'email']):
            raise ValueError('资料目录必须独立于数据库、记忆、生成内容和备份目录')
        for folder in ['knowledge', 'memory', 'generated', 'staging', 'backups']:
            (self.home / folder).mkdir(exist_ok=True)

    def get(self, name):
        with self.lock:
            if name == 'api_key':
                return self.values[name] or os.environ.get('DEEPSEEK_API_KEY', '')
            return self.values[name]

    def save(self, values):
        with self.lock:
            self.values.update(values)
            atomic_write(self.path, json.dumps(self.values, ensure_ascii=False, indent=2))

    def public(self):
        with self.lock:
            return {k: v for k, v in self.values.items() if k not in ['api_key', 'password']} | {
                'has_api_key': bool(self.get('api_key'))}


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        with self.tx() as c:
            c.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,scope TEXT DEFAULT '',created REAL);
              CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,chat TEXT,role TEXT,content TEXT,task TEXT,created REAL);
              CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,chat TEXT,prompt TEXT,status TEXT,error TEXT DEFAULT '',
                request_id TEXT UNIQUE,created REAL,updated REAL,scope TEXT DEFAULT '',dismissed INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,expires REAL);
              CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,path TEXT UNIQUE,version TEXT,status TEXT,
                warning TEXT DEFAULT '',units INTEGER DEFAULT 0,updated REAL);
              CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,doc TEXT,position INTEGER,label TEXT,text TEXT);
              CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(tokens,content='',tokenize='unicode61');
              CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,task TEXT,doc TEXT,version TEXT,path TEXT,
                position INTEGER,label TEXT,text TEXT);
              CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,path TEXT UNIQUE,hash TEXT,protected INTEGER DEFAULT 0,
                sources TEXT DEFAULT '[]',updated REAL);
              CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,memory TEXT,before_text TEXT,after_text TEXT,
                before_exists INTEGER,actor TEXT,state TEXT,created REAL,before_meta TEXT DEFAULT '{}',after_meta TEXT DEFAULT '{}');
              CREATE INDEX IF NOT EXISTS message_chat ON messages(chat,created);
              CREATE INDEX IF NOT EXISTS chunk_doc ON chunks(doc,position);
              CREATE INDEX IF NOT EXISTS evidence_task ON evidence(task);
            ''')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(revisions)')}
            for column in ['before_meta', 'after_meta']:
                if column not in columns:
                    c.execute(f"ALTER TABLE revisions ADD COLUMN {column} TEXT DEFAULT '{{}}'")
            task_columns = {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}
            if 'dismissed' not in task_columns:
                c.execute('ALTER TABLE tasks ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0')
            for column in ['memory_status', 'memory_error']:
                if column not in task_columns:
                    c.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def tx(self):
        with self.lock:
            c = sqlite3.connect(self.path, timeout=20)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    def rows(self, sql, args=()):
        with self.tx() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.tx() as c:
            c.execute(sql, args)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + value


def password_matches(password, stored):
    try:
        return secrets.compare_digest(password_hash(password, stored.split(':')[0]), stored)
    except (ValueError, TypeError):
        return False


class InstanceLock:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('已有 Hikari 实例使用这个数据目录') from None

    def close(self):
        self.file.close()
