"""Deterministic contract tests, not a real-model memory quality evaluation."""
import json
import threading
import time

import pytest

from hikari.app import create_app
from hikari.core import uid


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    return create_app(tmp_path, background=False)


def turn(app, prompt, chat='chat', status='completed', age=180):
    tid, now = uid(), time.time() - age
    db = app.state.db
    db.execute('INSERT INTO tasks(id,chat,prompt,status,request_id,created,updated,memory_status) VALUES(?,?,?,?,?,?,?,?)',
               (tid, chat, prompt, status, uid(), now, now, 'pending' if status == 'completed' else ''))
    db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), chat, 'user', prompt, tid, now))
    if status == 'completed':
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), chat, 'assistant', '示例回答', tid, now + .01))
    return tid


def update(text='偏好先看例子。', quote='我喜欢先看例子', key='表达偏好', kind='用户事实', path='profile/自动记忆.md'):
    return dict(path=path, key=key, kind=kind, text=text, quote=quote)


def reviewer(app, updates):
    calls = []
    def model(messages, tools, timeout):
        assert tools == [] and 0 < timeout <= 45
        calls.append(json.loads(messages[-1]['content']))
        return {'role': 'assistant', 'content': json.dumps({'updates': updates}, ensure_ascii=False)}
    app.state.agent.model = model
    return calls


def test_idle_batch_does_not_require_remember_and_noop(app):
    first = turn(app, '我喜欢先看例子')
    second = turn(app, '我是数学专业学生', age=170)
    calls = reviewer(app, [update(), update('数学专业学生。', '我是数学专业学生', '专业')])
    assert app.state.agent.review_idle()
    assert len(calls) == 1 and len(calls[0]['turns']) == 2
    mem = app.state.memory
    content = mem.list()[0]['content']
    assert first in content and second in content
    assert '用户事实' in content
    assert not app.state.agent.review_idle()
    previous = mem.list()[0]
    revisions = app.state.db.rows('SELECT id FROM revisions')
    turn(app, '我喜欢先看例子')
    assert app.state.agent.review_idle()
    assert mem.list()[0]['content'] == previous['content']
    assert app.state.db.rows('SELECT id FROM revisions') == revisions


def test_correction_merge_feedback_inference_and_undo(app):
    mem = app.state.memory
    mem.apply_personal([update(), update('准备考研。', '准备考研', '目标')], [], '我喜欢先看例子，准备考研', 'old')
    before = mem.list()[0]['content']
    tid = turn(app, '改了，我喜欢先推导；这题我还是不会，可能是基础不牢')
    reviewer(app, [update('偏好先推导。', '我喜欢先推导'),
                   update('用户表示这题还不会。', '这题我还是不会', '本题困难', '用户反馈', 'learning/自动记忆.md'),
                   update('可能基础不牢，尚待确认。', '可能是基础不牢', '基础情况', '助手推测', 'learning/自动记忆.md')])
    assert app.state.agent.review_idle()
    content = (mem.root / 'profile/自动记忆.md').read_text('utf-8')
    assert content.count('[表达偏好]') == 1 and '先看例子' not in content and '[目标]' in content
    learning = (mem.root / 'learning/自动记忆.md').read_text('utf-8')
    assert '用户反馈' in learning and '助手推测' in learning and tid in learning
    rev = app.state.db.one("SELECT id FROM revisions WHERE after_text=?", (content,))
    mem.undo(rev['id'])
    assert (mem.root / 'profile/自动记忆.md').read_text('utf-8') == before
    assert next(m for m in mem.list() if m['path'].startswith('profile'))['protected']


@pytest.mark.parametrize('mode', ['external', 'user', 'undo', 'delete'])
def test_protected_automatic_file_never_overwritten(app, mode):
    mem = app.state.memory
    mem.apply_personal([update()], [], '我喜欢先看例子', 'old')
    old = mem.list()[0]
    if mode == 'external':
        (mem.root / old['path']).write_text('手工整理', encoding='utf-8')
    elif mode == 'user':
        mem.write(old['path'], '网页编辑', 'user', old['hash'])
    elif mode == 'delete':
        mem.delete(old['id'], old['hash'])
    else:
        mem.undo(app.state.db.one('SELECT id FROM revisions')['id'])
    before = (mem.root / old['path']).read_text('utf-8')
    tid = turn(app, '我喜欢先推导')
    reviewer(app, [update('偏好先推导。', '我喜欢先推导')])
    assert app.state.agent.review_idle()
    assert (mem.root / old['path']).read_text('utf-8') == before
    assert app.state.db.one('SELECT memory_status,status FROM tasks WHERE id=?', (tid,)) == {'memory_status': 'failed', 'status': 'completed'}


def test_snapshot_race_and_invalid_batch_are_rejected_before_write(app):
    mem = app.state.memory
    mem.apply_personal([update()], [], '我喜欢先看例子', 'old')
    snapshot = mem.list()
    mem.write(snapshot[0]['path'], '并发修改', 'user', snapshot[0]['hash'])
    with pytest.raises(ValueError):
        mem.apply_personal([update('偏好先推导。', '我喜欢先推导')], snapshot, '我喜欢先推导', 'new')
    assert mem.list()[0]['content'] == '并发修改'
    with pytest.raises(ValueError):
        mem.apply_personal([update(path='learning/自动记忆.md'), update(quote='伪造用户原话')], mem.list(), '我喜欢先看例子', 'new')
    assert not (mem.root / 'learning/自动记忆.md').exists()


