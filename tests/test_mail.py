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
