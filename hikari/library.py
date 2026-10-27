from __future__ import annotations

import logging
import re
import threading
import time
import zipfile
from pathlib import Path

import jieba

from .core import digest, uid, within

jieba.setLogLevel(logging.ERROR)
SUPPORTED = {'.pdf', '.pptx', '.md', '.docx'}


def tokens(text):
    return ' '.join(w.lower() for w in jieba.cut_for_search(text) if re.search(r'\w', w))


def fingerprint(path):
    import hashlib
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def extract(path):
    suffix = path.suffix.lower()
    if suffix in {'.pptx', '.docx'}:
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
                raise ValueError('Office 文件解压后超过 512 MB')
    if suffix == '.pdf':
        from pypdf import PdfReader
        pdf = PdfReader(path)
        return [(i, f'第 {i} 页', p.extract_text() or '') for i, p in enumerate(pdf.pages, 1)]
    if suffix == '.pptx':
        from pptx import Presentation
        result = []
        for i, slide in enumerate(Presentation(path).slides, 1):
            parts = []
            def shapes_text(shapes):
                for shape in shapes:
                    if shape.has_text_frame:
                        parts.append(shape.text)
                    if shape.has_table:
                        parts.extend(' | '.join(c.text for c in row.cells) for row in shape.table.rows)
                    if hasattr(shape, 'shapes'):
                        shapes_text(shape.shapes)
            shapes_text(slide.shapes)
            result.append((i, f'第 {i} 张幻灯片', '\n'.join(parts)))
        return result
    if suffix == '.docx':
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        result = []
        for i, block in enumerate(Document(path).iter_inner_content(), 1):
            text = block.text if isinstance(block, Paragraph) else '\n'.join(
                ' | '.join(c.text for c in row.cells) for row in block.rows)
            result.append((i, f'第 {i} 个段落/表格', text))
        return result
    text = path.read_text(encoding='utf-8-sig')
    result = []
    for match in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text):
        line = text.count('\n', 0, match.start()) + 1
        result.append((len(result) + 1, f'第 {line} 行起', match.group()))
    return result


