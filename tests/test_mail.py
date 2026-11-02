import json
import time

import pytest

from hikari.core import Config, DB
from hikari.mail import Mail, DefinitelyNotSent


class FakeTransport:
    def __init__(self):
        self.records, self.missing, self.sent = [], [], []
        self.fail = None

    def fetch(self, service):
        return self.records, self.missing

    def send(self, payload, message_id, reply_to):
        self.sent.append((payload, message_id, reply_to))
        if self.fail:
            raise self.fail()


@pytest.fixture
def mail(tmp_path):
    cfg = Config(tmp_path)
    m = Mail(cfg, DB(tmp_path / 'app.sqlite3'))
    m.transport = FakeTransport()
    return m


def message(mid='<a@example.org>', refs=(), direction='in', received=None):
    return dict(id=mid, refs=list(refs), sender='person@example.org', subject='测试',
                body='请确认是否参加？', direction=direction, received=received or time.time() + 1, attachments=[])


def draft(m):
    m.ingest([message()])
    m.request_draft('<a@example.org>')
    d = m.detail('<a@example.org>')
    p = d['draft']['payload'] | {'body': '我会参加。'}
    return m.save_draft('<a@example.org>', d['draft']['version'], d['thread_version'], p)


def send(m, d, request='request-1'):
    return m.send(d['message']['id'], d['draft']['version'], d['thread_version'], request)


def test_sync_identity_history_and_topics(mail):
    records = [message(received=time.time() - 86400), message('<other@example.org>')]
    mail.ingest(records)
    mail.ingest(records)
    assert len(mail.db.rows('SELECT * FROM mail_messages')) == 2
    assert mail.detail(records[0]['id'])['message']['state'] == 'history'
    assert mail.detail(records[1]['id'])['message']['thread'] != mail.detail(records[0]['id'])['message']['thread']
    mail = Mail(mail.cfg, mail.db)
    assert len(mail.db.rows('SELECT * FROM mail_messages')) == 2


def test_exact_version_and_duplicate_send(mail):
    d = draft(mail)
    p = d['draft']['payload'] | {'cc': ['second@example.org'], 'subject': '最终主题', 'body': '最终正文'}
    latest = mail.save_draft(d['message']['id'], d['draft']['version'], d['thread_version'], p)
    with pytest.raises(ValueError, match='更新'):
        send(mail, d)
    result = send(mail, latest)
    assert result['state'] == 'sent'
    assert send(mail, latest) == result
    assert send(mail, latest, 'different-request') == result
    assert mail.transport.sent[0][0] == p
    assert len(mail.transport.sent) == 1


def test_new_mail_and_external_reply_invalidate(mail):
    d = draft(mail)
    mail.transport.records = [message('<b@example.org>', ['<a@example.org>'], received=time.time() + 10)]
    with pytest.raises(ValueError):
        send(mail, d)
    assert not mail.transport.sent
    assert mail.detail('<a@example.org>')['draft']['payload']['body'] == '我会参加。'
    mail.ingest([message('<sent@example.org>', ['<a@example.org>'], 'out', time.time() + 20)])
    assert mail.detail('<a@example.org>')['draft']['state'] == 'external_reply'


def test_uncertain_restart_and_reconcile(mail):
    d = draft(mail)
    mail.transport.fail = ConnectionError
    result = send(mail, d)
    assert result['state'] == 'unknown'
    assert send(mail, d) == result
    other = Mail(mail.cfg, mail.db)
    other.recover()
    assert other.detail('<a@example.org>')['draft']['state'] == 'unknown'
    mid = mail.transport.sent[0][1]
    other.ingest([message(mid, ['<a@example.org>'], 'out', time.time() + 10)])
    assert other.db.one('SELECT state FROM mail_sends')['state'] == 'sent'


def test_restart_submitting_never_replays(mail):
    d = draft(mail)
    mail.db.execute("INSERT INTO mail_sends VALUES('s',?,?,'r','<s@example.org>','{}','h','submitting','',?)", (d['draft']['id'], d['draft']['version'], time.time()))
    mail.recover()
    assert mail.db.one('SELECT state FROM mail_sends')['state'] == 'unknown'
    assert not mail.transport.sent


