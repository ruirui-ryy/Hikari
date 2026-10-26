import json
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from hikari.app import create_app
from hikari.core import Config, DB, InstanceLock, digest, password_hash
from hikari.library import Library, extract
from hikari.memory import Memory


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    app = create_app(tmp_path, background=False)
    app.state.cfg.save({'username': 'owner', 'password': password_hash('test-password-123')})
    with TestClient(app) as client:
        client.headers['X-Hikari'] = '1'
        assert client.post('/api/login', json={'username': 'owner', 'password': 'test-password-123'}).status_code == 200
        yield app, client


def seed(app, name='数学/讲义.md', content='# 特征向量\n\n矩阵的特征向量在变换后保持方向不变。\n\n特征值描述沿该方向的缩放。'):
    p = app.state.library.root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')
    app.state.library.scan()
    return p, app.state.library.catalog()[0]


def chat(client, text='请解释特征向量', request_id='unique-request-123'):
    cid = client.post('/api/chats', json={'title': '新对话'}).json()['id']
    tid = client.post(f'/api/chats/{cid}/messages', json={'content': text, 'request_id': request_id}).json()['id']
    return cid, tid


def test_auth_csrf_and_secret(service):
    app, client = service
    assert client.get('/api/settings').json()['has_api_key'] is False
    assert 'password' not in client.get('/api/settings').json()
    assert client.get('/static/app.js').headers['content-type'].startswith('application/javascript')
    assert client.post('/api/chats', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    client.headers.pop('X-Hikari')
    assert client.post('/api/chats', json={}).status_code == 403
    client.cookies.clear()
    for route in ['/api/documents', '/api/memories', '/api/chats', '/api/sources/missing', '/api/generated', '/api/backups/missing.zip']:
        assert client.get(route).status_code == 401


def test_all_formats(tmp_path):
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches
    from pypdf import PdfWriter
    md = tmp_path / 'a.md'
    md.write_text('标题\n\n内容一\n\n内容二', encoding='utf-8')
    assert len(extract(md)) == 3
    doc = Document()
    doc.add_paragraph('课程第一段')
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '表格内容'
    doc.add_paragraph('课程第二段')
    doc.save(tmp_path / 'a.docx')
    units = extract(tmp_path / 'a.docx')
    assert '表格内容' in units[1][2] and units[2][2] == '课程第二段'
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(2)).text = '矩阵与向量'
    deck.save(tmp_path / 'a.pptx')
    assert extract(tmp_path / 'a.pptx')[0][2] == '矩阵与向量'
    pdf = PdfWriter()
    pdf.add_blank_page(width=300, height=300)
    pdf.write(tmp_path / 'a.pdf')
    assert extract(tmp_path / 'a.pdf') == [(1, '第 1 页', '')]


def test_index_changes_scope_and_old_evidence(service):
    app, client = service
    path, doc = seed(app)
    lib = app.state.library
    assert lib.search('特征向量', '数学')
    assert lib.search('特征向量', '物理') == []
    hit = next(h for h in lib.search('特征向量') if '保持方向' in h['text'])
    evidence = lib.read('task1', doc['id'], hit['position'])[0]
    assert client.get('/api/sources/' + evidence['id']).json()['current']
    path.write_text('新版本完全替换为积分课程', encoding='utf-8')
    assert not client.get('/api/sources/' + evidence['id']).json()['current']
    assert client.get('/api/documents/' + doc['id'] + '/file?version=' + evidence['version']).status_code == 409
    lib.scan()
    assert not lib.search('特征向量')
    assert lib.search('积分')
    path.unlink()
    lib.scan()
    assert not lib.search('积分')
    assert lib.catalog()[0]['status'] == 'deleted'
    assert '保持方向' in client.get('/api/sources/' + evidence['id']).json()['text']


def test_upload_no_overwrite_or_traversal(service):
    app, client = service
    r = client.post('/api/documents/upload', files={'file': ('x.md', b'hello')}, data={'folder': 'course'})
    assert r.status_code == 200
    assert client.post('/api/documents/upload', files={'file': ('x.md', b'other')}, data={'folder': 'course'}).status_code == 409
    assert (app.state.library.root / 'course/x.md').read_bytes() == b'hello'
    assert client.post('/api/documents/upload', files={'file': ('x.md', b'x')}, data={'folder': '../../escape'}).status_code == 409
    assert client.post('/api/documents/upload', files={'file': ('../x.md', b'x')}).status_code == 409


