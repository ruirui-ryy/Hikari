from __future__ import annotations

import json
import re
import threading
import time

from .core import atomic_write, digest, uid, within
from .notes import NoteStore


class Memory:
    def __init__(self, cfg, db, library):
        self.cfg, self.db, self.library = cfg, db, library
        self.root = cfg.home / 'memory'
        self.lock = threading.RLock()
        self.notes = NoteStore(cfg, db, library, self.lock)

    def context(self, query, budget=16000):
        """Prefer relevant personal memories over an arbitrary alphabetical prefix."""
        terms = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]{2}', query.lower()))
        rows = self.list()
        def score(row):
            text = (row['path'] + '\n' + (row['content'] or '')).lower()
            return (sum(term in text for term in terms),
                    row['path'].startswith(('profile/', 'learning/')), row['updated'])
        result = []
        for row in sorted(rows, key=score, reverse=True):
            item = {k: row[k] for k in ['path', 'protected', 'stale']}
            item['content'] = (row['content'] or '')[:4000]
            size = len(json.dumps(item, ensure_ascii=False))
            if size <= budget:
                result.append(item)
                budget -= size
        return result

    def apply_personal(self, updates, snapshot, prompt, tid):
        """Validate the whole proposal first; write through the existing revision journal.

        Stable keys let the model merge paraphrases and replace corrected facts. Only
        the two generated files are writable here; all other notes are dedup context.
        """
        if not isinstance(updates, list) or len(updates) > 12:
            raise ValueError('自动记忆更新列表无效')
        paths = {'profile/自动记忆.md', 'learning/自动记忆.md'}
        kinds = {'用户事实', '用户反馈', '助手推测'}
        old = {m['path']: m for m in snapshot}
        grouped = {}
        for item in updates:
            if not isinstance(item, dict) or set(item) != {'path', 'key', 'kind', 'text', 'quote'}:
                raise ValueError('自动记忆条目字段无效')
            if any(not isinstance(v, str) for v in item.values()):
                raise ValueError('自动记忆条目必须为文本')
            path, key, kind, text, quote = (item[k] for k in ['path', 'key', 'kind', 'text', 'quote'])
            if path not in paths or kind not in kinds or not re.fullmatch(r'[\w\-\u4e00-\u9fff]{1,60}', key):
                raise ValueError('自动记忆路径、类别或主题键无效')
            turns = prompt if isinstance(prompt, dict) else {tid: prompt}
            source_task = next((source for source, message in turns.items() if quote in message), None)
            if not text.strip() or len(text) > 600 or not quote.strip() or len(quote) > 600 or not source_task:
                raise ValueError('自动记忆必须附本轮用户原话，且长度有效')
            if any(c in text + quote for c in '\r\n'):
                raise ValueError('自动记忆条目不能跨行')
            if self.cfg.get('api_key') and self.cfg.get('api_key') in text + quote:
                raise ValueError('自动记忆包含服务密钥')
            grouped.setdefault(path, []).append(item | {'source_task': source_task})
        plans = []
        for path, items in grouped.items():
            previous = old.get(path)
            if previous and previous['protected']:
                raise ValueError('自动记忆文件受用户保护，未覆盖')
            before = (previous or {}).get('content') or ''
            lines = before.splitlines()
            for item in items:
                prefix = '- [' + item['key'] + '] '
                value = prefix + item['kind'] + '：' + item['text']
                matches = [i for i, line in enumerate(lines) if line.startswith(prefix)]
                if len(matches) > 1:
                    raise ValueError('自动记忆主题重复，需人工检查')
                if matches and lines[matches[0]].split('（依据：', 1)[0] == value:
                    continue
                line = value + '（依据：用户原话「' + item['quote'] + '」；轮次：' + item['source_task'] + '）'
                if matches:
                    lines[matches[0]] = line
                else:
                    lines.append(line)
            after = '\n'.join(lines) + '\n'
            if after != before:
                plans.append((path, after, (previous or {}).get('hash')))
        changed = 0
        for path, content, expected in plans:
            self.write(path, content, 'agent', expected)
            changed += 1
        return changed

    def reconcile(self):
        with self.lock:
            self.notes.recover()
            for revision in self.db.rows("SELECT r.*,m.path FROM revisions r JOIN memories m ON r.memory=m.id WHERE r.state='pending'"):
                if not re.fullmatch(r'(profile|learning|understanding)/[^/\\]+\.md', revision['path']):
                    self.db.execute("UPDATE revisions SET state='conflict' WHERE id=?", (revision['id'],))
                    continue
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
                # Version 1 notes have their own authority, conflict isolation and journal.
                # Never import them into legacy context, editing, deletion or undo paths.
                if not re.fullmatch(r'(profile|learning|understanding)/[^/\\]+\.md', rel):
                    continue
                if not self.db.one('SELECT id FROM memories WHERE path=?', (rel,)):
                    try:
                        text = within(self.root, rel).read_text('utf-8')
                        self.db.execute('INSERT INTO memories(id,path,hash,protected,updated) VALUES(?,?,?,?,?)',
                                        (uid(), rel, digest(text), 1, time.time()))
                    except (ValueError, OSError, UnicodeError):
                        pass
            result = []
            for row in self.db.rows('SELECT * FROM memories ORDER BY path'):
                if not re.fullmatch(r'(profile|learning|understanding)/[^/\\]+\.md', row['path']):
                    continue
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
            if not re.fullmatch(r'(profile|learning|understanding)/[^/\\]+\.md', rev['path']):
                raise ValueError('此记忆不支持旧版撤销')
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
