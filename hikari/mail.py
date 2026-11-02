"""Local mail workflow. Only the authenticated final-editor route may call send.

Remote delivery cannot share SQLite's transaction: a lost SMTP acknowledgement is
deliberately an unknown outcome, including when the process dies during submission.
"""
from __future__ import annotations

import json
import re
import threading
import time
from email.utils import getaddresses

from .core import atomic_write, digest, uid


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def addresses(values):
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError('收件地址格式无效')
    result = []
    for value in values:
        if not isinstance(value, str) or re.search(r'[\r\n]', value):
            raise ValueError('收件地址格式无效')
        parsed = getaddresses([value])
        if len(parsed) != 1 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', parsed[0][1]):
            raise ValueError('请填写完整邮箱地址')
        result.append(parsed[0][1].lower())
    return list(dict.fromkeys(result))


class DefinitelyNotSent(Exception):
    """Transport proves DATA was not accepted. All other errors are ambiguous."""


class Mail:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.lock = threading.RLock()
        self.sync_lock = threading.RLock()
        self.transport = None
        self.root = cfg.home / 'email'
        self.root.mkdir(exist_ok=True)
        with db.tx() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS mail_meta(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS mail_messages(
                id TEXT PRIMARY KEY, thread TEXT, direction TEXT, received REAL,
                sender TEXT, refs TEXT, payload TEXT, deleted INTEGER DEFAULT 0,
                cleaned INTEGER DEFAULT 0, state TEXT, location TEXT DEFAULT '');
              CREATE INDEX IF NOT EXISTS mail_thread ON mail_messages(thread,received);
              CREATE TABLE IF NOT EXISTS mail_drafts(
                id TEXT PRIMARY KEY,message TEXT UNIQUE,version INTEGER,thread_version TEXT,
                payload TEXT,manual INTEGER DEFAULT 0,state TEXT,error TEXT DEFAULT '',
                requested INTEGER DEFAULT 0, instruction TEXT DEFAULT '',updated REAL);
              CREATE TABLE IF NOT EXISTS mail_versions(
                draft TEXT,version INTEGER,payload TEXT,created REAL,PRIMARY KEY(draft,version));
              CREATE TABLE IF NOT EXISTS mail_sends(
                id TEXT PRIMARY KEY,draft TEXT,version INTEGER,request TEXT UNIQUE,
                message_id TEXT UNIQUE,payload TEXT,hash TEXT,state TEXT,error TEXT,
                created REAL,UNIQUE(draft,version));
              CREATE TABLE IF NOT EXISTS mail_documents(
                name TEXT PRIMARY KEY,content TEXT,hash TEXT);
              CREATE TABLE IF NOT EXISTS mail_document_revisions(
                id TEXT PRIMARY KEY,name TEXT,before_text TEXT,after_text TEXT,created REAL);
            ''')
            c.execute("INSERT OR IGNORE INTO mail_meta VALUES('enabled_at',?)", (str(time.time()),))
        for name in ('profile', 'rules'):
            if not self.db.one('SELECT name FROM mail_documents WHERE name=?', (name,)):
                text = '# 邮件个人资料\n\n仅填写明确允许用于邮件的信息。\n' if name == 'profile' else '# 邮件排除规则\n\n每条规则为一行 JSON，scope=sender，保留用户原话 quote。\n'
                self.db.execute('INSERT INTO mail_documents VALUES(?,?,?)', (name, text, digest(text)))
            atomic_write(self.root / (name + '.md'), self.document(name)['content'])

    def recover(self):
        self.db.execute("UPDATE mail_sends SET state='unknown',error='提交期间服务停止，先核实，未自动重发' WHERE state='submitting'")
        self.db.execute("UPDATE mail_drafts SET state='unknown' WHERE id IN (SELECT draft FROM mail_sends WHERE state='unknown')")
        self.db.execute("UPDATE mail_drafts SET state='possible',error='拟稿中断，可重新请求' WHERE state='processing'")

    def meta(self, key, default=''):
        row = self.db.one('SELECT value FROM mail_meta WHERE key=?', (key,))
        return row['value'] if row else default

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO mail_meta VALUES(?,?)', (key, str(value)))

    def document(self, name):
        if name not in ('profile', 'rules'):
            raise ValueError('邮件资料类型无效')
        return self.db.one('SELECT * FROM mail_documents WHERE name=?', (name,))

    def rules(self, text=None):
        text = self.document('rules')['content'] if text is None else text
        result = []
        for line in text.splitlines():
            if line.startswith('- {'):
                try:
                    rule = json.loads(line[2:])
                    rule['address'] = addresses([rule['address']])[0]
                    if rule['scope'] != 'sender' or not rule['quote'].strip() or not isinstance(rule['active'], bool):
                        raise ValueError()
                    result.append(rule)
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise ValueError('规则需包含 address、scope=sender、用户原话 quote 和布尔 active') from None
        return result

    def write_document(self, name, text, expected_hash):
        if len(text) > 40000:
            raise ValueError('邮件资料过长')
        with self.lock, self.db.tx() as c:
            old = self.document(name)
            if old['hash'] != expected_hash:
                raise ValueError('资料已在其他设备修改，请刷新')
            if name == 'rules':
                self.rules(text)
            c.execute('INSERT INTO mail_document_revisions VALUES(?,?,?,?,?)', (uid(), name, old['content'], text, time.time()))
            c.execute('UPDATE mail_documents SET content=?,hash=? WHERE name=?', (text, digest(text), name))
        atomic_write(self.root / (name + '.md'), text)
        return self.document(name)

    def thread_version(self, thread):
        return digest(packed(self.db.rows('SELECT id,direction,deleted FROM mail_messages WHERE thread=? ORDER BY id', (thread,))))

    def ingest(self, records):
        """Transport supplies stable RFC identity, references and server INTERNALDATE.
        UID/UIDVALIDITY is only a locator, never the durable deduplication key.
        """
        with self.lock, self.db.tx() as c:
            for record in sorted(records, key=lambda x: x['received']):
                mid = record['id']
                old = c.execute('SELECT * FROM mail_messages WHERE id=?', (mid,)).fetchone()
                if old:
                    c.execute('UPDATE mail_messages SET location=?,deleted=0 WHERE id=?', (record.get('location', old['location']), mid))
                    continue
                refs = record.get('refs', [])
                parent = None
                for ref in reversed(refs):
                    parent = c.execute('SELECT thread FROM mail_messages WHERE id=?', (ref,)).fetchone()
                    if parent:
                        break
                thread = parent['thread'] if parent else (refs[0] if refs else mid)
                # A child may arrive before its parent; merge by explicit RFC references only.
                related = c.execute('SELECT thread,refs FROM mail_messages').fetchall()
                for r in related:
                    if mid in json.loads(r['refs']):
                        c.execute('UPDATE mail_messages SET thread=? WHERE thread=?', (thread, r['thread']))
                sender = addresses([record['sender']])[0]
                c.execute('INSERT INTO mail_messages(id,thread,direction,received,sender,refs,payload,state,location) VALUES(?,?,?,?,?,?,?,?,?)',
                          (mid, thread, record['direction'], record['received'], sender, packed(refs), packed(record),
                           'new' if record['direction'] == 'in' and record['received'] >= float(self.meta('enabled_at')) else 'history', record.get('location', '')))
                c.execute("UPDATE mail_drafts SET state='review',version=version+1,updated=? WHERE message IN (SELECT id FROM mail_messages WHERE thread=?) AND state IN ('draft','incomplete','possible','processing','review')", (time.time(), thread))
            # Explicit reply references supersede pending work. A later incoming message
            # supersedes earlier ones in the same thread, never same-sender topics.
            for r in c.execute('SELECT * FROM mail_messages ORDER BY received').fetchall():
                later = c.execute('SELECT * FROM mail_messages WHERE thread=? AND received>? ORDER BY received', (r['thread'], r['received'])).fetchall()
                replied = any(x['direction'] == 'out' and r['id'] in json.loads(x['refs']) for x in later)
                superseded = any(x['direction'] == 'in' for x in later)
                if r['direction'] == 'in' and (replied or superseded):
                    state = 'external_reply' if replied else 'superseded'
                    c.execute("UPDATE mail_messages SET state=? WHERE id=? AND state NOT IN ('sent','cleaned')", (state, r['id']))
                    c.execute("UPDATE mail_drafts SET state=?,version=version+1 WHERE message=? AND state NOT IN ('sent','unknown','submitting','cleaned',?)", (state, r['id'], state))
            for send in c.execute("SELECT * FROM mail_sends WHERE state='unknown'").fetchall():
                if c.execute("SELECT id FROM mail_messages WHERE id=? AND direction='out'", (send['message_id'],)).fetchone():
                    c.execute("UPDATE mail_sends SET state='sent',error='' WHERE id=?", (send['id'],))
                    c.execute("UPDATE mail_drafts SET state='sent' WHERE id=?", (send['draft'],))

    def detail(self, mid):
        message = self.db.one('SELECT * FROM mail_messages WHERE id=?', (mid,))
        if not message:
            raise ValueError('邮件不存在')
        draft = self.db.one('SELECT * FROM mail_drafts WHERE message=?', (mid,))
        if draft:
            draft['payload'] = json.loads(draft['payload'])
        return {'message': message | {'payload': json.loads(message['payload'])}, 'draft': draft,
                'thread_version': self.thread_version(message['thread']),
                'history': [r | {'payload': json.loads(r['payload'])} for r in self.db.rows('SELECT * FROM mail_messages WHERE thread=? ORDER BY received', (message['thread'],))],
                'versions': self.db.rows('SELECT * FROM mail_versions WHERE draft=? ORDER BY version DESC', (draft['id'],)) if draft else [],
                'sends': self.db.rows('SELECT id,version,state,error,created FROM mail_sends WHERE draft=? ORDER BY created DESC', (draft['id'],)) if draft else []}

    def request_draft(self, mid, instruction=''):
        with self.lock:
            d = self.detail(mid)
            m, old = d['message'], d['draft']
            if m['deleted'] or m['cleaned']:
                raise ValueError('请先主动重新获取源邮件')
            if old and old['state'] in ('submitting', 'unknown', 'sent'):
                raise ValueError('请先核实发送状态')
            if old:
                self.db.execute("UPDATE mail_drafts SET requested=1,instruction=?,state='possible',version=version+1,error='' WHERE id=?", (instruction, old['id']))
            else:
                payload = {'to': [m['sender']], 'cc': [], 'subject': 'Re: ' + m['payload'].get('subject', ''), 'body': '', 'attachments': [], 'questions': []}
                self.db.execute('INSERT INTO mail_drafts(id,message,version,thread_version,payload,state,requested,instruction,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                                (uid(), mid, 1, d['thread_version'], packed(payload), 'possible', 1, instruction, time.time()))
            self.db.execute("UPDATE mail_messages SET state='requested' WHERE id=?", (mid,))

    def save_draft(self, mid, version, thread_version, payload):
        with self.lock:
            d = self.detail(mid)
            old = d['draft']
            if not old or old['version'] != version or d['thread_version'] != thread_version:
                raise ValueError('邮件或草稿已变化，请打开最新版本复核')
            if old['state'] in ('submitting', 'unknown', 'sent', 'cleaned', 'external_reply', 'superseded'):
                raise ValueError('当前状态不能编辑发送，请重新核对邮件')
            payload = self.validate_payload(payload)
            state = 'incomplete' if payload['questions'] else 'draft'
            self.db.execute('INSERT OR IGNORE INTO mail_versions VALUES(?,?,?,?)', (old['id'], version, packed(old['payload']), time.time()))
            self.db.execute('UPDATE mail_drafts SET payload=?,version=version+1,thread_version=?,manual=1,state=?,requested=0,updated=? WHERE id=?',
                            (packed(payload), thread_version, state, time.time(), old['id']))
            return self.detail(mid)

    def validate_payload(self, p):
        if not isinstance(p, dict):
            raise ValueError('草稿格式无效')
        result = {k: p.get(k, [] if k in ('to', 'cc', 'attachments', 'questions') else '') for k in ('to', 'cc', 'subject', 'body', 'attachments', 'questions')}
        result['to'], result['cc'] = addresses(result['to']), addresses(result['cc'])
        if not result['to'] or not isinstance(result['subject'], str) or re.search(r'[\r\n]', result['subject']) or len(result['subject']) > 998:
            raise ValueError('收件人或主题无效')
        if not isinstance(result['body'], str) or len(result['body']) > 100000:
            raise ValueError('正文无效或过长')
        if not isinstance(result['questions'], list) or any(not isinstance(q, str) for q in result['questions']):
            raise ValueError('待补充问题格式无效')
        if not isinstance(result['attachments'], list):
            raise ValueError('附件格式无效')
        for aid in result['attachments']:
            if not isinstance(aid, str) or not re.fullmatch(r'[a-f0-9]{24}', aid) or not (self.root / 'outgoing' / aid).is_file():
                raise ValueError('发送附件不可用，请重新添加')
        return result

    def send(self, mid, version, thread_version, request_id):
        if not self.transport:
            raise ValueError('邮箱尚未连接，请在主机配置')
        # Serialise preflight + freeze + submission against sync, cleanup and disconnect.
        with self.sync_lock, self.lock:
            d = self.detail(mid)
            old = d['draft']
            existing = self.db.one('SELECT * FROM mail_sends WHERE request=? OR (draft=? AND version=?)', (request_id, old['id'] if old else '', version))
            if existing:
                if not old or existing['draft'] != old['id'] or existing['version'] != version:
                    raise ValueError('发送请求标识已用于其他版本')
                return self.send_public(existing)
            self.sync(force=True)  # Must succeed, including sent-folder inspection.
            d = self.detail(mid)
            old = d['draft']
            if not old or old['version'] != version or d['thread_version'] != thread_version or old['thread_version'] != thread_version:
                raise ValueError('线程或草稿有更新，请打开最新版本重新确认')
            p = self.validate_payload(old['payload'])
            if old['state'] != 'draft' or p['questions'] or not p['body'].strip() or d['message']['deleted']:
                raise ValueError('请先保存完整草稿并解决待补充问题')
            sid = uid()
            message_id = '<hikari-' + sid + '@smail.nju.edu.cn>'
            with self.db.tx() as c:
                c.execute('INSERT INTO mail_sends VALUES(?,?,?,?,?,?,?,?,?,?)', (sid, old['id'], version, request_id, message_id, packed(p), digest(packed(p)), 'submitting', '', time.time()))
                c.execute("UPDATE mail_drafts SET state='submitting' WHERE id=?", (old['id'],))
            state, error = 'sent', ''
            try:
                self.transport.send(p, message_id, mid)
            except DefinitelyNotSent:
                state, error = 'failed', '服务器未接受发送；请检查配置后保存新版本再确认重试'
            except Exception:
                state, error = 'unknown', '发送结果待核实；未自动重发，请检查已发送邮件'
            with self.db.tx() as c:
                c.execute('UPDATE mail_sends SET state=?,error=? WHERE id=?', (state, error, sid))
                c.execute('UPDATE mail_drafts SET state=?,error=? WHERE id=?', ('draft' if state == 'failed' else state, error, old['id']))
                if state == 'sent':
                    c.execute("UPDATE mail_messages SET state='sent' WHERE id=?", (mid,))
            return self.send_public(self.db.one('SELECT * FROM mail_sends WHERE id=?', (sid,)))

    @staticmethod
    def send_public(row):
        return {k: row[k] for k in ('id', 'version', 'state', 'error', 'created')}

    def resolve(self, sid, action):
        with self.sync_lock, self.lock:
            row = self.db.one("SELECT * FROM mail_sends WHERE id=? AND state='unknown'", (sid,))
            if not row:
                raise ValueError('没有待核实发送')
            if action not in ('sent', 'risk_retry'):
                raise ValueError('请选择已核实发送，或明确承担重复风险再试')
            with self.db.tx() as c:
                c.execute('UPDATE mail_sends SET state=?,error=? WHERE id=?', ('sent' if action == 'sent' else 'risk_released', '用户手动核实' if action == 'sent' else '用户明确承担重复风险', sid))
                c.execute('UPDATE mail_drafts SET state=?,version=version+1 WHERE id=?', ('sent' if action == 'sent' else 'review', row['draft']))

    def discard(self, mid):
        with self.lock:
            d = self.detail(mid)
            if d['draft'] and d['draft']['state'] in ('submitting', 'unknown'):
                raise ValueError('请先核实发送')
            self.db.execute("UPDATE mail_messages SET state='discarded' WHERE id=?", (mid,))
            self.db.execute("UPDATE mail_drafts SET state='discarded',requested=0,version=version+1 WHERE message=?", (mid,))

    def cleanup(self):
        # Wait for a submission already in progress. Keep tombstones and uncertain sends.
        with self.sync_lock, self.lock, self.db.tx() as c:
            c.execute("UPDATE mail_messages SET payload='{}',cleaned=1,sender='',state=CASE WHEN state='sent' THEN state ELSE 'cleaned' END")
            c.execute("UPDATE mail_drafts SET payload='{}',instruction='',requested=0,version=version+1,state=CASE WHEN state IN ('unknown','sent') THEN state ELSE 'cleaned' END,error=''")
            c.execute('DELETE FROM mail_versions')
            c.execute("UPDATE mail_sends SET payload=''")
            import shutil
            for folder in ('incoming', 'outgoing'):
                shutil.rmtree(self.root / folder, ignore_errors=False) if (self.root / folder).exists() else None
        return {'ok': True, 'notice': '本地正文、附件与草稿已清理；最小记录及待核实发送仍保留，旧备份未删除'}

    def sync(self, force=False):
        with self.sync_lock:
            if not self.transport or self.meta('disconnected') == '1':
                raise ValueError('邮箱未连接')
            if self.meta('auth_failed') == '1':
                raise ValueError('邮箱凭证失效，请在主机重新配置后重启')
            try:
                records, missing = self.transport.fetch(self)
                self.ingest(records)
                with self.lock:
                    for mid in missing:
                        self.db.execute('UPDATE mail_messages SET deleted=1 WHERE id=?', (mid,))
                        self.db.execute("UPDATE mail_drafts SET state='source_deleted',version=version+1 WHERE message=? AND state NOT IN ('sent','unknown','submitting','cleaned','source_deleted')", (mid,))
                self.set_meta('last_sync', time.time())
                self.set_meta('sync_error', '')
            except Exception as e:
                from .mail_transport import AuthenticationFailed
                if isinstance(e, AuthenticationFailed):
                    self.set_meta('auth_failed', '1')
                self.set_meta('sync_error', '凭证失效，请在主机重新配置' if isinstance(e, AuthenticationFailed) else '同步失败，请检查主机网络、邮箱配置和文件夹')
                raise ValueError(self.meta('sync_error')) from None

    def status(self):
        return {'connected': bool(self.transport) and self.meta('disconnected') != '1',
                'paused': self.meta('paused') == '1', 'last_sync': float(self.meta('last_sync', '0')),
                'error': self.meta('sync_error'), 'pending': self.db.one("SELECT count(*) n FROM mail_drafts WHERE state IN ('draft','incomplete','possible','review','unknown','processing')")['n']}
