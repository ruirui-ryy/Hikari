"""Isolated real web/API preview with fictional notes; no background agent."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop('DEEPSEEK_API_KEY', None)
from hikari.app import create_app
from hikari.core import ROOT, password_hash
from memory_samples import seed
import uvicorn

app = create_app(ROOT / 'local' / 'memory-preview', background=False)
app.state.demo = True
app.state.cfg.save(dict(username='preview', password=password_hash('local-preview-only')))
seed(app.state.memory.notes)

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8767, access_log=False, proxy_headers=False)
