from __future__ import annotations

import json
import re
import threading
import time

from .core import atomic_write, digest, uid, within


class Memory:
    def __init__(self, cfg, db, library):
        self.cfg, self.db, self.library = cfg, db, library
        self.root = cfg.home / 'memory'
        self.lock = threading.RLock()

    def reconcile(self):
        with self.lock:
            for revision in self.db.rows("SELECT r.*,m.path FROM revisions r JOIN memories m ON r.memory=m.id WHERE r.state='pending'"):
                path = within(self.root, revision['path'])
                text = path.read_text('utf-8') if path.exists() else None
                if text == revision['after_text']:
                    meta = json.loads(revision['after_meta'])
                    self.db.execute("UPDATE revisions SET state='applied' WHERE id=?", (revision['id'],))
                    self.db.execute('UPDATE memories SET hash=?,protected=?,sources=? WHERE id=?',
                                    (digest(text), meta.get('protected', 1), json.dumps(meta.get('sources', [])), revision['memory']))
                else:
                    self.db.execute("UPDATE revisions SET state='interrupted' WHERE id=?", (revision['id'],))
                    self.db.execute('UPDATE memories SET protected=1 WHERE id=?', (revision['memory'],))

    def list(self, query=''):
        with self.lock:
            # Handwritten Markdown files are imported as user-protected memories.
            for path in self.root.rglob('*.md'):
                rel = path.relative_to(self.root).as_posix()
                if not self.db.one('SELECT id FROM memories WHERE path=?', (rel,)):
                    try:
                        text = within(self.root, rel).read_text('utf-8')
                        self.db.execute('INSERT INTO memories(id,path,hash,protected,updated) VALUES(?,?,?,?,?)',
                                        (uid(), rel, digest(text), 1, time.time()))
                    except (ValueError, OSError, UnicodeError):
                        pass
            result = []
            for row in self.db.rows('SELECT * FROM memories ORDER BY path'):
                try:
                    path = within(self.root, row['path'])
                    text = path.read_text('utf-8') if path.exists() else None
                except (ValueError, OSError, UnicodeError):
                    text = None
                changed = text is None or digest(text) != row['hash']
                if changed and not row['protected']:
                    self.db.execute('UPDATE memories SET protected=1 WHERE id=?', (row['id'],))
                sources = json.loads(row['sources'])
                stale = any(not self.library.current(s['doc'], s['version']) for s in sources)
                if not query or query.lower() in (row['path'] + '\n' + (text or '')).lower():
                    result.append(row | {'content': text, 'hash': digest(text) if text is not None else None,
                                         'protected': bool(row['protected'] or changed), 'stale': stale,
                                         'sources': sources})
            return result

    def write(self, path, content, actor, expected_hash=None, sources=None):
        with self.lock:
            if not re.fullmatch(r'(profile|learning|understanding)/[^/\\]{1,100}\.md', path):
                raise ValueError('记忆路径应为 profile/主题.md、learning/主题.md 或 understanding/主题.md')
            if len(content) > 40000:
                raise ValueError('单份记忆不能超过 40000 字符')
            secret = self.cfg.get('api_key')
            if secret and secret in content:
                raise ValueError('记忆内容包含服务密钥，已拒绝写入')
            target = within(self.root, path)
            old = self.db.one('SELECT * FROM memories WHERE path=?', (path,))
            exists = target.exists()
            before = target.read_text('utf-8') if exists else ''
            actual = digest(before) if exists else None
            if actual != expected_hash:
                raise ValueError('记忆已被修改，请重新读取后处理冲突')
            if actor == 'agent' and ((old and (old['protected'] or actual != old['hash'])) or (exists and not old)):
                raise ValueError('用户编辑或删除过此记忆，需由用户决定，不能自动覆盖')
            if path.startswith('understanding/') and actor == 'agent' and not sources:
                raise ValueError('资料理解笔记必须附本轮已读来源')
            mid = old['id'] if old else uid()
            rid = uid()
            before_meta = {'sources': json.loads(old['sources']) if old else [], 'protected': old['protected'] if old else 0}
            after_meta = {'sources': sources if sources is not None else before_meta['sources'], 'protected': int(actor != 'agent')}
            with self.db.tx() as c:
                if not old:
                    c.execute('INSERT INTO memories(id,path,hash,updated) VALUES(?,?,?,?)', (mid, path, actual, time.time()))
                c.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (rid, mid, before, content, int(exists), actor, 'pending', time.time(),
                           json.dumps(before_meta), json.dumps(after_meta)))
            # Compare again immediately before atomic replacement to catch external edits.
            latest = target.read_text('utf-8') if target.exists() else None
            if latest != (before if exists else None):
                self.db.execute("UPDATE revisions SET state='conflict' WHERE id=?", (rid,))
                raise ValueError('写入前检测到外部编辑，已保留用户内容')
            atomic_write(target, content)
            with self.db.tx() as c:
                c.execute('UPDATE memories SET hash=?,protected=?,sources=?,updated=? WHERE id=?',
                          (digest(content), after_meta['protected'], json.dumps(after_meta['sources'], ensure_ascii=False), time.time(), mid))
                c.execute("UPDATE revisions SET state='applied' WHERE id=?", (rid,))
            return {'id': mid, 'revision': rid, 'hash': digest(content)}

    def undo(self, rid):
        with self.lock:
            rev = self.db.one("SELECT r.*,m.path FROM revisions r JOIN memories m ON r.memory=m.id WHERE r.id=?", (rid,))
            if not rev or rev['state'] != 'applied':
                raise ValueError('修订不存在或不可撤销')
            if self.db.one("SELECT id FROM revisions WHERE memory=? AND created>? AND state='applied'", (rev['memory'], rev['created'])):
                raise ValueError('已有后续修订，不能覆盖')
            target = within(self.root, rev['path'])
            if not target.exists() or target.read_text('utf-8') != rev['after_text']:
                raise ValueError('内容已有外部修改，不能覆盖')
            # Undo itself is a journalled revision; empty new files remain protected tombstones.
            self.write(rev['path'], rev['before_text'], 'undo', digest(rev['after_text']), json.loads(rev['before_meta']).get('sources', []))
            self.db.execute("UPDATE revisions SET state='undone' WHERE id=?", (rid,))

    def delete(self, mid, expected_hash):
        with self.lock:
            row = self.db.one('SELECT * FROM memories WHERE id=?', (mid,))
            if not row:
                raise ValueError('记忆不存在')
            path = within(self.root, row['path'])
            if not path.exists() or digest(path.read_text('utf-8')) != expected_hash:
                raise ValueError('内容变化，请刷新后重试')
            # Journal deletion as an empty protected entry. Keep the file to make protection visible.
            return self.write(row['path'], '', 'user', expected_hash)
