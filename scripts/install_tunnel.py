"""Download official Windows x64 cloudflared, checking GitHub's release digest."""
import hashlib
import json
from pathlib import Path
import sys

import httpx

target_dir = Path(__file__).resolve().parent.parent / 'tools'
target_dir.mkdir(exist_ok=True)
with httpx.Client(follow_redirects=True, timeout=90) as client:
    release = client.get('https://api.github.com/repos/cloudflare/cloudflared/releases/latest')
    release.raise_for_status()
    metadata = release.json()
    asset = next(a for a in metadata['assets'] if a['name'] == 'cloudflared-windows-amd64.exe')
    expected = asset.get('digest', '')
    if not expected.startswith('sha256:'):
        raise SystemExit('官方发布未提供 SHA256，停止自动安装，请使用官方安装说明。')
    payload = client.get(asset['browser_download_url'])
    payload.raise_for_status()
    actual = hashlib.sha256(payload.content).hexdigest()
    if actual != expected.split(':', 1)[1]:
        raise SystemExit('下载校验失败，未安装。')
    target = target_dir / 'cloudflared.exe'
    target.write_bytes(payload.content)
    (target_dir / 'cloudflared-release.json').write_text(json.dumps({'release': metadata['tag_name'], 'sha256': actual,
        'url': asset['browser_download_url']}, indent=2), encoding='utf-8')
    print('已下载并验证 Cloudflare 官方版本：' + metadata['tag_name'])