def test_definite_failure_new_version_required(mail):
    d = draft(mail)
    mail.transport.fail = DefinitelyNotSent
    assert send(mail, d)['state'] == 'failed'
    assert send(mail, d)['state'] == 'failed'
    assert len(mail.transport.sent) == 1
    latest = mail.save_draft(d['message']['id'], d['draft']['version'], d['thread_version'], d['draft']['payload'])
    mail.transport.fail = None
    assert send(mail, latest, 'retry-request')['state'] == 'sent'


def test_cleanup_keeps_tombstones_and_uncertainty(mail):
    d = draft(mail)
    mail.transport.fail = ConnectionError
    send(mail, d)
    mail.cleanup()
    mail.ingest([message()])
    state = mail.detail('<a@example.org>')
    assert state['message']['cleaned'] and state['message']['payload'] == {}
    assert state['draft']['state'] == 'unknown'
    assert mail.db.one('SELECT payload FROM mail_sends')['payload'] == ''
    assert mail.db.rows('SELECT * FROM mail_versions') == []
    with pytest.raises(ValueError):
        mail.request_draft('<a@example.org>')
    mail.ingest([message('<new@example.org>', ['<a@example.org>'])])
    assert mail.detail('<new@example.org>')['message']['state'] == 'new'


def test_markdown_revisions_restart_and_conflict(mail):
    rules = mail.document('rules')
    text = rules['content'] + '\n- ' + json.dumps(dict(address='person@example.org', scope='sender', quote='以后不用回这个地址', active=True), ensure_ascii=False)
    mail.write_document('rules', text, rules['hash'])
    other = Mail(mail.cfg, mail.db)
    assert other.rules()[0]['active'] is True
    with pytest.raises(ValueError, match='其他设备'):
        other.write_document('rules', '', rules['hash'])
    other.write_document('rules', rules['content'], other.document('rules')['hash'])
    assert not other.rules()
    assert len(other.db.rows('SELECT * FROM mail_document_revisions')) == 2


def test_incomplete_and_header_injection_blocked(mail):
    d = draft(mail)
    p = d['draft']['payload'] | {'questions': ['请补充时间']}
    latest = mail.save_draft(d['message']['id'], d['draft']['version'], d['thread_version'], p)
    with pytest.raises(ValueError):
        send(mail, latest)
    with pytest.raises(ValueError):
        mail.validate_payload(p | {'to': ['a@example.org\r\nBcc: b@example.org']})
    assert not mail.transport.sent


def worker(mail, model=None):
    from hikari.agent import Agent
    from hikari.library import Library
    from hikari.memory import Memory
    library = Library(mail.cfg, mail.db)
    return Agent(mail.cfg, mail.db, library, Memory(mail.cfg, mail.db, library), model or (lambda *_: {'content': json.dumps(dict(category='draft', body='谢谢来信。', questions=[]))}))


def test_model_scope_manual_protection_and_rules(mail):
    m = message()
    mail.ingest([m])
    requests = []
    def model(messages, tools, timeout):
        requests.append(messages)
        assert tools == []
        context = json.loads(messages[1]['content'])
        assert set(context) == {'message', 'references', 'profile', 'instruction', 'previous'}
        return {'content': '{"category":"draft","body":"谢谢。","questions":[]}'}
    a = worker(mail, model)
    assert mail.process_one(a)
    d = mail.detail(m['id'])
    p = d['draft']['payload'] | {'body': '我的手工正文'}
    mail.save_draft(m['id'], d['draft']['version'], d['thread_version'], p)
    assert not mail.process_one(a)
    assert mail.detail(m['id'])['draft']['payload']['body'] == '我的手工正文'
    # New reference on another branch invalidates the manual version, preserving it.
    mail.ingest([message('<branch@example.org>', [m['id']], 'out', time.time() - 10)])
    assert not mail.process_one(a)
    assert len(requests) == 1
    r = mail.document('rules')
    mail.write_document('rules', r['content'] + '\n- {"address":"person@example.org","scope":"sender","quote":"以后不用回这个地址","active":true}', r['hash'])
    mail.ingest([message('<excluded@example.org>')])
    assert mail.process_one(a)
    assert mail.detail('<excluded@example.org>')['message']['state'] == 'excluded'
    mail.request_draft('<excluded@example.org>', '请手动拟稿')
    assert mail.process_one(a, manual_only=True)
    assert len(requests) == 2


