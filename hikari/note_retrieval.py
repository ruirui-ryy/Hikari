"""Explicit note retrieval. Markdown is authoritative; vectors are disposable."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections import Counter

from .core import ROOT, digest
from .library import tokens

MODEL = 'BAAI/bge-small-zh-v1.5'
MODEL_REVISION = '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
MODEL_DIR = ROOT / 'local' / 'embedding' / 'bge-small-zh-v1.5'
PIPELINE = 'title80-body300-overlap60-v1'


def status(row):
    """Clues, never a claim that an unchallenged note is currently true."""
    meta = row['meta']
    return dict(nature=meta['nature'], valid_from=meta.get('valid_from'),
                valid_until=meta.get('valid_until'), successors=row['summary'],
                review_hints=row['review_hints'], available=row['available'], issues=row['issues'])


class LocalEmbedding:
    def __init__(self):
        self.model = None
        self.version = None

    def load(self):
        if self.model is None:
            try:
                from fastembed import TextEmbedding
                manifest = json.loads((MODEL_DIR / 'manifest.json').read_text('utf-8'))
                if manifest['model'] != MODEL or manifest['revision'] != MODEL_REVISION:
                    raise ValueError('wrong model')
                # Verify the exact downloaded artifacts before reusing vector caches.
                for name, expected in manifest['files'].items():
                    if digest((MODEL_DIR / name).read_bytes()) != expected:
                        raise ValueError('changed embedding artifacts')
                self.version = digest(json.dumps(manifest, sort_keys=True)) + ':' + PIPELINE
                self.model = TextEmbedding(MODEL, specific_model_path=str(MODEL_DIR),
                                           cache_dir=str(MODEL_DIR), local_files_only=True,
                                           threads=2, providers=['CPUExecutionProvider'])
            except Exception:
                raise ValueError('本地语义模型未准备好，请在主机运行 scripts/prepare_memory_embedding.py；关键词仍可用') from None

    def encode(self, texts, *, query=False):
        self.load()
        iterator = self.model.query_embed(texts) if query else self.model.passage_embed(texts, batch_size=8)
        result = []
        for vector in iterator:
            values = vector.tolist()
            norm = math.sqrt(sum(v*v for v in values)) or 1
            result.append([v/norm for v in values])
        return result


class NoteRetrieval:
    def __init__(self, store, embedder=None):
        self.store = store
        self.embedder = embedder or LocalEmbedding()
        self.lock = threading.RLock()
        self.cache = ROOT / 'local' / 'memory-retrieval' / digest(str(store.cfg.home)) / 'vectors.sqlite3'

    def read(self, mid):
        row = self.store.read(mid)
        return dict(id=row['meta']['id'], title=row['meta']['title'], markdown=row['content'],
                    body=row['body'], hash=row['hash'], status=status(row),
                    relations=row['meta']['relations'], sources=row['sources'])

    @staticmethod
    def _keyword(query, rows):
        words = set(tokens(query).split())
        docs = [Counter(tokens(r['meta']['title'] + '\n' + r['body']).split()) for r in rows]
        average = sum(sum(d.values()) for d in docs) / max(len(docs), 1) or 1
        frequencies = {w: sum(w in d for d in docs) for w in words}
        scores = {}
        for row, doc in zip(rows, docs):
            length = sum(doc.values())
            score = sum(math.log(1 + (len(rows)-frequencies[w]+0.5)/(frequencies[w]+0.5)) *
                        doc[w]*2.2/(doc[w]+1.2*(0.25+0.75*length/average)) for w in words if doc[w])
            if score:
                scores[row['meta']['id']] = score
        return scores

    def _semantic(self, query, rows):
        self.embedder.load()
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY, hash TEXT, version TEXT, data TEXT)')
            cached = {r[0]: r[1:] for r in db.execute('SELECT * FROM vectors')}
            vectors = {}
            for row in rows:
                mid, title = row['meta']['id'], row['meta']['title']
                entry = cached.get(mid)
                if entry and entry[:2] == (row['hash'], self.embedder.version):
                    vectors[mid] = json.loads(entry[2])
                else:
                    body = row['body']
                    chunks = [title[:80] + '\n' + body[i:i+300] for i in range(0, max(1, len(body)), 240)]
                    vectors[mid] = self.embedder.encode(chunks)
                    db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)',
                               (mid, row['hash'], self.embedder.version, json.dumps(vectors[mid])))
            # Deleted, invalid, or quarantined records never survive as searchable vectors.
            for mid in cached.keys() - vectors.keys():
                db.execute('DELETE FROM vectors WHERE id=?', (mid,))
        question = self.embedder.encode([query], query=True)[0]
        return {mid: max(sum(a*b for a, b in zip(question, v)) for v in chunks)
                for mid, chunks in vectors.items()}

    def search(self, query, mode, limit=5):
        if mode not in {'keyword', 'semantic', 'hybrid'}:
            raise ValueError('必须明确选择 keyword、semantic 或 hybrid')
        if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not 1 <= limit <= 20:
            raise ValueError('查询须为 1–1000 字符，候选数须为 1–20')
        with self.lock:
            rows = [r for r in self.store.scan() if r['available'] and r['meta']]
            if not rows:
                return []
            keyword = self._keyword(query, rows) if mode != 'semantic' else {}
            semantic = self._semantic(query, rows) if mode != 'keyword' else {}
            def ranked(scores):
                return sorted(scores, key=lambda mid: (-scores[mid], mid))
            if mode == 'hybrid':
                scores = {}
                for ranking in [ranked(keyword), ranked(semantic)]:
                    for rank, mid in enumerate(ranking, 1):
                        scores[mid] = scores.get(mid, 0) + 1/(60+rank)
            else:
                scores = keyword if mode == 'keyword' else semantic
            by_id = {r['meta']['id']: r for r in rows}
            return [dict(id=mid, title=by_id[mid]['meta']['title'], status=status(by_id[mid]), score=scores[mid])
                    for mid in ranked(scores)[:limit]]
