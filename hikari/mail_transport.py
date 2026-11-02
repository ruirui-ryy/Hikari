"""IMAP read-only/PEEK and SMTP SSL adapter. Secrets never enter Config/public API."""
from __future__ import annotations

import base64
import email
import imaplib
import json
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser

from .core import atomic_write, digest, uid
from .mail import DefinitelyNotSent


class AuthenticationFailed(Exception):
    pass


def protect_secret(text):
    """Windows DPAPI, bound to the current host user; no plaintext fallback."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]

    def run(raw, decrypt):
        buf = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        dest = Blob()
        crypt = ctypes.windll.crypt32
        if decrypt:
            ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
        else:
            ok = crypt.CryptProtectData(ctypes.byref(source), 'Hikari mail', None, None, None, 1, ctypes.byref(dest))
        if not ok:
            raise ValueError('Windows 无法访问本机邮箱凭证，请重新配置')
        try:
            return ctypes.string_at(dest.data, dest.size)
        finally:
            ctypes.windll.kernel32.LocalFree(dest.data)
    if isinstance(text, str):
        return base64.b64encode(run(text.encode(), False)).decode()
    return run(base64.b64decode(text), True).decode()


def configure(cfg):
    import getpass
    address = input('完整学生邮箱地址: ').strip().lower()
    from .mail import addresses
    address = addresses([address])[0]
    if not address.endswith('@smail.nju.edu.cn'):
        raise ValueError('此接入仅支持 smail.nju.edu.cn')
    password = getpass.getpass('邮箱客户端专用密码（不显示）: ')
    if not password:
        raise ValueError('客户端专用密码不能为空')
    # Folder names are not assumed; discover selectable sent folders via SPECIAL-USE.
    sent = input('已发送文件夹的 IMAP 名称（留空尝试自动发现）: ').strip()
    value = dict(address=address, password=password, sent=sent)
    atomic_write(cfg.home / 'email-secret.dpapi', protect_secret(json.dumps(value)))
    from .core import DB
    db = DB(cfg.home / 'app.sqlite3')
    if db.one("SELECT name FROM sqlite_master WHERE name='mail_meta'"):
        db.execute("DELETE FROM mail_meta WHERE key IN ('auth_failed','disconnected')")
    print('凭证已使用 Windows 用户保护保存。重启 Hikari 后开始同步；不会自动发送邮件。')


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'br', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_message(raw, direction, received, location, root):
    msg = email.message_from_bytes(raw, policy=policy.default)
    # RFC Message-ID is normally stable across folders/UID resets. The fallback
    # hashes the raw message, excluding only mutable IMAP flags (not in RFC bytes).
    mid = str(msg.get('Message-ID', '')).strip() or '<sha256-' + digest(raw) + '@local>'
    refs = re.findall(r'<[^<>\s]+>', str(msg.get('References', '')) + ' ' + str(msg.get('In-Reply-To', '')))
    attachments = []
    body = msg.get_body(preferencelist=('plain', 'html'))
    body_warning = ''
    try:
        text = body.get_content() if body else ''
        if not isinstance(text, str):
            raise ValueError('non-text content')
    except (LookupError, UnicodeError, ValueError, KeyError):
        text = ''
        body_warning = '正文编码或格式无法解析，尚未读懂，请在邮箱核对'
    if body and body.get_content_type() == 'text/html':
        parser = TextHTML()
        parser.feed(text)
        text = ''.join(parser.parts)
    for part in msg.walk():
        if not part.get_filename() and part.get_content_disposition() != 'attachment':
            continue
        filename = part.get_filename() or '附件'
        data = part.get_payload(decode=True) or b''
        aid = uid()
        folder = root / 'incoming'
        folder.mkdir(exist_ok=True)
        suffix = __import__('pathlib').Path(filename).suffix.lower()
        path = folder / (aid + suffix)
        warning, extracted = '', ''
        if len(data) > 20 * 1024 * 1024:
            warning = '附件超过 20 MB，尚未读懂'
        else:
            path.write_bytes(data)
            try:
                if suffix in ('.pdf', '.docx', '.pptx', '.txt', '.md'):
                    from .library import extract
                    extracted = '\n'.join(u[2] for u in extract(path))
                    if not extracted.strip():
                        warning = '无可提取文字，图片或扫描内容尚未读懂'
                    if suffix in ('.pdf', '.docx', '.pptx'):
                        warning = (warning + '；' if warning else '') + '仅提取文字，图片与图中内容尚未读懂'
                    if len(extracted) > 60000:
                        extracted = extracted[:60000]
                        warning += '；提取文字超过上限，后续内容未读'
                else:
                    warning = '此格式尚未读懂'
            except Exception:
                warning = '附件解析失败，尚未读懂'
        attachments.append(dict(id=aid, name=filename, text=extracted, warning=warning, file=path.name if path.exists() else ''))
    return dict(id=mid, refs=list(dict.fromkeys(refs)), sender=(getaddresses([str(msg.get('From', ''))]) or [('', '')])[0][1],
                to=[a for _, a in getaddresses(msg.get_all('To', []))], cc=[a for _, a in getaddresses(msg.get_all('Cc', []))],
                subject=str(msg.get('Subject', '')), body=text[:100000], attachments=attachments,
                received=received, direction=direction, location=location,
                warning='正文超过读取上限' if len(text) > 100000 else body_warning)


class Transport:
    def __init__(self, cfg):
        self.cfg = cfg
        self.secrets = json.loads(protect_secret((cfg.home / 'email-secret.dpapi').read_bytes()))

    def connect(self):
        conn = imaplib.IMAP4_SSL('imap.exmail.qq.com', 993, ssl_context=ssl.create_default_context(), timeout=30)
        try:
            conn.login(self.secrets['address'], self.secrets['password'])
        except imaplib.IMAP4.error:
            conn.logout()
            raise AuthenticationFailed() from None
        return conn

    def folders(self, conn):
        sent = self.secrets.get('sent')
        if not sent:
            status, lines = conn.list()
            if status != 'OK':
                raise ValueError('无法列出邮箱文件夹')
            for line in lines or []:
                if line and b'\\sent' in line.lower():
                    # Preserve the server's encoded/quoted wire mailbox name.
                    match = re.match(rb'\([^)]*\)\s+(?:"[^"]*"|NIL)\s+(.+)', line)
                    if match:
                        sent = match[1].decode('ascii')
                        break
        if not sent:
            raise ValueError('请在主机配置已发送文件夹名称')
        return [('INBOX', 'in'), (sent, 'out')]

    @staticmethod
    def checked(result):
        status, data = result
        if status != 'OK':
            raise ValueError('IMAP 请求失败')
        return data

    def fetch(self, service):
        conn = self.connect()
        records, present, inventories = [], set(), []
        try:
            folders = self.folders(conn)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=366)).strftime('%d-%b-%Y')
            for folder, direction in folders:
                self.checked(conn.select(folder, readonly=True))
                validity = (conn.response('UIDVALIDITY')[1] or [b''])[0].decode()
                all_uids = self.checked(conn.uid('search', None, 'ALL'))[0].split()
                recent = set(self.checked(conn.uid('search', None, 'SINCE', cutoff))[0].split())
                prefix = folder + ':' + validity + ':'
                inventories.append((prefix, set(all_uids)))
                known = {r['location']: r['id'] for r in service.db.rows('SELECT id,location FROM mail_messages')}
                for remote_uid in all_uids:
                    location = prefix + remote_uid.decode()
                    if location in known:
                        present.add(known[location])
                        continue
                    if remote_uid not in recent:
                        continue
                    header = self.checked(conn.uid('fetch', remote_uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES IN-REPLY-TO FROM TO CC SUBJECT)] INTERNALDATE RFC822.SIZE)'))
                    chunks = [r for r in header if isinstance(r, tuple)]
                    if not chunks:
                        raise ValueError('无法读取邮件标识')
                    metadata, raw_header = chunks[0]
                    mid = str(email.message_from_bytes(raw_header, policy=policy.default).get('Message-ID', '')).strip()
                    if mid and service.db.one('SELECT id FROM mail_messages WHERE id=?', (mid,)):
                        present.add(mid)
                        service.db.execute("UPDATE mail_messages SET location=?,deleted=0,direction=CASE WHEN ?='out' THEN 'out' ELSE direction END WHERE id=?", (location, direction, mid))
                        continue
                    size = re.search(rb'RFC822.SIZE (\d+)', metadata)
                    stamp = imaplib.Internaldate2tuple(metadata)
                    if not stamp:
                        raise ValueError('邮件缺少服务器收件时间')
                    received = time.mktime(stamp)
                    if size and int(size[1]) > 30 * 1024 * 1024:
                        record = parse_message(raw_header, direction, received, location, service.root)
                        record['warning'] = '邮件超过 30 MB，仅保存邮件头；正文和附件尚未读懂，请在邮箱核对'
                        records.append(record)
                        present.add(record['id'])
                        continue
                    full = self.checked(conn.uid('fetch', remote_uid, '(BODY.PEEK[])'))
                    raw = b''.join(r[1] for r in full if isinstance(r, tuple))
                    if not raw:
                        raise ValueError('无法读取邮件正文')
                    record = parse_message(raw, direction, received, location, service.root)
                    records.append(record)
                    present.add(record['id'])
            # Only locators in the current UID epoch can be proven deleted. Older
            # epochs require identity reconciliation; absence isn't deletion proof.
            missing = [r['id'] for r in service.db.rows('SELECT id,location FROM mail_messages WHERE deleted=0')
                       if r['id'] not in present and any(r['location'].startswith(prefix) and r['location'][len(prefix):].encode() not in uids
                                                        for prefix, uids in inventories)]
            return records, missing
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def send(self, payload, message_id, reply_to):
        smtp = None
        entered_data = False
        try:
            smtp = smtplib.SMTP_SSL('smtp.exmail.qq.com', 465, timeout=30, context=ssl.create_default_context())
            smtp.login(self.secrets['address'], self.secrets['password'])
            msg = EmailMessage()
            msg['From'] = self.secrets['address']
            msg['To'] = ', '.join(payload['to'])
            if payload['cc']:
                msg['Cc'] = ', '.join(payload['cc'])
            msg['Subject'], msg['Message-ID'], msg['In-Reply-To'] = payload['subject'], message_id, reply_to
            msg['References'] = reply_to
            msg['Date'] = email.utils.formatdate(localtime=True)
            msg.set_content(payload['body'])
            for aid in payload['attachments']:
                folder = self.cfg.home / 'email' / 'outgoing'
                info = json.loads((folder / (aid + '.json')).read_text('utf-8'))
                data = (folder / aid).read_bytes()
                confirmed = payload.get('attachment_details', {}).get(aid)
                if confirmed != {'name': info['name'], 'hash': digest(data)}:
                    raise DefinitelyNotSent()
                msg.add_attachment(data, maintype='application', subtype='octet-stream', filename=info['name'])
            # Explicit envelope commands identify definite pre-DATA rejections and
            # avoid silently sending to only a subset of confirmed recipients.
            if smtp.mail(self.secrets['address'])[0] != 250:
                raise DefinitelyNotSent()
            recipients = list(dict.fromkeys(payload['to'] + payload['cc']))
            if any(smtp.rcpt(a)[0] not in (250, 251) for a in recipients):
                smtp.rset()
                raise DefinitelyNotSent()
            entered_data = True
            smtp.data(msg.as_bytes())
        except smtplib.SMTPDataError:
            raise DefinitelyNotSent() from None
        except DefinitelyNotSent:
            raise
        except Exception:
            if not entered_data:
                raise DefinitelyNotSent() from None
            raise
        finally:
            if smtp:
                # QUIT failure after a positive DATA reply cannot undo acceptance.
                try:
                    smtp.close()
                except Exception:
                    pass

    def fetch_specific(self, service, ids):
        """Explicit user restoration / older reference lookup, not routine sync."""
        conn = self.connect()
        records = []
        try:
            for folder, direction in self.folders(conn):
                self.checked(conn.select(folder, readonly=True))
                validity = (conn.response('UIDVALIDITY')[1] or [b''])[0].decode()
                for mid in ids[:20]:
                    if not re.fullmatch(r'<[^<>\s"\\]+>', mid):
                        continue
                    found = self.checked(conn.uid('search', None, 'HEADER', 'Message-ID', '"' + mid + '"'))[0].split()
                    for remote_uid in found:
                        headers = self.checked(conn.uid('fetch', remote_uid, '(INTERNALDATE RFC822.SIZE)'))
                        metadata = next((h[0] if isinstance(h, tuple) else h for h in headers if h), b'')
                        size = re.search(rb'RFC822.SIZE (\d+)', metadata)
                        stamp = imaplib.Internaldate2tuple(metadata)
                        if not size or int(size[1]) > 30 * 1024 * 1024 or not stamp:
                            continue
                        data = self.checked(conn.uid('fetch', remote_uid, '(BODY.PEEK[])'))
                        raw = b''.join(r[1] for r in data if isinstance(r, tuple))
                        record = parse_message(raw, direction, time.mktime(stamp), folder + ':' + validity + ':' + remote_uid.decode(), service.root)
                        if record['id'] == mid:
                            records.append(record)
            return records
        finally:
            try:
                conn.logout()
            except Exception:
                pass
