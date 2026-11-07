"""Synthetic contract tests; fake vectors do not establish semantic quality."""
import json

import pytest
from fastapi.testclient import TestClient

from hikari.app import create_app
from hikari.core import password_hash
from hikari.note_format import create, parse
from hikari.note_retrieval import NoteRetrieval


class FakeEmbedding:
    version = 'test-v1'
    calls = 0

    def load(self):
        pass

    def encode(self, texts, query=False):
        if not query:
            self.calls += len(texts)
        return [[1., 0.] if '早上' in text else [0., 1.] for text in texts]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    app = create_app(tmp_path, background=False)
    app.state.cfg.save({'username': 'owner', 'password': password_hash('test-password-123')})
    retrieval = app.state.note_retrieval
    retrieval.cache = tmp_path / 'vectors.sqlite3'
    retrieval.embedder = FakeEmbedding()
    with TestClient(app) as client:
        client.headers['X-Hikari'] = '1'
        client.post('/api/login', json={'username': 'owner', 'password': 'test-password-123'})
        yield retrieval, client


def add(retrieval, title, body, **kw):
    text = create(title, body, sources=[dict(type='manual', description='虚构，无外部原文')], **kw)
    mid = parse(text)[0]['id']
    retrieval.store.add(text)
    return mid


def test_explicit_modes_read_and_status(service):
    r, client = service
    old = add(r, '三月安排', '晚上学习', valid_from='2026-03')
    new = add(r, '四月安排', '早上学习', valid_from='2026-04')
    r.store.relate(new, old, 'supersedes', '安排改变')
    for mode in ['keyword', 'semantic', 'hybrid']:
        hits = client.get('/api/notes/search', params=dict(q='早上', mode=mode)).json()
        assert hits[0]['id'] == new
        assert set(hits[0]) == {'id', 'title', 'status', 'score'}
    row = client.get('/api/notes/' + old).json()
    assert row['markdown'] == r.store.read(old)['content']
    assert row['relations'][0]['target'] == new
    assert row['status']['successors'][0]['valid_from'] == '2026-04'
    assert row['status']['valid_from'] == '2026-03'
    assert row['sources'][0]['excerpt_reverified'] is False
    assert client.get('/api/notes/search', params=dict(q='test')).status_code == 422
    for params in [dict(q='x', mode='auto'), dict(q='', mode='keyword'), dict(q='x', mode='keyword', limit=21)]:
        assert client.get('/api/notes/search', params=params).status_code == 409
    client.cookies.clear()
    assert client.get('/api/notes/' + old).status_code == 401
    assert client.get('/api/notes/search?q=x&mode=keyword').status_code == 401


def test_cache_invalidation_rename_delete_quarantine(service):
    r, _ = service
    mid = add(r, '安排', '早上学习')
    assert r.search('早上', 'semantic')[0]['id'] == mid
    calls = r.embedder.calls
    row = r.store.read(mid)
    path = r.store.root / row['path']
    renamed = path.with_name('改名.md')
    path.rename(renamed)
    r.search('早上', 'semantic')
    assert calls == r.embedder.calls
    renamed.write_text(row['content'] + '\n手工增补', encoding='utf-8')
    r.search('早上', 'semantic')
    assert r.embedder.calls == calls + 1
    r.embedder.version = 'test-v2'
    r.search('早上', 'semantic')
    assert r.embedder.calls == calls + 2
    # Invalid graph cannot leak from a previously cached vector.
    other = add(r, '其他', '晚上学习')
    r.store.relate(mid, other, 'related_to', '共同主题')
    second = r.store.read(other)
    (r.store.root / second['path']).unlink()
    assert r.search('早上', 'hybrid') == []
    assert r.read(mid)['status']['available'] is False


def test_keyword_never_loads_model_and_failure_is_explicit(service):
    r, _ = service
    add(r, '数学', '特征向量方向')
    def fail():
        raise ValueError('模型不可用')
    r.embedder.load = fail
    assert r.search('特征向量', 'keyword')
    assert not r.search('足球', 'keyword')
    for mode in ['semantic', 'hybrid']:
        with pytest.raises(ValueError, match='模型不可用'):
            r.search('特征向量', mode)


def test_long_body_last_chunk_is_encoded(service):
    r, _ = service
    mid = add(r, '长记录', '晚上'*400 + '早上学习')
    assert r.search('早上', 'semantic')[0]['id'] == mid
    assert r.embedder.calls > 1
