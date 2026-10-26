"""Explicit real-API smoke test. Sends only synthetic text; never prints credentials."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hikari.app import create_app
from hikari.core import uid
import time
import tempfile


def main():
    app = create_app(background=False)
    cfg, db, lib, agent = app.state.cfg, app.state.db, app.state.library, app.state.agent
    if not cfg.get('api_key'):
        print('未配置模型密钥，跳过真实 API 测试。')
        return 2
    try:
        reply = agent.call_model([{'role': 'user', 'content': '请只回复：连接成功'}], [], 40)
        print(json.dumps({'connected': True, 'model': cfg.get('model'), 'has_reply': bool(reply.get('content'))}, ensure_ascii=False))
    except Exception as e:
        import httpx
        code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        print(json.dumps({'connected': False, 'error_type': type(e).__name__, 'http_status': code}, ensure_ascii=False))
        return 1
    if '--agent' in sys.argv:
        with tempfile.TemporaryDirectory(prefix='model-smoke-', dir=cfg.home / 'staging') as folder:
            trial = create_app(folder, background=False)
            trial.state.cfg.save({'max_tools': 12, 'max_seconds': 120})
            document = trial.state.library.root / '合成课程.md'
            document.write_text('# 合成课程\n\n测试定义：青叶变换会把每个输入数字乘以三，再加二。\n\n例如输入四，输出十四。此定义仅用于测试，不是通用数学术语。', encoding='utf-8')
            trial.state.library.scan()
            cid, tid, now = uid(), uid(), time.time()
            prompt = '请检索并阅读我的合成课程资料：青叶变换输入五时得到多少？请引用来源。'
            d = trial.state.db
            d.execute('INSERT INTO chats VALUES(?,?,?,?)', (cid, '合成验收', '', now))
            d.execute('INSERT INTO tasks(id,chat,prompt,status,request_id,created,updated,scope) VALUES(?,?,?,?,?,?,?,?)',
                      (tid, cid, prompt, 'queued', uid(), now, now, ''))
            d.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), cid, 'user', prompt, tid, now))
            try:
                trial.state.agent.run(d.one('SELECT * FROM tasks WHERE id=?', (tid,)))
                answer = d.one("SELECT content FROM messages WHERE role='assistant'")['content']
                passed = '/api/sources/' in answer and ('17' in answer or '十七' in answer)
                print(json.dumps({'agent_passed': passed, 'read_evidence': len(d.rows('SELECT id FROM evidence')),
                                  'memory_files': len(trial.state.memory.list())}, ensure_ascii=False))
                if not passed:
                    return 1
                follow, created = uid(), time.time()
                prompt2 = '继续刚才的青叶变换，输入三会得到什么？请核对原文并引用来源。'
                d.execute('INSERT INTO tasks(id,chat,prompt,status,request_id,created,updated,scope) VALUES(?,?,?,?,?,?,?,?)',
                          (follow, cid, prompt2, 'queued', uid(), created, created, ''))
                d.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), cid, 'user', prompt2, follow, created))
                trial.state.agent.run(d.one('SELECT * FROM tasks WHERE id=?', (follow,)))
                answer2 = d.one("SELECT content FROM messages WHERE task=? AND role='assistant'", (follow,))['content']
                follow_passed = '/api/sources/' in answer2 and ('11' in answer2 or '十一' in answer2)
                print(json.dumps({'followup_passed': follow_passed}, ensure_ascii=False))
                if not follow_passed:
                    return 1
            except Exception as e:
                print(json.dumps({'agent_passed': False, 'error_type': type(e).__name__}, ensure_ascii=False))
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
