"""Storage fault injection with fictional records; not a real-model acceptance test."""
import pytest

from hikari.core import Config, DB, digest
from hikari.library import Library
from hikari.memory import Memory
from hikari.note_format import create, filename, parse, render
from hikari.notes import NoteStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    cfg = Config(tmp_path)
    db = DB(tmp_path / 'app.sqlite3')
    return NoteStore(cfg, db, Library(cfg, db))


def note(title='虚构决定', **kw):
    return create(title, '独立结论：' + title + '\n', sources=[{'type': 'manual', 'description': '虚构验收，无外部原文'}], **kw)


def add(store, title='虚构决定', **kw):
    text = note(title, **kw)
    meta, _ = parse(text)
    store.add(text)
    return meta['id']


def pair(store):
    first, second = add(store, '旧决定', valid_from='2026-03'), add(store, '新决定', valid_from='2026-04')
    changes, expected = {}, {}
    for mid, kind, target in [(first, 'superseded_by', second), (second, 'supersedes', first)]:
        row = store.read(mid)
        row['meta']['relations'].append(dict(type=kind, target=target, reason='虚构偏好在四月改变'))
        changes[row['path']], expected[row['path']] = render(row['meta'], row['body']), row['hash']
    return first, second, changes, expected


def test_uuid_rename_rebuild_and_legacy_exclusion(store):
    mid = add(store)
    row = store.read(mid)
    (store.root / row['path']).rename(store.root / '手动改名.md')
    store.db.execute('DELETE FROM memory_note_index')
    assert store.read(mid)['path'] == '手动改名.md'
    assert len(store.db.rows('SELECT * FROM memory_operations')) == 1
    legacy = Memory(store.cfg, store.db, store.library)
    assert legacy.list() == [] and legacy.context('虚构') == []
    assert not store.db.rows('SELECT * FROM memories')
    with pytest.raises(ValueError):
        legacy.write('notes/手动改名.md', row['content'], 'user', row['hash'])
    with pytest.raises(AttributeError):
        store.undo('anything')


def test_evolution_correction_fork_and_review(store):
    old, new, changes, expected = pair(store)
    store.apply(changes, expected)
    corrected = add(store, '理由纠错', valid_from='2026-03')
    derived = add(store, '偏好归纳', nature='inference')
    store.relate(derived, old, 'derived_from', '从三月决定归纳')
    store.relate(corrected, old, 'corrects', '原理由记错，并非四月才改变')
    branch = add(store, '另一新决定', valid_from='2026-04')
    store.relate(branch, old, 'supersedes', '竞争结论，留待复核')
    row = store.read(old)
    assert len(row['summary']) == 3
    assert {e['type'] for e in row['summary']} == {'superseded_by', 'corrected_by'}
    assert [e['valid_from'] for e in row['summary'] if e['target'] == new] == ['2026-04']
    assert 'valid_until' not in row['meta']
    assert store.read(derived)['review_hints']
    assert store.read(derived)['available']
    store.db.execute('DELETE FROM memory_note_index')
    assert store.read(old)['meta']['relations'] == row['meta']['relations']


