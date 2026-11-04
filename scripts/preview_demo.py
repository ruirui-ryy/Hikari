"""Isolated browser QA server: synthetic materials and a deterministic model, no API calls."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop('DEEPSEEK_API_KEY', None)
from hikari.app import create_app
from hikari.core import ROOT, password_hash
import uvicorn

app = create_app(ROOT / 'local' / 'preview-data')
app.state.demo = True
app.state.cfg.save({'username': 'preview', 'password': password_hash('local-preview-only')})
sample = app.state.library.root / '线性代数' / '向量与变换.md'
sample.parent.mkdir(parents=True, exist_ok=True)
sample.write_text('# 特征向量\n\n对于矩阵 A，若非零向量 v 满足 Av = λv，则 v 是特征向量，λ 是对应的特征值。变换后向量仍在同一条直线上。\n\n当 λ 为负数时方向反转，λ 为零时向量映射为零向量。', encoding='utf-8')


def model(messages, tools, timeout):
    if messages[-1]['role'] != 'tool':
        name, args = 'search', {'query': '特征向量'}
    else:
        result = json.loads(messages[-1]['content'])
        if isinstance(result, list) and result and 'citation' in result[0]:
            ev = result[0]
            return {'role': 'assistant', 'content': '特征向量可以理解为矩阵变换中**仍留在同一条直线上的非零向量**。\n\n它满足：\n\n$$Av = \\lambda v$$\n\n其中 $\\lambda$ 决定缩放程度；当它为负数时，方向会反转。' + ev['citation'] + '\n\n> 这是合成资料的界面演示。实际问答将由 DeepSeek 生成。'}
        hits = [h for h in result if 'Av' in h.get('text', '')] or result
        name, args = 'read', {'doc': hits[0]['doc'], 'position': hits[0]['position']}
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-demo', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}


app.state.agent.model = model
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8766, proxy_headers=False, access_log=False)
