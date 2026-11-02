from __future__ import annotations

import json
import threading

from fastapi import Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .core import atomic_write, digest, uid, within
from .mail import packed


class MailAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mid: str = Field(min_length=1, max_length=1000)


class DraftRequest(MailAction):
    instruction: str = Field(default='', max_length=16000)


class DraftEdit(MailAction):
    version: int = Field(ge=1)
    thread_version: str = Field(min_length=64, max_length=64)
    payload: dict


class SendRequest(MailAction):
    version: int = Field(ge=1)
    thread_version: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=8, max_length=100)


class MailDocument(BaseModel):
    content: str = Field(max_length=40000)
    expected_hash: str = Field(max_length=64)


class ResolveRequest(BaseModel):
    send_id: str = Field(max_length=100)
    action: str = Field(pattern='^(sent|risk_retry)$')


class PauseRequest(BaseModel):
    paused: bool


def mount_mail(app, mail, agent, auth):
    deps = [Depends(auth)]

    @app.get('/api/email', dependencies=deps)
    def inbox():
        rows = mail.db.rows('SELECT m.*,d.state AS draft_state,d.version,d.error FROM mail_messages m LEFT JOIN mail_drafts d ON d.message=m.id ORDER BY m.received DESC')
        for r in rows:
            p = json.loads(r.pop('payload'))
            r['subject'] = p.get('subject', '已清理的邮件')
        return mail.status() | {'messages': rows}

    @app.get('/api/email/message', dependencies=deps)
    def detail(mid: str):
        return mail.detail(mid)

    @app.post('/api/email/draft', dependencies=deps)
    def request_draft(data: DraftRequest):
        mail.request_draft(data.mid, data.instruction)
        agent.wake.set()
        return mail.detail(data.mid)

    @app.put('/api/email/draft', dependencies=deps)
    def edit(data: DraftEdit):
        return mail.save_draft(data.mid, data.version, data.thread_version, data.payload)

    @app.post('/api/email/send', dependencies=deps)
    def send(data: SendRequest):
        return mail.send(data.mid, data.version, data.thread_version, data.request_id)

    @app.post('/api/email/resolve', dependencies=deps)
    def resolve(data: ResolveRequest):
        mail.resolve(data.send_id, data.action)
        return {'ok': True}

    @app.post('/api/email/discard', dependencies=deps)
    def discard(data: MailAction):
        mail.discard(data.mid)
        return {'ok': True}

    @app.post('/api/email/sync', dependencies=deps)
    def sync():
        mail.sync(force=True)
        agent.wake.set()
        return mail.status()

    @app.post('/api/email/pause', dependencies=deps)
    def pause(data: PauseRequest):
        with mail.lock:
            mail.set_meta('paused', '1' if data.paused else '0')
        agent.wake.set()
        return mail.status()

    @app.post('/api/email/disconnect', dependencies=deps)
    def disconnect():
        with mail.sync_lock, mail.lock:
            mail.set_meta('disconnected', '1')
            mail.transport = None
            (mail.cfg.home / 'email-secret.dpapi').unlink(missing_ok=True)
        return {'ok': True, 'notice': '已停止邮箱收发并删除本机凭证。若需撤销客户端密码，请前往邮箱网站。'}

    @app.post('/api/email/cleanup', dependencies=deps)
    def cleanup():
        return mail.cleanup()

    @app.get('/api/email/documents/{name}', dependencies=deps)
    def document(name: str):
        return mail.document(name)

    @app.put('/api/email/documents/{name}', dependencies=deps)
    def write_document(name: str, data: MailDocument):
        return mail.write_document(name, data.content, data.expected_hash)

    @app.get('/api/email/revisions', dependencies=deps)
    def revisions():
        return mail.db.rows('SELECT * FROM mail_document_revisions ORDER BY created DESC LIMIT 100')

    @app.post('/api/email/attachments', dependencies=deps)
    async def upload(file: UploadFile = File(...)):
        name = (file.filename or '附件').replace('\\', '/').split('/')[-1]
        if any(c in name for c in '\r\n') or len(name) > 200:
            raise ValueError('附件名称无效')
        data = await file.read(20 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError('发送附件上限为 20 MB')
        with mail.lock:
            aid = uid()
            folder = mail.root / 'outgoing'
            folder.mkdir(exist_ok=True)
            (folder / aid).write_bytes(data)
            atomic_write(folder / (aid + '.json'), packed({'name': name, 'hash': digest(data)}))
        return {'id': aid, 'name': name}

    @app.get('/api/email/attachments/{aid}', dependencies=deps)
    def attachment(aid: str):
        # Never expose attachment files inline in the authenticated application origin.
        for row in mail.db.rows('SELECT payload FROM mail_messages WHERE cleaned=0'):
            for a in json.loads(row['payload']).get('attachments', []):
                if a['id'] == aid and a.get('file'):
                    path = within(mail.root / 'incoming', a['file'])
                    if path.is_file():
                        return FileResponse(path, filename=a['name'], media_type='application/octet-stream')
        folder = mail.root / 'outgoing'
        if len(aid) == 24 and all(c in '0123456789abcdef' for c in aid) and (folder / aid).is_file():
            info = json.loads((folder / (aid + '.json')).read_text('utf-8'))
            return FileResponse(folder / aid, filename=info['name'], media_type='application/octet-stream')
        raise HTTPException(404, '附件不可用')

    @app.post('/api/email/restore', dependencies=deps)
    def restore(data: MailAction):
        with mail.sync_lock, mail.lock:
            if not mail.transport or mail.meta('disconnected') == '1':
                raise ValueError('邮箱未连接')
            records = mail.transport.fetch_specific(mail, [data.mid])
            record = next((r for r in records if r['id'] == data.mid), None)
            if not record:
                raise ValueError('未在服务器找到这封邮件')
            mail.db.execute('UPDATE mail_messages SET payload=?,sender=?,cleaned=0,deleted=0 WHERE id=?', (packed(record), record['sender'], data.mid))
            return mail.detail(data.mid)

    @app.post('/api/email/history', dependencies=deps)
    def older_history(data: MailAction):
        with mail.sync_lock, mail.lock:
            if not mail.transport:
                raise ValueError('邮箱未连接')
            detail = mail.detail(data.mid)
            refs = json.loads(detail['message']['refs'])
            wanted = [r for r in refs if not mail.db.one('SELECT id FROM mail_messages WHERE id=?', (r,))]
            records = mail.transport.fetch_specific(mail, wanted)
            # Older references are background, never auto-draft candidates.
            mail.ingest(records)
            for r in records:
                mail.db.execute("UPDATE mail_messages SET state='history' WHERE id=? AND state='new'", (r['id'],))
            return mail.detail(data.mid)


def sync_loop(mail, stop, wake):
    delay = 0
    while not stop.wait(delay):
        if not mail.transport or mail.meta('disconnected') == '1' or mail.meta('auth_failed') == '1':
            delay = 120
            continue
        try:
            mail.sync()
            wake.set()
            delay = 120
        except Exception:
            delay = min(1800, max(120, delay * 2))