class Library:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.lock = threading.RLock()
        self.refresh = threading.Event()
        self.scanning = False
        self.last_scan = None
        self.error = ''

    @property
    def root(self):
        return Path(self.cfg.get('knowledge')).resolve()

    def current(self, doc, version):
        row = self.db.one('SELECT * FROM documents WHERE id=?', (doc,))
        if not row or row['status'] != 'ready' or row['version'] != version:
            return False
        try:
            return fingerprint(within(self.root, row['path'])) == version
        except (OSError, ValueError):
            return False

    def scan(self):
        if not self.lock.acquire(blocking=False):
            return
        self.scanning = True
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            seen = set()
            for path in self.root.rglob('*'):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                    continue
                rel = path.relative_to(self.root).as_posix()
                seen.add(rel)
                old = self.db.one('SELECT * FROM documents WHERE path=?', (rel,))
                doc = old['id'] if old else uid()
                try:
                    safe = within(self.root, rel)
                    if safe.stat().st_size > self.cfg.get('max_upload_mb') * 1024 * 1024:
                        raise ValueError('文件超过当前大小上限')
                    version = fingerprint(safe)
                    if old and old['version'] == version and old['status'] in ['ready', 'error']:
                        continue
                    self.db.execute('INSERT INTO documents(id,path,version,status,updated) VALUES(?,?,?,?,?) '
                                    'ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated',
                                    (doc, rel, version, 'indexing', time.time()))
                    units = extract(safe)
                    if fingerprint(safe) != version:
                        raise ValueError('文件在解析时发生变化，稍后重试')
                    empty = sum(not t.strip() for _, _, t in units)
                    warning = f'{empty} 个位置没有可提取文字；可能包含扫描页或图片。' if empty else ''
                    if not any(t.strip() for _, _, t in units):
                        warning = '未提取到可检索文字。' + warning
                    warning += '第一版不识别图片，公式和图表可能缺失。' if path.suffix.lower() in ['.pdf', '.pptx'] else ''
                    with self.db.tx() as c:
                        self._clear(c, doc)
                        for position, label, text in units:
                            # Split long units without losing page/paragraph location.
                            for start in range(0, len(text), 3000):
                                part = text[start:start + 3300]
                                if not part.strip():
                                    continue
                                cur = c.execute('INSERT INTO chunks(doc,position,label,text) VALUES(?,?,?,?)',
                                                (doc, position, label, part))
                                c.execute('INSERT INTO search(rowid,tokens) VALUES(?,?)', (cur.lastrowid, tokens(part)))
                        c.execute('UPDATE documents SET version=?,status=?,warning=?,units=?,updated=? WHERE id=?',
                                  (version, 'ready', warning, len(units), time.time(), doc))
                except Exception as e:
                    message = str(e)[:300] if isinstance(e, ValueError) else f'解析失败（{type(e).__name__}）'
                    self.db.execute('INSERT INTO documents(id,path,version,status,warning,updated) VALUES(?,?,?,?,?,?) '
                                    'ON CONFLICT(id) DO UPDATE SET status=excluded.status,warning=excluded.warning,updated=excluded.updated',
                                    (doc, rel, '', 'error', message, time.time()))
            with self.db.tx() as c:
                for row in c.execute('SELECT id,path FROM documents').fetchall():
                    if row['path'] not in seen:
                        self._clear(c, row['id'])
                        c.execute("UPDATE documents SET status='deleted',updated=? WHERE id=?", (time.time(), row['id']))
            self.last_scan, self.error = time.time(), ''
        except Exception as e:
            self.error = f'资料扫描失败（{type(e).__name__}），请检查目录权限'
        finally:
            self.scanning = False
            self.lock.release()

    def _clear(self, c, doc):
        # Contentless FTS requires original token values to remove old postings.
        for row in c.execute('SELECT id,text FROM chunks WHERE doc=?', (doc,)).fetchall():
            c.execute("INSERT INTO search(search,rowid,tokens) VALUES('delete',?,?)", (row['id'], tokens(row['text'])))
        c.execute('DELETE FROM chunks WHERE doc=?', (doc,))

    def in_scope(self, path, scope):
        return not scope or path == scope or path.startswith(scope.rstrip('/') + '/')

    def catalog(self, scope='', include_deleted=False):
        sql = 'SELECT * FROM documents'
        if not include_deleted:
            sql += " WHERE status!='deleted'"
        return [r for r in self.db.rows(sql + ' ORDER BY path') if self.in_scope(r['path'], scope)]

    def purge_deleted(self):
        """Remove missing originals' index records; retain independent citation snapshots."""
        removed, skipped = [], []
        with self.lock, self.db.tx() as c:
            c.execute('BEGIN IMMEDIATE')
            for row in c.execute("SELECT id,path FROM documents WHERE status='deleted'").fetchall():
                path = within(self.root, row['path'])
                if path.exists():
                    skipped.append(row['path'])
                    continue
                self._clear(c, row['id'])
                c.execute("DELETE FROM documents WHERE id=? AND status='deleted'", (row['id'],))
                removed.append(row['path'])
        return {'removed': removed, 'skipped': skipped}

    def search(self, query, scope='', limit=10):
        words = list(dict.fromkeys(tokens(query).split()))[:24]
        if not words:
            return []
        expression = ' OR '.join('"' + w.replace('"', '""') + '"' for w in words)
        prefix = scope.rstrip('/') + '/'
        rows = self.db.rows('SELECT c.id,c.doc,c.position,c.label,c.text,d.path,d.version,bm25(search) AS rank '
                            'FROM search JOIN chunks c ON c.id=search.rowid JOIN documents d ON d.id=c.doc '
                            "WHERE search MATCH ? AND d.status='ready' AND (?='' OR d.path=? OR substr(d.path,1,?)=?) "
                            'ORDER BY rank LIMIT 40', (expression, scope, scope, len(prefix), prefix))
        result = []
        for row in rows:
            if self.in_scope(row['path'], scope):
                result.append(row | {'text': row['text'][:700]})
                if len(result) >= min(limit, 20):
                    break
        return result

    def read(self, task, doc, position, scope='', radius=0, chunk_id=None):
        item = self.db.one('SELECT * FROM documents WHERE id=?', (doc,))
        if not item or not self.in_scope(item['path'], scope):
            raise ValueError('资料不存在或不在当前范围')
        if not self.current(doc, item['version']):
            raise ValueError('原件已变化或尚未完成索引，请刷新后重查')
        if chunk_id is not None and not self.db.one('SELECT id FROM chunks WHERE id=? AND doc=? AND position=?', (chunk_id, doc, position)):
            raise ValueError('命中片段已变化，请重新搜索')
        chunks = self.db.rows('SELECT * FROM chunks WHERE doc=? AND position BETWEEN ? AND ? AND (? IS NULL OR id>=?) ORDER BY position,id',
                              (doc, max(1, position - min(radius, 1)), position + min(radius, 1), chunk_id, chunk_id))
        result, remaining = [], 18000
        for chunk in chunks:
            if remaining <= 0:
                break
            eid = uid()
            text = chunk['text'][:remaining]
            remaining -= len(text)
            evidence = dict(id=eid, task=task, doc=doc, version=item['version'], path=item['path'],
                            position=chunk['position'], label=chunk['label'], text=text)
            with self.db.tx() as c:
                c.execute('INSERT INTO evidence VALUES(:id,:task,:doc,:version,:path,:position,:label,:text)', evidence)
            result.append(evidence | {'citation': f'[[source:{eid}]]'})
        return result
