from __future__ import annotations

import html
import json
import mimetypes
import os
import secrets
import shutil
import sqlite3
import threading
import time
import zipfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .core import Config, DB, InstanceLock, ROOT, digest, password_matches, uid, within
from .library import Library, SUPPORTED, fingerprint
from .memory import Memory


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1000)


class ChatInput(BaseModel):
    title: str = Field(default='新对话', min_length=1, max_length=100)
    scope: str = Field(default='', max_length=500)


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    request_id: str = Field(min_length=8, max_length=100)


class MemoryInput(BaseModel):
    path: str = Field(max_length=150)
    content: str = Field(max_length=40000)
    expected_hash: str | None = None


class SettingsInput(BaseModel):
    model: str = Field(default='deepseek-flash', min_length=1, max_length=150)
    base_url: str = Field(default='https://api.deepseek.com', max_length=300)
    api_key: str | None = Field(default=None, max_length=1000)
    knowledge: str = Field(max_length=1000)
    max_tools: int = Field(default=24, ge=1, le=100)
    max_seconds: int = Field(default=300, ge=30, le=1800)
    secure_cookie: bool = False


def create_app(home=None, model=None, background=True):
    # Windows registry may map .js to text/plain; nosniff then blocks every script.
    mimetypes.init()
    mimetypes.add_type('application/javascript', '.js')
    mimetypes.add_type('text/css', '.css')
    cfg = Config(home)
    db = DB(cfg.home / 'app.sqlite3')
    library = Library(cfg, db)
    memory = Memory(cfg, db, library)
    agent = Agent(cfg, db, library, memory, model)
    stop = threading.Event()
    attempts = {}
    auth_lock = threading.Lock()

    def scanning():
        while not stop.is_set():
            library.scan()
            library.refresh.wait(cfg.get('scan_seconds'))
            library.refresh.clear()

    @asynccontextmanager
    async def lifespan(app):
        lock = InstanceLock(cfg.home / 'instance.lock')
        agent.recover()
        threads = []
        if background:
            for target in [agent.loop, scanning]:
                t = threading.Thread(target=target, daemon=True)
                t.start()
                threads.append(t)
        try:
            yield
        finally:
            stop.set()
            agent.stop.set()
            agent.wake.set()
            library.refresh.set()
            # Keep process ownership until in-flight model/file operations have exited.
            for t in threads:
                t.join()
            lock.close()

    app = FastAPI(title='Hikari', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db
    app.state.library, app.state.memory, app.state.agent = library, memory, agent

    @app.middleware('http')
    async def headers_and_origin(request: Request, call_next):
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            # Custom header prevents cross-site form submission even without Origin.
            if request.headers.get('x-hikari') != '1':
                return JSONResponse({'detail': '请求来源无效'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '跨站请求已拒绝'}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
            if request.url.path.endswith('.js'):
                response.headers['Content-Type'] = 'application/javascript'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    def auth(request: Request):
        token = request.cookies.get('hikari_session', '')
        if not token or not db.one('SELECT token FROM sessions WHERE token=? AND expires>?', (digest(token), time.time())):
            raise HTTPException(401, '请登录')

    @app.get('/api/health')
    def health():
        return {'service': 'hikari', 'demo': bool(getattr(app.state, 'demo', False)),
                'configured': bool(cfg.get('password'))}

    @app.get('/api/auth')
    def who(request: Request):
        try:
            auth(request)
            logged = True
        except HTTPException:
            logged = False
        return {'logged_in': logged, 'configured': bool(cfg.get('password')), 'username': cfg.get('username') if logged else '',
                'demo': bool(getattr(app.state, 'demo', False))}

    @app.post('/api/login')
    def login(data: Login, request: Request):
        now = time.time()
        # Global limit suits this single-user application, including proxied connections.
        with auth_lock:
            recent = [x for x in attempts.get('global', []) if x > now - 300]
            if len(recent) >= 10:
                raise HTTPException(429, '尝试次数过多，请五分钟后重试')
            attempts['global'] = recent + [now]
        if not cfg.get('password'):
            raise HTTPException(409, '请在主机运行启动脚本，先创建个人账户')
        if not secrets.compare_digest(data.username.encode(), cfg.get('username').encode()) or not password_matches(data.password, cfg.get('password')):
            raise HTTPException(401, '账户或密码不正确')
        with auth_lock:
            attempts.clear()
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (now,))
        db.execute('INSERT INTO sessions VALUES(?,?)', (digest(token), now + 30 * 86400))
        response = JSONResponse({'ok': True})
        secure = cfg.get('secure_cookie') or urlsplit(request.headers.get('origin', '')).scheme == 'https'
        response.set_cookie('hikari_session', token, httponly=True, samesite='strict', secure=secure, max_age=30 * 86400)
        return response

    @app.post('/api/logout', dependencies=[Depends(auth)])
    def logout(request: Request):
        db.execute('DELETE FROM sessions WHERE token=?', (digest(request.cookies.get('hikari_session', '')),))
        response = JSONResponse({'ok': True})
        response.delete_cookie('hikari_session')
        return response

    @app.get('/api/status', dependencies=[Depends(auth)])
    def status():
        task = db.one("SELECT id,chat,status,updated FROM tasks WHERE status NOT IN ('queued','completed','failed','interrupted','cancelled') ORDER BY created LIMIT 1")
        if not task and agent.active:
            task = db.one("SELECT id,chat,'remembering' AS status,updated FROM tasks WHERE id=? AND memory_status='reviewing'", (agent.active,))
        queued = db.one("SELECT count(*) AS n FROM tasks WHERE status='queued'")['n']
        return {'agent': task or {'status': 'idle'}, 'queued': queued, 'indexing': library.scanning,
                'last_scan': library.last_scan, 'index_error': library.error}

    @app.get('/api/chats', dependencies=[Depends(auth)])
    def chats():
        return db.rows('SELECT * FROM chats ORDER BY created DESC')

    @app.post('/api/chats', dependencies=[Depends(auth)])
    def new_chat(data: ChatInput):
        cid = uid()
        db.execute('INSERT INTO chats VALUES(?,?,?,?)', (cid, data.title, data.scope, time.time()))
        return {'id': cid}

    def chat_exists(cid):
        row = db.one('SELECT * FROM chats WHERE id=?', (cid,))
        if not row:
            raise HTTPException(404, '聊天不存在')
        return row

    @app.put('/api/chats/{cid}', dependencies=[Depends(auth)])
    def update_chat(cid: str, data: ChatInput):
        chat_exists(cid)
        db.execute('UPDATE chats SET title=?,scope=? WHERE id=?', (data.title, data.scope.strip('/'), cid))
        return {'ok': True}

    @app.delete('/api/chats/{cid}', dependencies=[Depends(auth)])
    def delete_chat(cid: str):
        with db.tx() as c:
            if c.execute("SELECT id FROM tasks WHERE chat=? AND memory_status='reviewing'", (cid,)).fetchone():
                raise ValueError('聊天正在整理记忆，请稍后再删除')
            if c.execute("SELECT id FROM tasks WHERE chat=? AND status NOT IN ('completed','failed','interrupted','cancelled')", (cid,)).fetchone():
                raise ValueError('聊天还有待处理任务，请完成或取消后再删除')
            # Evidence snapshots can still be referenced by long-term memories and saved notes.
            c.execute('DELETE FROM tasks WHERE chat=?', (cid,))
            c.execute('DELETE FROM messages WHERE chat=?', (cid,))
            c.execute('DELETE FROM chats WHERE id=?', (cid,))
        return {'ok': True, 'notice': '聊天已删除，长期记忆仍保留'}

    @app.get('/api/chats/{cid}', dependencies=[Depends(auth)])
    def chat(cid: str):
        row = chat_exists(cid)
        return row | {'messages': db.rows('SELECT * FROM messages WHERE chat=? ORDER BY created,id', (cid,)),
                      'tasks': db.rows('SELECT * FROM tasks WHERE chat=? ORDER BY created,id', (cid,))}

    @app.post('/api/chats/{cid}/messages', dependencies=[Depends(auth)])
    def enqueue(cid: str, data: MessageInput):
        chat = chat_exists(cid)
        if not data.content.strip():
            raise ValueError('消息不能为空')
        tid, now = uid(), time.time()
        with db.tx() as c:
            old = c.execute('SELECT * FROM tasks WHERE request_id=?', (data.request_id,)).fetchone()
            if old:
                if old['chat'] != cid or old['prompt'] != data.content:
                    raise ValueError('请求标识已用于另一条消息')
                return {'id': old['id']}
            c.execute('INSERT INTO tasks(id,chat,prompt,status,request_id,created,updated,scope) VALUES(?,?,?,?,?,?,?,?)',
                      (tid, cid, data.content, 'queued', data.request_id, now, now, chat['scope']))
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), cid, 'user', data.content, tid, now))
            if chat['title'] == '新对话':
                c.execute('UPDATE chats SET title=? WHERE id=?', (data.content[:30], cid))
        agent.wake.set()
        return {'id': tid}

    @app.post('/api/tasks/{tid}/retry', dependencies=[Depends(auth)])
    def retry(tid: str):
        # A new request avoids replaying uncertain old writes; show an explicit new user turn.
        row = db.one("SELECT * FROM tasks WHERE id=? AND status IN ('failed','interrupted')", (tid,))
        if not row:
            raise ValueError('任务不可重试')
        return enqueue(row['chat'], MessageInput(content=row['prompt'], request_id=uid()))

    @app.post('/api/tasks/{tid}/dismiss', dependencies=[Depends(auth)])
    def dismiss(tid: str):
        with db.tx() as c:
            row = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise HTTPException(404, '任务不存在')
            if row['status'] not in ('failed', 'interrupted'):
                raise ValueError('只能关闭失败或中断的任务提示')
            c.execute('UPDATE tasks SET dismissed=1 WHERE id=?', (tid,))
        return {'ok': True}

    @app.post('/api/tasks/{tid}/cancel', dependencies=[Depends(auth)])
    def cancel(tid: str):
        with db.tx() as c:
            row = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row or row['status'] != 'queued':
                raise ValueError('只能取消尚未执行的排队任务')
            c.execute("UPDATE tasks SET status='cancelled',updated=? WHERE id=?", (time.time(), tid))
        return {'ok': True}

    @app.get('/api/documents', dependencies=[Depends(auth)])
    def documents():
        return library.catalog()

    @app.post('/api/documents/refresh', dependencies=[Depends(auth)])
    def refresh():
        # Force retries of previously failed files, without hiding errors until work starts.
        db.execute("UPDATE documents SET version='' WHERE status='error'")
        library.refresh.set()
        return {'ok': True}

    @app.post('/api/documents/upload', dependencies=[Depends(auth)])
    async def upload(file: UploadFile = File(...), folder: str = Form('')):
        name = (file.filename or '').replace('\\', '/')
        if '/' in name or Path(name).suffix.lower() not in SUPPORTED or name.startswith('.'):
            raise ValueError('仅支持 PDF、PPTX、Markdown、DOCX 文件名')
        rel = folder.strip('/') + '/' + name if folder.strip('/') else name
        target = within(library.root, rel)
        staged = cfg.home / 'staging' / uid()
        total = 0
        try:
            with staged.open('wb') as f:
                while block := await file.read(1024 * 1024):
                    total += len(block)
                    if total > cfg.get('max_upload_mb') * 1024 * 1024:
                        raise ValueError('上传超过文件大小上限')
                    f.write(block)
            with library.lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # Exclusive publication protects existing files, including concurrent uploads.
                    os.link(staged, target)
                except FileExistsError:
                    raise ValueError('同名文件已存在，请改名后上传') from None
                except OSError as exc:
                    # A configured knowledge folder may be on another Windows volume.
                    if getattr(exc, 'winerror', None) != 17 and exc.errno != 18:
                        raise
                    try:
                        with target.open('xb') as destination, staged.open('rb') as source:
                            shutil.copyfileobj(source, destination)
                    except FileExistsError:
                        raise ValueError('同名文件已存在，请改名后上传') from None
            library.refresh.set()
            return {'ok': True, 'path': rel}
        finally:
            staged.unlink(missing_ok=True)
            await file.close()

    @app.get('/api/documents/{doc}/content', dependencies=[Depends(auth)])
    def content(doc: str, position: int = 1):
        row = db.one('SELECT * FROM documents WHERE id=?', (doc,))
        if not row or not library.current(doc, row['version']):
            raise HTTPException(409, '原件已变化或索引不可用')
        return row | {'chunks': db.rows('SELECT position,label,text FROM chunks WHERE doc=? AND position=? ORDER BY id', (doc, position))}

    @app.get('/api/documents/{doc}/file', dependencies=[Depends(auth)])
    def original(doc: str, version: str = ''):
        row = db.one('SELECT * FROM documents WHERE id=?', (doc,))
        if not row or not library.current(doc, version or row['version']):
            raise HTTPException(409, '原件已变化或不可用，不能用新版本替代旧来源')
        path = within(library.root, row['path'])
        return FileResponse(path, media_type='application/pdf' if path.suffix.lower() == '.pdf' else 'application/octet-stream',
                            filename=path.name, content_disposition_type='inline' if path.suffix.lower() == '.pdf' else 'attachment')

    @app.get('/api/sources/{eid}', dependencies=[Depends(auth)])
    def source(eid: str):
        row = db.one('SELECT * FROM evidence WHERE id=?', (eid,))
        if not row:
            raise HTTPException(404, '来源不存在')
        valid = library.current(row['doc'], row['version'])
        row['current'] = valid
        return row

    @app.get('/api/memories', dependencies=[Depends(auth)])
    def memories():
        return memory.list()

    @app.put('/api/memories', dependencies=[Depends(auth)])
    def edit_memory(data: MemoryInput):
        return memory.write(data.path, data.content, 'user', data.expected_hash)

    @app.delete('/api/memories/{mid}', dependencies=[Depends(auth)])
    def delete_memory(mid: str, expected_hash: str):
        return memory.delete(mid, expected_hash)

    @app.get('/api/revisions', dependencies=[Depends(auth)])
    def revisions():
        return db.rows('SELECT r.*,m.path FROM revisions r JOIN memories m ON m.id=r.memory ORDER BY r.created DESC LIMIT 100')

    @app.post('/api/revisions/{rid}/undo', dependencies=[Depends(auth)])
    def undo(rid: str):
        memory.undo(rid)
        return {'ok': True}

    @app.get('/api/settings', dependencies=[Depends(auth)])
    def settings():
        return cfg.public()

    @app.put('/api/settings', dependencies=[Depends(auth)])
    def save_settings(data: SettingsInput):
        if agent.active or db.one("SELECT id FROM tasks WHERE status='queued'"):
            raise ValueError('请等待任务处理完毕后修改模型设置')
        url = urlsplit(data.base_url)
        if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError('模型地址必须为有效 HTTPS 地址，不能含凭证、查询或片段')
        root = Path(data.knowledge).expanduser().resolve()
        if not root.is_absolute() or root == root.parent or root == cfg.home or root.is_relative_to(cfg.home / 'memory'):
            raise ValueError('请选择独立的资料目录')
        oldroot = library.root
        with library.lock:
            if root != oldroot and db.one("SELECT id FROM tasks WHERE status NOT IN ('completed','failed','interrupted','cancelled')"):
                raise ValueError('有任务待处理，暂不能切换资料目录')
            root.mkdir(parents=True, exist_ok=True)
            values = data.model_dump(exclude_none=True)
            if values.get('api_key') == '':
                values.pop('api_key')
            values['knowledge'] = str(root)
            if root != oldroot:
                raise ValueError('第一版暂不在网页切换已有资料根目录；请停服后在本机配置，避免旧来源指向新文件')
            cfg.save(values)
        library.refresh.set()
        return cfg.public()

    @app.post('/api/model/test', dependencies=[Depends(auth)])
    def test_model():
        try:
            response = agent.call_model([{'role': 'user', 'content': '请只回复：连接成功'}], [], 45)
            return {'ok': True, 'model': cfg.get('model'), 'reply': response.get('content', '')[:100]}
        except Exception as e:
            raise ValueError(f'连接验证失败（{type(e).__name__}），请检查密钥、模型地址、额度和网络') from None

    @app.get('/api/generated', dependencies=[Depends(auth)])
    def generated():
        return [p.name for p in (cfg.home / 'generated').glob('*.md')]

    @app.get('/api/generated/{filename}', dependencies=[Depends(auth)])
    def generated_file(filename: str):
        path = within(cfg.home / 'generated', filename)
        if not path.is_file() or path.suffix != '.md':
            raise HTTPException(404, '文件不存在')
        return FileResponse(path, filename=path.name)

    @app.post('/api/backup', dependencies=[Depends(auth)])
    def backup():
        if agent.active or db.one("SELECT id FROM tasks WHERE status='queued'"):
            raise ValueError('请等待任务处理完毕后备份')
        filename = 'hikari-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uid()[:6] + '.zip'
        path = cfg.home / 'backups' / filename
        snapshot = cfg.home / 'staging' / (uid() + '.sqlite3')
        with agent.execution_lock, library.lock, memory.lock, db.lock:
            if agent.active or db.one("SELECT id FROM tasks WHERE status NOT IN ('completed','failed','interrupted','cancelled')"):
                raise ValueError('请等待任务处理完毕后备份')
            try:
                with closing(sqlite3.connect(db.path)) as src, closing(sqlite3.connect(snapshot)) as dst:
                    src.backup(dst)
                    dst.execute('DELETE FROM sessions')
                    dst.commit()
                with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
                    z.write(snapshot, 'app.sqlite3')
                    for folder in ['memory', 'generated']:
                        for file in (cfg.home / folder).rglob('*'):
                            if file.is_file() and file.resolve().is_relative_to((cfg.home / folder).resolve()):
                                z.write(file, file.relative_to(cfg.home).as_posix())
                    z.writestr('RESTORE.txt', '停服后恢复 app.sqlite3、memory、generated；资料原件及含密钥的 config.json 请在主机另外备份。恢复前保留当前数据副本。')
            finally:
                snapshot.unlink(missing_ok=True)
        return {'filename': filename}

    @app.get('/api/backups/{filename}', dependencies=[Depends(auth)])
    def backup_file(filename: str):
        path = within(cfg.home / 'backups', filename)
        if not path.is_file() or path.suffix != '.zip':
            raise HTTPException(404, '备份不存在')
        return FileResponse(path, filename=filename)

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web' / 'index.html', headers={'Cache-Control': 'no-cache'})

    @app.get('/sw.js')
    def sw():
        return FileResponse(ROOT / 'web' / 'sw.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})

    return app
