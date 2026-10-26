"""Print the configured port only if the real, configured Hikari service answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from hikari.core import Config


def main():
    port = int(Config().get('port'))
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(f'http://127.0.0.1:{port}/api/health')
            response.raise_for_status()
            health = response.json()
        if health.get('service') != 'hikari' or health.get('demo') is not False or not health.get('configured'):
            raise ValueError('wrong service')
    except (httpx.HTTPError, ValueError):
        print(f'端口 {port} 上没有可用的正式 Hikari 服务。请先运行 scripts/start.ps1，并保持窗口运行。', file=sys.stderr)
        return 1
    print(port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