def test_memory_user_priority_stale_and_undo(service):
    app, client = service
    path, doc = seed(app)
    mem = app.state.memory
    source = {'doc': doc['id'], 'version': doc['version']}
    r = mem.write('understanding/向量.md', '有依据的理解', 'agent', sources=[source])
    assert not mem.list()[0]['stale']
    path.write_text('已变更', encoding='utf-8')
    assert mem.list()[0]['stale']
    mem.write('understanding/向量.md', '用户纠正', 'user', r['hash'])
    with pytest.raises(ValueError, match='用户编辑'):
        mem.write('understanding/向量.md', 'AI覆盖', 'agent', digest('用户纠正'), [source])
    with pytest.raises(ValueError, match='后续修订'):
        mem.undo(r['revision'])
    file = mem.root / 'profile/手写.md'
    file.parent.mkdir(exist_ok=True)
    file.write_text('手写内容', encoding='utf-8')
    assert any(m['path'] == 'profile/手写.md' and m['protected'] for m in mem.list())
    r = mem.write('learning/练习.md', '第一版', 'agent')
    r2 = mem.write('learning/练习.md', '第二版', 'agent', r['hash'])
    mem.undo(r2['revision'])
    assert (mem.root / 'learning/练习.md').read_text('utf-8') == '第一版'
    assert next(m for m in mem.list() if m['path'] == 'learning/练习.md')['protected']


def test_external_memory_edit_and_delete_protection(service):
    app, _ = service
    mem = app.state.memory
    r = mem.write('profile/偏好.md', '先举例', 'agent')
    path = mem.root / 'profile/偏好.md'
    path.write_text('先推导', encoding='utf-8')
    with pytest.raises(ValueError):
        mem.write('profile/偏好.md', '覆盖', 'agent', digest('先推导'))
    assert mem.list()[0]['protected']
    path.unlink()
    with pytest.raises(ValueError):
        mem.write('profile/偏好.md', '恢复', 'agent')


def test_journal_recovery(service):
    app, _ = service
    mem, db = app.state.memory, app.state.db
    r = mem.write('profile/恢复.md', '新的内容', 'user')
    db.execute("UPDATE revisions SET state='pending' WHERE id=?", (r['revision'],))
    db.execute('UPDATE memories SET protected=0,hash=? WHERE id=?', ('old', r['id']))
    mem.reconcile()
    m = mem.list()[0]
    assert m['protected'] and m['hash'] == digest('新的内容')


def test_queue_dedup_recovery_and_cancellation(service):
    app, client = service
    cid, tid = chat(client)
    assert client.post(f'/api/chats/{cid}/messages', json={'content': '请解释特征向量', 'request_id': 'unique-request-123'}).json()['id'] == tid
    assert len(client.get('/api/chats/' + cid).json()['messages']) == 1
    assert client.delete('/api/chats/' + cid).status_code == 409
    app.state.db.execute("UPDATE tasks SET status='reading' WHERE id=?", (tid,))
    app.state.agent.recover()
    assert app.state.db.one('SELECT status FROM tasks WHERE id=?', (tid,))['status'] == 'interrupted'
    new = client.post('/api/tasks/' + tid + '/retry').json()['id']
    assert new != tid
    assert client.post('/api/tasks/' + new + '/cancel').status_code == 200
    assert client.delete('/api/chats/' + cid).status_code == 200


def test_agent_tool_loop_sources_and_memory(service):
    app, client = service
    seed(app)
    cid, tid = chat(client)
    seen = []
    def call(messages, tools, timeout):
        seen.append(messages[-1])
        n = len(seen)
        if n == 1:
            name, args = 'search', {'query': '特征向量'}
        elif n == 2:
            hit = json.loads(messages[-1]['content'])[0]
            name, args = 'read', {'doc': hit['doc'], 'position': hit['position']}
        elif n == 3:
            ev = json.loads(messages[-1]['content'])[0]
            name, args = 'remember', {'path': 'understanding/向量.md', 'content': '事实：方向保持。依据：' + ev['citation'], 'evidence_ids': [ev['id']]}
        else:
            ev = app.state.db.one('SELECT id FROM evidence WHERE task=?', (tid,))
            return {'role': 'assistant', 'content': '变换后方向保持。[[source:' + ev['id'] + ']]'}
        return {'role': 'assistant', 'content': None, 'reasoning_content': 'internal-only',
                'tool_calls': [{'id': 'call' + str(n), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
    app.state.agent.model = call
    app.state.agent.run(app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,)))
    result = client.get('/api/chats/' + cid).json()
    assert result['tasks'][0]['status'] == 'completed'
    answer = result['messages'][-1]['content']
    assert '/api/sources/' in answer and 'internal-only' not in json.dumps(result)
    assert app.state.memory.list()[0]['path'] == 'understanding/向量.md'
    assert 'internal-only' not in app.state.db.path.read_bytes().decode('utf-8', errors='ignore')


def test_agent_permissions_and_bad_source(service):
    app, client = service
    cid, tid = chat(client)
    agent = app.state.agent
    with pytest.raises(ValueError):
        agent.dispatch(tid, '', 'shell', {'command': 'anything'})
    with pytest.raises(ValueError):
        agent.dispatch(tid, '', 'read', {'doc': 'x', 'position': -1})
    with pytest.raises(ValueError, match='核验'):
        agent.finalize('伪造[[source:fake]]', tid)


def test_queue_context_includes_previous_completed_answer(service):
    app, client = service
    cid, first = chat(client, '第一个问题')
    second = client.post(f'/api/chats/{cid}/messages', json={'content': '接着解释', 'request_id': 'second-unique-request'}).json()['id']
    responses = []
    def model(messages, tools, timeout):
        responses.append(messages)
        return {'role': 'assistant', 'content': '第一次回答' if len(responses) == 1 else '第二次回答'}
    app.state.agent.model = model
    for tid in [first, second]:
        app.state.agent.run(app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,)))
    assert '第一次回答' in responses[1][0]['content']
    assert '接着解释' not in responses[0][0]['content']