def test_model_response_cannot_overwrite_concurrent_edit(mail):
    draft(mail)
    mail.request_draft('<a@example.org>', '改短些')
    def model(*_):
        d = mail.detail('<a@example.org>')
        mail.save_draft('<a@example.org>', d['draft']['version'], d['thread_version'], d['draft']['payload'] | {'body': '另一设备编辑'})
        return {'content': '{"category":"draft","body":"过时模型输出","questions":[]}'}
    assert mail.process_one(worker(mail, model))
    assert mail.detail('<a@example.org>')['draft']['payload']['body'] == '另一设备编辑'


def test_pause_and_chat_priority_and_history(mail):
    mail.ingest([message(), message('<old@example.org>', received=time.time() - 1000)])
    mail.set_meta('paused', '1')
    assert not mail.process_one(worker(mail))
    mail.request_draft('<old@example.org>', '回复历史邮件')
    assert mail.process_one(worker(mail), manual_only=True)
    mail.set_meta('paused', '0')
    mail.db.execute("INSERT INTO tasks(id,status) VALUES('chat-priority','queued')")
    assert not mail.process_one(worker(mail))


def test_unread_attachment_forces_question_and_clean_during_model(mail):
    m = message()
    m['attachments'] = [{'name': '扫描.pdf', 'warning': '尚未读懂', 'text': ''}]
    mail.ingest([m])
    assert mail.process_one(worker(mail))
    assert mail.detail(m['id'])['draft']['state'] == 'incomplete'
    mail.request_draft(m['id'])
    def model(*_):
        mail.cleanup()
        return {'content': '{"category":"draft","body":"不得恢复","questions":[]}'}
    mail.process_one(worker(mail, model))
    assert mail.detail(m['id'])['draft']['state'] == 'cleaned'
    assert mail.detail(m['id'])['draft']['payload'] == {}


def test_host_markdown_edit_survives_restart_and_records_revision(mail):
    r = mail.document('rules')
    text = r['content'] + '\n- {"address":"person@example.org","scope":"sender","quote":"以后不用回这个人","active":true}\n'
    (mail.root / 'rules.md').write_text(text, 'utf-8')
    other = Mail(mail.cfg, mail.db)
    assert other.rules()[0]['active']
    assert other.db.one('SELECT after_text FROM mail_document_revisions')['after_text'] == text
    (other.root / 'rules.md').write_text('- {broken json}\n', 'utf-8')
    other.ingest([message()])
    assert not other.safe_process_one(worker(other))
    assert other.status()['error']
    assert (other.root / 'rules.md').read_text('utf-8') == '- {broken json}\n'


def test_new_arrival_preserves_manual_draft_for_explicit_update(mail):
    original = draft(mail)
    mail.ingest([message('<newer@example.org>', ['<a@example.org>'], received=time.time() + 10)])
    assert mail.detail('<a@example.org>')['draft']['state'] == 'review'
    assert mail.detail('<newer@example.org>')['message']['state'] == 'needs_review'
    assert not mail.process_one(worker(mail))
    with pytest.raises(ValueError):
        send(mail, original)
    mail.request_draft('<newer@example.org>', '根据新来信更新')
    assert mail.detail('<newer@example.org>')['draft']['payload']['body'] == '我会参加。'
    assert mail.process_one(worker(mail), manual_only=True)
    assert mail.detail('<a@example.org>')['draft']['payload']['body'] == '我会参加。'


def test_superseded_auto_draft_exits_worker_candidates(mail):
    mail.ingest([message()])
    mail.process_one(worker(mail))
    mail.ingest([message('<newer@example.org>', ['<a@example.org>'], received=time.time() + 10)])
    assert mail.detail('<a@example.org>')['draft']['state'] == 'superseded'
    assert mail.process_one(worker(mail))
    assert not mail.process_one(worker(mail))


def test_revoking_rule_requeues_excluded_mail_without_reviving_cleaned(mail):
    rules = mail.document('rules')
    text = rules['content'] + '\n- {"address":"person@example.org","scope":"sender","quote":"以后不用回","active":true}'
    mail.write_document('rules', text, rules['hash'])
    mail.ingest([message()])
    assert mail.process_one(worker(mail))
    assert mail.detail('<a@example.org>')['message']['state'] == 'excluded'
    mail.write_document('rules', rules['content'], mail.document('rules')['hash'])
    assert mail.process_one(worker(mail))
    assert mail.detail('<a@example.org>')['draft']['state'] == 'draft'
