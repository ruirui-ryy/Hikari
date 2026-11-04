"""Disposable mail UI preview. No real credentials, model calls or SMTP sockets."""
import json
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop('DEEPSEEK_API_KEY', None)
from hikari.app import create_app
from hikari.core import ROOT, password_hash
from hikari.mail import packed
import uvicorn


def model(messages, *_):
    context = json.loads(messages[-1]['content'])
    instruction = context.get('instruction', '')
    body = ('【模拟更新】' + instruction) if instruction else '王老师您好：\n\n谢谢您的通知。我已收到材料，会在讨论前仔细阅读。\n\n祝好！'
    return {'content': json.dumps({'category': 'draft', 'body': body, 'questions': []}, ensure_ascii=False)}


class MockMailbox:
    def fetch(self, service):
        return [], []

    def fetch_specific(self, service, ids):
        return []

    def send(self, payload, message_id, reply_to):
        # Success exists only in this local simulator.
        pass


preview_root = ROOT / 'local' / 'previews'
preview_root.mkdir(parents=True, exist_ok=True)
app = create_app(tempfile.mkdtemp(prefix='email-', dir=preview_root), model=model)
app.state.demo = True
app.state.cfg.save({'username': 'preview', 'password': password_hash('local-preview-only')})
mail = app.state.mail
mail.transport = MockMailbox()
mail.ingest([dict(id='<preview@example.org>', refs=[], sender='teacher@example.org', to=['preview@smail.nju.edu.cn'], cc=[],
                  received=time.time() + 1, subject='下周讨论的阅读材料', body='同学你好：\n\n请在下周讨论前阅读材料。收到后请回复确认。\n\n王老师', direction='in', attachments=[])])
mail.request_draft('<preview@example.org>')
mail.process_one(app.state.agent, manual_only=True)
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8767, access_log=False)