@pytest.mark.parametrize('kind', ['supersedes', 'corrects'])
def test_invalid_program_graph_never_persisted(store, kind):
    a, b = add(store, 'A'), add(store, 'B')
    count = len(store.db.rows('SELECT * FROM memory_operations'))
    with pytest.raises(ValueError):
        store.relate(a, a, kind, '自指')
    assert len(store.db.rows('SELECT * FROM memory_operations')) == count
    store.relate(a, b, kind, '先后关系')
    snapshots = {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    with pytest.raises(ValueError):
        store.relate(b, a, kind, '循环')
    assert snapshots == {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    store.relate(a, b, 'related_to', '同一虚构主题的两种决定')
    assert all(r['available'] for r in store.scan())


@pytest.mark.parametrize('stage,index', [('validated', None), ('persisted', None), ('checked', None),
    ('replaced', 0), ('replaced', 1), ('verified', None), ('completed', None)])
def test_every_interruption_recovers_idempotently(store, monkeypatch, stage, index):
    a, b, changes, expected = pair(store)
    def crash(actual, oid, i=None):
        if actual == stage and i == index:
            raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(store, '_checkpoint', crash)
    with pytest.raises(RuntimeError):
        store.apply(changes, expected)
    other = NoteStore(store.cfg, store.db, store.library)
    other.recover()
    other.recover()
    rows = [other.read(mid) for mid in [a, b]]
    if stage == 'validated':
        assert all(not r['meta']['relations'] for r in rows)
    else:
        assert all(r['available'] and len(r['meta']['relations']) == 1 for r in rows)
    assert not store.db.rows("SELECT id FROM memory_operations WHERE state='pending'")


def test_third_content_conflict_sticky_restart_explicit_repair(store, monkeypatch):
    a, b, changes, expected = pair(store)
    def edit(stage, oid, index=None):
        if stage == 'replaced' and index == 0:
            path = store.root / next(iter(changes))
            path.write_text(path.read_text('utf-8') + '\n用户手工补充\n', encoding='utf-8')
    monkeypatch.setattr(store, '_checkpoint', edit)
    with pytest.raises(ValueError, match='整组停止'):
        store.apply(changes, expected)
    op = store.db.one("SELECT * FROM memory_operations WHERE state='conflict'")
    assert op
    other = NoteStore(store.cfg, store.db, store.library)
    files = {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    other.recover()
    assert files == {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    assert not other.read(a)['available'] and not other.read(b)['available']
    with pytest.raises(ValueError, match='不可提供'):
        other.read(a, for_ai=True)
    assert add(other, '无关记录')
    rows = [other.read(mid) for mid in [a, b]]
    fixed, hashes = {}, {}
    for row in rows:
        # Explicitly base the fresh plan on current content and preserve manual additions.
        row['meta']['relations'] = []
        fixed[row['path']], hashes[row['path']] = render(row['meta'], row['body']), row['hash']
    with pytest.raises(ValueError, match='覆盖原整组'):
        other.apply({rows[0]['path']: fixed[rows[0]['path']]}, {rows[0]['path']: rows[0]['hash']}, repairs=op['id'])
    repaired = other.apply(fixed, hashes, repairs=op['id'])
    assert other.db.one('SELECT state,repaired_by FROM memory_operations WHERE id=?', (op['id'],)) == {
        'state': 'repaired', 'repaired_by': repaired}
    assert all(other.read(mid)['available'] for mid in [a, b])
    assert '用户手工补充' in other.read(a)['body']


def test_invalid_external_files_local_isolation(store):
    a, b, changes, expected = pair(store)
    store.apply(changes, expected)
    unrelated = add(store, '无关结论')
    original = store.read(b)
    (store.root / '重复身份.md').write_text(original['content'], encoding='utf-8')
    (store.root / '坏格式.md').write_text('---\nid: [\n---\n坏 YAML', encoding='utf-8')
    rows = store.scan()
    assert sum('重复 ID' in str(r['issues']) for r in rows) == 2
    assert not store.read(a)['available']
    assert store.read(unrelated)['available']
    assert any('YAML' in str(r['issues']) for r in rows)
    (store.root / '重复身份.md').unlink()
    (store.root / original['path']).unlink()
    assert not store.read(a)['available']
    assert store.read(a)['meta']['relations'][0]['target'] == b
    assert store.read(unrelated)['available']


def test_external_cycles_retained_and_bad_record_not_legacy_imported(store):
    a, b = add(store, 'A'), add(store, 'B')
    for mid, other in [(a, b), (b, a)]:
        row = store.read(mid)
        row['meta']['relations'] = [dict(type='supersedes', target=other, reason='手改循环'),
                                   dict(type='superseded_by', target=other, reason='手改循环')]
        (store.root / row['path']).write_text(render(row['meta'], row['body']), encoding='utf-8')
    assert all(not row['available'] for row in store.scan())
    raw = {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    store.scan()
    assert raw == {p.name: p.read_bytes() for p in store.root.glob('*.md')}
    legacy = Memory(store.cfg, store.db, store.library)
    assert legacy.list() == []


def test_compare_before_intent_and_secret_rejection(store):
    mid = add(store)
    row = store.read(mid)
    path = store.root / row['path']
    path.write_text(row['content'] + '手改', encoding='utf-8')
    count = len(store.db.rows('SELECT * FROM memory_operations'))
    with pytest.raises(ValueError, match='修改'):
        store.apply({row['path']: row['content']}, {row['path']: row['hash']})
    assert len(store.db.rows('SELECT * FROM memory_operations')) == count
    # Freshly reading the manual edit permits a future edit; no permanent protection bit.
    now = store.read(mid)
    store.apply({now['path']: now['content'] + '\n新补充'}, {now['path']: now['hash']})
    store.cfg.values['api_key'] = 'synthetic-secret'
    with pytest.raises(ValueError, match='密钥'):
        store.add(note('synthetic-secret'))


def test_saved_excerpt_survives_missing_source_and_chat(store):
    text = create('虚构来源', '仅保存历史摘录', nature='source_statement', sources=[
        {'type': 'document', 'doc': 'missing-doc', 'version': 'read-version', 'position': '第 3 段', 'excerpt': '合成原文'},
        {'type': 'chat', 'conversation_id': 'deleted-chat', 'message_id': 'deleted-message', 'excerpt': '合成用户原话'}])
    mid = parse(text)[0]['id']
    store.add(text)
    row = store.read(mid)
    assert row['available']
    assert row['sources'][0]['current_document_version'] is False
    assert all(s['excerpt_state'] == 'historical_saved' and not s['excerpt_reverified'] for s in row['sources'])
    assert row['sources'][1]['excerpt'] == '合成用户原话'


def test_unrelated_bad_file_does_not_block_valid_proposal(store):
    store.root.mkdir(parents=True)
    (store.root / '缺必要字段.md').write_text('---\ntitle: x\n---\n内容', encoding='utf-8')
    (store.root / '坏编码.md').write_bytes(b'\xff')
    mid = add(store, '有效记忆')
    assert store.read(mid, for_ai=True)['available']
    assert len(store.scan()) == 3


def test_prewrite_external_edit_stops_group_and_pending_is_unavailable(store, monkeypatch):
    a, b, changes, expected = pair(store)
    def edit(stage, oid, index=None):
        if stage == 'checked':
            assert not store.read(a)['available']
            path = store.root / next(iter(changes))
            path.write_text(path.read_text('utf-8') + '\n手改优先', encoding='utf-8')
    monkeypatch.setattr(store, '_checkpoint', edit)
    with pytest.raises(ValueError, match='整组停止'):
        store.apply(changes, expected)
    assert not store.read(b)['meta']['relations']
    assert '手改优先' in store.read(a)['body']


def test_group_rename_delete_and_identity_conflict_rejected(store):
    mid = add(store)
    row = store.read(mid)
    store.apply({row['path']: None, '新名称.md': row['content']}, {row['path']: row['hash'], '新名称.md': None})
    assert store.read(mid)['path'] == '新名称.md'
    other = add(store, '关联')
    store.relate(other, mid, 'related_to', '同一主题')
    now = store.read(mid)
    with pytest.raises(ValueError):
        store.apply({'新名称.md': None}, {'新名称.md': now['hash']})
    assert store.read(mid)['available']
    with pytest.raises(ValueError):
        store.apply({'另一份.md': now['content']}, {'另一份.md': None})
    with pytest.raises(ValueError, match='大小写'):
        store.apply({'NAME.md': note('A'), 'name.md': note('B')}, {'NAME.md': None, 'name.md': None})


def test_interrupted_explicit_repair_resumes_and_links_history(store, monkeypatch):
    a, b, changes, expected = pair(store)
    def stop(stage, oid, index=None):
        if stage == 'persisted':
            raise RuntimeError('stop')
    monkeypatch.setattr(store, '_checkpoint', stop)
    with pytest.raises(RuntimeError):
        store.apply(changes, expected)
    path = store.root / next(iter(changes))
    path.write_text(path.read_text('utf-8') + '\n手动修改', encoding='utf-8')
    other = NoteStore(store.cfg, store.db, store.library)
    other.recover()
    old = other.db.one("SELECT id FROM memory_operations WHERE state='conflict'")['id']
    rows = [other.read(mid) for mid in [a, b]]
    monkeypatch.setattr(other, '_checkpoint', stop)
    with pytest.raises(RuntimeError):
        other.apply({r['path']: r['content'] for r in rows}, {r['path']: r['hash'] for r in rows}, repairs=old)
    resumed = NoteStore(store.cfg, store.db, store.library)
    resumed.recover()
    resumed.recover()
    assert all(resumed.read(mid)['available'] for mid in [a, b])
    history = resumed.operation(old)
    assert history['state'] == 'repaired' and history['repaired_by']
    assert len(history['files']) == 2 and isinstance(history['files'][0]['before'], bytes)


def test_legacy_pending_revision_and_undo_cannot_touch_new_notes(store):
    mid = add(store)
    row = store.read(mid)
    legacy = Memory(store.cfg, store.db, store.library)
    store.db.execute('INSERT INTO memories(id,path,hash,updated) VALUES(?,?,?,?)',
                     ('legacy-id', 'notes/' + row['path'], row['hash'], 0))
    store.db.execute('INSERT INTO revisions(id,memory,before_text,after_text,before_exists,actor,state,created) VALUES(?,?,?,?,?,?,?,?)',
                     ('legacy-rev', 'legacy-id', row['content'], 'old-format-overwrite', 1, 'agent', 'pending', 0))
    legacy.reconcile()
    assert store.read(mid)['content'] == row['content']
    assert store.db.one('SELECT state FROM revisions')['state'] == 'conflict'
    store.db.execute("UPDATE revisions SET state='applied'")
    with pytest.raises(ValueError, match='不支持旧版撤销'):
        legacy.undo('legacy-rev')
    assert store.read(mid)['content'] == row['content']


def test_mixed_evolution_cycle_rejected_and_missing_inverse_isolated(store):
    a, b = add(store, 'A'), add(store, 'B')
    store.relate(a, b, 'supersedes', '改变')
    with pytest.raises(ValueError, match='循环'):
        store.relate(b, a, 'corrects', '错误的混合循环')
    row = store.read(b)
    row['meta']['relations'] = []
    (store.root / row['path']).write_text(render(row['meta'], row['body']), encoding='utf-8')
    assert not store.read(a)['available'] and not store.read(b)['available']


def test_failed_repair_can_be_retried_without_leaving_parent_quarantined(store, monkeypatch):
    a, b, changes, expected = pair(store)
    def edit(stage, oid, index=None):
        if stage == 'persisted':
            path = store.root / next(iter(changes))
            path.write_bytes(path.read_bytes() + b'\nmanual edit\n')
    monkeypatch.setattr(store, '_checkpoint', edit)
    with pytest.raises(ValueError, match='整组停止'):
        store.apply(changes, expected)
    root = store.db.one("SELECT id FROM memory_operations WHERE state='conflict'")['id']
    rows = [store.read(mid) for mid in [a, b]]
    with pytest.raises(ValueError, match='整组停止'):
        store.apply({r['path']: r['content'] for r in rows}, {r['path']: r['hash'] for r in rows}, repairs=root)
    assert len(store.db.rows("SELECT id FROM memory_operations WHERE state='conflict'")) == 2
    other = NoteStore(store.cfg, store.db, store.library)
    rows = [other.read(mid) for mid in [a, b]]
    resolved = other.apply({r['path']: r['content'] for r in rows}, {r['path']: r['hash'] for r in rows}, repairs=root)
    assert not other.db.rows("SELECT id FROM memory_operations WHERE state IN ('pending','conflict')")
    assert all(r['repaired_by'] == resolved for r in other.db.rows("SELECT repaired_by FROM memory_operations WHERE state='repaired'"))
    assert all(other.read(mid)['available'] for mid in [a, b])