def test_queue_and_recent_activity_delay_review(app):
    turn(app, '我喜欢先看例子')
    new = turn(app, '刚说的话', age=0)
    calls = reviewer(app, [])
    assert not app.state.agent.review_idle()
    app.state.db.execute('UPDATE tasks SET updated=? WHERE id=?', (time.time() - 180, new))
    queued = turn(app, '其他聊天排队', chat='other', status='queued')
    assert not app.state.agent.review_idle()
    app.state.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (queued,))
    assert app.state.agent.review_idle() and len(calls) == 1


@pytest.mark.parametrize('result', ['not json', '{"updates":{}}', '{"updates":[{"bad":true}]}'])
def test_bad_review_does_not_lose_answer_or_repeat(app, result):
    tid = turn(app, '普通问题')
    app.state.agent.model = lambda *args: {'content': result}
    assert app.state.agent.review_idle()
    row = app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert row['status'] == 'completed' and row['memory_status'] == 'failed'
    assert app.state.db.one("SELECT content FROM messages WHERE task=? AND role='assistant'", (tid,))['content'] == '示例回答'
    assert app.state.memory.list() == []
    assert not app.state.agent.review_idle()


def test_recovery_does_not_replay_uncertain_review(app):
    tid = turn(app, '我喜欢先看例子')
    app.state.db.execute("UPDATE tasks SET memory_status='reviewing' WHERE id=?", (tid,))
    app.state.agent.recover()
    assert app.state.db.one('SELECT memory_status,status FROM tasks WHERE id=?', (tid,)) == {'memory_status': 'interrupted', 'status': 'completed'}
    assert not app.state.agent.review_idle()


def test_answer_marks_pending_and_next_chat_receives_relevant_memory(app):
    mem = app.state.memory
    for i in range(35):
        mem.write(f'understanding/a{i}.md', '资料理解', 'user')
    mem.apply_personal([update()], mem.list(), '我喜欢先看例子', 'old')
    tid = turn(app, '给我一个例子', chat='new-chat', status='queued', age=0)
    contexts = []
    def model(messages, tools, timeout):
        contexts.append(messages[0]['content'])
        return {'role': 'assistant', 'content': '例子：……'}
    app.state.agent.model = model
    app.state.agent.run(app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,)))
    assert len(contexts) == 1 and '偏好先看例子' in contexts[0]
    assert app.state.db.one('SELECT memory_status FROM tasks WHERE id=?', (tid,))['memory_status'] == 'pending'


def test_credentials_rejected_without_partial_write(app):
    app.state.cfg.save({'api_key': 'private-test-key'})
    with pytest.raises(ValueError):
        app.state.memory.apply_personal([update(), update('private-test-key', 'private-test-key', '密钥')], [],
                                        '我喜欢先看例子 private-test-key', 'new')
    assert app.state.memory.list() == []


def test_new_message_during_model_request_defers_write(app):
    tid = turn(app, '我喜欢先看例子')
    def model(*args):
        turn(app, '其实我现在喜欢先推导', status='queued', age=0)
        return {'content': json.dumps({'updates': [update()]}, ensure_ascii=False)}
    app.state.agent.model = model
    assert app.state.agent.review_idle()
    assert app.state.memory.list() == []
    assert app.state.db.one('SELECT memory_status FROM tasks WHERE id=?', (tid,))['memory_status'] == 'pending'


def test_worker_automatically_processes_idle_pending_turn(app):
    tid = turn(app, '我喜欢先看例子')
    reviewer(app, [update()])
    agent = app.state.agent
    worker = threading.Thread(target=agent.loop)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = app.state.db.one('SELECT memory_status FROM tasks WHERE id=?', (tid,))
            if row['memory_status'] == 'updated':
                break
            time.sleep(.02)
        assert row['memory_status'] == 'updated'
        assert app.state.memory.list()
    finally:
        agent.stop.set()
        agent.wake.set()
        worker.join(5)
    assert not worker.is_alive()


def test_timeout_preserves_answer_and_no_private_exception_details(app):
    tid = turn(app, '我喜欢先看例子')
    def model(*args):
        raise TimeoutError('敏感异常原文')
    app.state.agent.model = model
    assert app.state.agent.review_idle()
    row = app.state.db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert row['status'] == 'completed' and row['memory_status'] == 'failed'
    assert '敏感异常原文' not in row['memory_error']


def test_model_empty_updates_changes_no_markdown(app):
    tid = turn(app, '什么是特征向量？')
    reviewer(app, [])
    assert app.state.agent.review_idle()
    assert app.state.memory.list() == []
    assert app.state.db.one('SELECT memory_status FROM tasks WHERE id=?', (tid,))['memory_status'] == 'unchanged'


def test_delete_chat_cannot_remove_inflight_review(app):
    tid = turn(app, '我喜欢先看例子')
    app.state.db.execute("UPDATE tasks SET memory_status='reviewing' WHERE id=?", (tid,))
    endpoint = next(r.endpoint for r in app.routes if getattr(r, 'path', '') == '/api/chats/{cid}' and 'DELETE' in r.methods)
    with pytest.raises(ValueError, match='整理记忆'):
        endpoint('chat')
    assert app.state.db.one('SELECT id FROM tasks WHERE id=?', (tid,))