def test_backup_snapshot_and_sessions(service):
    app, client = service
    app.state.memory.write('profile/偏好.md', '记忆', 'agent')
    r = client.post('/api/backup')
    assert r.status_code == 200
    filename = r.json()['filename']
    with zipfile.ZipFile(app.state.cfg.home / 'backups' / filename) as z:
        assert 'app.sqlite3' in z.namelist() and 'memory/profile/偏好.md' in z.namelist()
        assert not any('config.json' in n for n in z.namelist())
        restored = app.state.cfg.home / 'restored'
        z.extractall(restored)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(restored / 'app.sqlite3')) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM memories').fetchone()[0] == 1
    assert client.get('/api/backups/' + filename).status_code == 200


def test_instance_exclusion(tmp_path):
    lock = InstanceLock(tmp_path / 'instance.lock')
    with pytest.raises(RuntimeError):
        InstanceLock(tmp_path / 'instance.lock')
    lock.close()


def test_long_unit_can_read_search_hit(service):
    app, _ = service
    _, doc = seed(app, content='普通文字。' * 6000 + '终端特殊术语：青叶变换。')
    hit = app.state.library.search('青叶变换')[0]
    result = app.state.library.read('long-task', doc['id'], hit['position'], chunk_id=hit['id'])
    assert any('青叶变换' in e['text'] for e in result)


def test_single_worker_queue_and_reconnect(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    entered, release = threading.Event(), threading.Event()
    count = []
    def model(messages, tools, timeout):
        count.append(messages[-1]['content'])
        if len(count) == 1:
            entered.set()
            assert release.wait(5)
        return {'role': 'assistant', 'content': '已回答：' + messages[-1]['content']}
    app = create_app(tmp_path, model=model)
    app.state.cfg.save({'username': 'owner', 'password': password_hash('test-password-123')})
    with TestClient(app) as c:
        c.headers['X-Hikari'] = '1'
        c.post('/api/login', json={'username': 'owner', 'password': 'test-password-123'})
        cid, first = chat(c, 'first')
        try:
            assert entered.wait(3)
            second = c.post(f'/api/chats/{cid}/messages', json={'content': 'second', 'request_id': 'second-distinct-id'}).json()['id']
            assert len(count) == 1
            assert c.get('/api/status').json()['queued'] == 1
        finally:
            release.set()
        other = TestClient(app)
        other.cookies.update(c.cookies)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                data = other.get('/api/chats/' + cid).json()
                if all(t['status'] == 'completed' for t in data['tasks']):
                    break
                time.sleep(.05)
            assert [t['status'] for t in data['tasks']] == ['completed', 'completed']
            assert count == ['first', 'second']
            assert len([m for m in data['messages'] if m['role'] == 'assistant']) == 2
        finally:
            other.close()


def test_model_credentials_are_not_in_context(service, monkeypatch):
    import httpx
    app, _ = service
    key = 'synthetic-private-api-key'
    app.state.cfg.save({'api_key': key})
    actual_client = httpx.Client
    def handle(request):
        assert request.headers['authorization'] == 'Bearer ' + key
        assert key not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}]})
    monkeypatch.setattr(httpx, 'Client', lambda **options: actual_client(transport=httpx.MockTransport(handle), trust_env=False))
    result = app.state.agent.call_model([{'role': 'user', 'content': key}], [], 10)
    assert result['content'] == 'ok'


def test_scope_filter_is_applied_before_result_limit(service):
    app, _ = service
    seed(app, '课程01/共同术语.md', '\n\n'.join(['共同术语与特征向量'] * 350))
    other = app.state.library.root / '课程10/讲义.md'
    other.parent.mkdir(parents=True)
    other.write_text('共同术语与特征向量', encoding='utf-8')
    app.state.library.scan()
    hits = app.state.library.search('共同术语与特征向量', '课程10')
    assert hits and all(h['path'] == '课程10/讲义.md' for h in hits)
