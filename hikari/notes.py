"""Markdown authority, rebuildable index, and recoverable multi-file operations.

Filesystem replacements are individually atomic, never advertised as group atomic.
All readers rescan files and consult the durable journal before offering AI data.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from collections import defaultdict
from uuid import uuid4

from .core import atomic_write, digest, within
from .note_format import INVERSE, filename, parse, render


def graph_issues(rows):
    issues = defaultdict(list)
    by_id = defaultdict(list)
    for path, row in rows.items():
        if row.get('meta'):
            by_id[row['meta']['id']].append(path)
    for paths in by_id.values():
        if len(paths) > 1:
            for path in paths:
                issues[path].append('重复 ID：所有同 ID 文件均隔离')
    graphs = {kind: defaultdict(set) for kind in ['supersedes', 'corrects']}
    for path, row in rows.items():
        if not row.get('meta'):
            continue
        meta = row['meta']
        for edge in meta['relations']:
            targets = by_id.get(edge['target'], [])
            if len(targets) != 1:
                issues[path].append('关系目标缺失或 ID 歧义：' + edge['target'])
                continue
            target = rows[targets[0]]['meta']
            if not any(e['type'] == INVERSE[edge['type']] and e['target'] == meta['id']
                       for e in target['relations']):
                issues[path].append('关系缺少反向边：' + edge['target'])
                issues[targets[0]].append('关系缺少对应边：' + meta['id'])
            kind = edge['type']
            if kind in graphs:
                graphs[kind][meta['id']].add(edge['target'])
            elif kind in {'superseded_by', 'corrected_by'}:
                graphs[INVERSE[kind]][edge['target']].add(meta['id'])
    # Separate supersession/correction meanings, but forbid mixed evolution cycles too.
    combined = defaultdict(set)
    for graph in graphs.values():
        for node, targets in graph.items():
            combined[node].update(targets)
    # Iterative reachability avoids recursion limits for externally edited large graphs.
    for start in combined:
        pending, visited = list(combined[start]), set()
        while pending:
            node = pending.pop()
            if node == start:
                for path in by_id[start]:
                    issues[path].append('取代/纠正关系存在自指或循环')
                break
            if node not in visited:
                visited.add(node)
                pending.extend(combined.get(node, ()))
    return issues


class NoteStore:
    def __init__(self, cfg, db, library, lock=None):
        self.cfg, self.db, self.library = cfg, db, library
        self.root = cfg.home / 'memory' / 'notes'
        self.lock = lock or threading.RLock()
        with db.tx() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS memory_operations(
                id TEXT PRIMARY KEY,state TEXT NOT NULL,actor TEXT NOT NULL,created REAL NOT NULL,
                plan TEXT NOT NULL,repairs TEXT,repaired_by TEXT);
              CREATE TABLE IF NOT EXISTS memory_note_index(path TEXT PRIMARY KEY,id TEXT,hash TEXT,meta TEXT,issues TEXT);
              CREATE INDEX IF NOT EXISTS memory_note_identity ON memory_note_index(id);
            ''')

    def _path(self, path):
        if not isinstance(path, str) or '/' in path or '\\' in path or not path.endswith('.md'):
            raise ValueError('notes/ 仅支持单层 Markdown 文件')
        target = self.root / path
        if self.root.resolve() != self.root.absolute() or target.is_symlink() or target.is_junction():
            raise ValueError('记忆目录或文件不能为链接')
        return within(self.root, path)

    def _raw(self, path):
        target = self._path(path)
        return target.read_bytes() if target.exists() else None

    def _files(self):
        if not self.root.exists():
            return {}
        result = {}
        for file in sorted(self.root.glob('*.md')):
            try:
                result[file.name] = self._raw(file.name)
            except (OSError, ValueError):
                result[file.name] = None
        return result

    def _decode(self, files):
        rows = {}
        for path, raw in files.items():
            row = dict(path=path, hash=digest(raw) if raw is not None else None, meta=None, issues=[])
            try:
                row['content'] = raw.decode('utf-8')
                row['meta'], row['body'] = parse(row['content'])
            except (ValueError, UnicodeError, AttributeError, OSError, RecursionError):
                # Parser messages have field names, never source text or credentials.
                row['issues'].append('UTF-8 或元信息格式无效')
                try:
                    if raw is not None:
                        parse(raw.decode('utf-8'))
                except ValueError as exc:
                    if not isinstance(exc, UnicodeError):
                        row['issues'] = [str(exc)]
                except (AttributeError, RecursionError):
                    pass
            rows[path] = row
        for path, issues in graph_issues(rows).items():
            rows[path]['issues'].extend(issues)
        return rows

    def scan(self):
        """Rebuild only derived state. Journal and snapshots are never deleted here."""
        with self.lock:
            rows = self._decode(self._files())
            for op in self.db.rows("SELECT * FROM memory_operations WHERE state IN ('pending','conflict')"):
                plan = json.loads(op['plan'])
                ids = {item['id'] for item in plan if item.get('id')}
                paths = {item['path'] for item in plan}
                for path, row in rows.items():
                    meta = row['meta']
                    if path in paths or (meta and (meta['id'] in ids or any(e['target'] in ids for e in meta['relations']))):
                        row['issues'].append('关联操作未完成：' + op['id'])
            by_id = {r['meta']['id']: r for r in rows.values() if r['meta'] and not r['issues']}
            for row in rows.values():
                row['available'] = not row['issues']
                row['summary'], row['review_hints'], row['sources'] = [], [], []
                meta = row['meta']
                if not meta:
                    continue
                for edge in meta['relations']:
                    if edge['type'] in {'superseded_by', 'corrected_by'}:
                        target = by_id.get(edge['target'])
                        date = (target or {}).get('meta', {}).get('valid_from')
                        row['summary'].append(dict(type=edge['type'], target=edge['target'], valid_from=date,
                                                   reason=edge['reason']))
                    if edge['type'] == 'derived_from':
                        base = by_id.get(edge['target'])
                        if base and any(e['type'] == 'corrected_by' for e in base['meta']['relations']):
                            row['review_hints'].append('依据已被纠正，结论保留待复核：' + edge['target'])
                for source in meta['sources']:
                    current = None
                    if source['type'] == 'document':
                        current = bool(self.library.current(source['doc'], source['version']))
                    row['sources'].append(source | {'excerpt_state': 'historical_saved' if 'excerpt' in source else 'manual',
                                                     'current_document_version': current,
                                                     'excerpt_reverified': False})
            with self.db.tx() as c:
                c.execute('DELETE FROM memory_note_index')
                for path, row in rows.items():
                    c.execute('INSERT INTO memory_note_index VALUES(?,?,?,?,?)',
                              (path, (row['meta'] or {}).get('id'), row['hash'], json.dumps(row['meta']), json.dumps(row['issues'])))
            return list(rows.values())

    def read(self, mid, *, for_ai=False):
        rows = [r for r in self.scan() if r['meta'] and r['meta']['id'] == mid]
        if len(rows) != 1:
            raise ValueError('记忆 ID 不存在或存在歧义')
        if for_ai and not rows[0]['available']:
            raise ValueError('记忆异常，暂不可提供给 AI')
        return rows[0]

    def operation(self, oid):
        """Explicit local inspection; snapshots are never added to AI context."""
        row = self.db.one('SELECT * FROM memory_operations WHERE id=?', (oid,))
        if not row:
            raise ValueError('操作不存在')
        row['files'] = [{**item, 'before': self._unpack(item['before']), 'after': self._unpack(item['after'])}
                        for item in json.loads(row.pop('plan'))]
        return row

    def _checkpoint(self, stage, operation, index=None):
        """Fault-injection seam; production deliberately does nothing."""

    @staticmethod
    def _repair_group(operations, oid):
        """A failed repair must not permanently block either its parent or a retry."""
        group = {oid}
        while True:
            linked = {o['id'] for o in operations if o['id'] in group or o['repairs'] in group}
            linked.update(o['repairs'] for o in operations if o['id'] in group and o['repairs'])
            if linked <= group:
                return group
            group.update(linked)

    def apply(self, changes, expected, *, actor='user', repairs=None):
        """Apply complete file proposal. expected contains the hash (or None) for every path.

        Callers must supply both directions of changed relations. The entire candidate
        graph is validated before persisting any intent. Unrelated invalid files remain.
        Explicit repair must cover every old operation path using its current content.
        """
        with self.lock:
            if not isinstance(changes, dict) or not changes or set(changes) != set(expected):
                raise ValueError('必须提供整组文件及每个文件的预期 hash')
            old_ops = self.db.rows("SELECT * FROM memory_operations WHERE state IN ('pending','conflict')")
            repair = next((o for o in old_ops if o['id'] == repairs and o['state'] == 'conflict'), None)
            repair_ids = self._repair_group(old_ops, repairs) if repair else set()
            repair_ops = [o for o in old_ops if o['id'] in repair_ids]
            if repairs:
                paths = {p['path'] for o in repair_ops for p in json.loads(o['plan'])}
                if not repair or not paths <= changes.keys():
                    raise ValueError('修复必须明确引用冲突操作并覆盖原整组文件')
                if any(o['state'] == 'pending' for o in repair_ops):
                    raise ValueError('修复操作尚待恢复，不能重叠提交')
            before = self._files()
            candidate = dict(before)
            folded = {}
            for path in before.keys() | changes.keys():
                if path.casefold() in folded and folded[path.casefold()] != path:
                    raise ValueError('文件名大小写存在路径冲突')
                folded[path.casefold()] = path
            for path, text in changes.items():
                self._path(path)
                if (digest(before[path]) if before.get(path) is not None else None) != expected[path]:
                    raise ValueError('文件已被修改，请重新读取当前整组内容')
                if text is None:
                    candidate.pop(path, None)
                else:
                    parse(text)
                    secret = self.cfg.get('api_key')
                    if secret and secret in text:
                        raise ValueError('记忆包含服务密钥，拒绝写入')
                    candidate[path] = text.encode('utf-8')
            previous, proposed = self._decode(before), self._decode(candidate)
            for path, row in proposed.items():
                if row['issues'] and (path in changes or row['issues'] != previous.get(path, {}).get('issues')):
                    raise ValueError('方案存在无效元信息或关系：' + path + '；' + '; '.join(row['issues']))
            touched_ids = {(r.get('meta') or {}).get('id') for p in changes for r in [previous.get(p, {}), proposed.get(p, {})]} - {None}
            for op in old_ops:
                if op['id'] in repair_ids:
                    continue
                plan = json.loads(op['plan'])
                if any(p['path'] in changes or p.get('id') in touched_ids for p in plan):
                    raise ValueError('关联操作未完成，须先恢复或显式修复')
            plan = []
            for path in changes:
                row = proposed.get(path) or previous.get(path, {})
                plan.append(dict(path=path, id=(row.get('meta') or {}).get('id'),
                                 before=self._pack(before.get(path)), after=self._pack(candidate.get(path))))
            oid = str(uuid4())
            self._checkpoint('validated', oid)
            self.db.execute('INSERT INTO memory_operations VALUES(?,?,?,?,?,?,NULL)',
                            (oid, 'pending', actor, time.time(), json.dumps(plan), repairs))
            self._checkpoint('persisted', oid)
            self._finish(oid)
            return oid

    @staticmethod
    def _pack(raw):
        return base64.b64encode(raw).decode('ascii') if raw is not None else None

    @staticmethod
    def _unpack(raw):
        return base64.b64decode(raw) if raw is not None else None

    def _finish(self, oid):
        op = self.db.one('SELECT * FROM memory_operations WHERE id=?', (oid,))
        if not op or op['state'] != 'pending':
            return
        plan = json.loads(op['plan'])
        locations = defaultdict(set)
        for item in plan:
            if item.get('id'):
                locations[item['id']].add(item['path'].casefold())
            for snapshot in [item['before'], item['after']]:
                if snapshot is not None:
                    try:
                        meta, _ = parse(self._unpack(snapshot).decode('utf-8'))
                    except (ValueError, UnicodeError):
                        continue
                    locations[meta['id']].add(item['path'].casefold())

        def conflict():
            self.db.execute("UPDATE memory_operations SET state='conflict' WHERE id=?", (oid,))
            raise ValueError('关联操作未完成：检测到外部编辑，整组停止并保留现场')

        def check():
            # Absence at a creation path also matches its before snapshot. Check
            # UUID locations so an external rename/copy is not mistaken for an
            # unwritten file and recreated at the old name during recovery.
            for path, raw in self._files().items():
                if raw is None:
                    continue
                try:
                    meta, _ = parse(raw.decode('utf-8'))
                except (ValueError, UnicodeError):
                    continue
                allowed = locations.get(meta['id'])
                if allowed is not None and path.casefold() not in allowed:
                    conflict()
            for item in plan:
                try:
                    raw = self._raw(item['path'])
                except (OSError, ValueError):
                    conflict()
                if raw not in (self._unpack(item['before']), self._unpack(item['after'])):
                    conflict()

        check()
        self._checkpoint('checked', oid)
        for index, item in enumerate(plan):
            check()  # Check the whole group, including already replaced files.
            target = self._path(item['path'])
            after = self._unpack(item['after'])
            if self._raw(item['path']) != after:
                if after is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write(target, after.decode('utf-8'))
            self._checkpoint('replaced', oid, index)
        check()
        for item in plan:
            if self._raw(item['path']) != self._unpack(item['after']):
                conflict()
        self._checkpoint('verified', oid)
        repair_ids = self._repair_group(self.db.rows(
            "SELECT * FROM memory_operations WHERE state IN ('pending','conflict')"), op['repairs']) if op['repairs'] else set()
        with self.db.tx() as c:
            c.execute("UPDATE memory_operations SET state='applied' WHERE id=?", (oid,))
            for repaired in repair_ids:
                c.execute("UPDATE memory_operations SET state='repaired',repaired_by=? WHERE id=? AND state='conflict'",
                          (oid, repaired))
        self._checkpoint('completed', oid)

    def recover(self):
        with self.lock:
            for op in self.db.rows("SELECT id FROM memory_operations WHERE state='pending' ORDER BY created,id"):
                try:
                    self._finish(op['id'])
                except ValueError:
                    if self.db.one('SELECT state FROM memory_operations WHERE id=?', (op['id'],))['state'] != 'conflict':
                        raise

    def add(self, text):
        meta, _ = parse(text)
        path = filename(meta)
        return self.apply({path: text}, {path: None})

    def relate(self, source, target, kind, reason):
        if kind not in INVERSE:
            raise ValueError('关系类型无效')
        rows = [self.read(source), self.read(target)]
        changes, expected = {}, {}
        for row, edge_kind, other in [(rows[0], kind, target), (rows[1], INVERSE[kind], source)]:
            meta = row['meta']
            meta['relations'] = [e for e in meta['relations'] if (e['type'], e['target']) != (edge_kind, other)]
            meta['relations'].append(dict(type=edge_kind, target=other, reason=reason))
            changes[row['path']], expected[row['path']] = render(meta, row['body']), row['hash']
        return self.apply(changes, expected)
