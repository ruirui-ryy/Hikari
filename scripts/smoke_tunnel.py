"""Expose a temporary synthetic health endpoint, verify HTTPS, then close it."""
import json
import os
import queue
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

marker = 'hikari-connection-test-' + secrets.token_hex(6)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(marker.encode())

    def log_message(self, *args):
        pass


def main():
    binary = Path(__file__).resolve().parent.parent / 'tools' / 'cloudflared.exe'
    if not binary.exists():
        print(json.dumps({'passed': False, 'reason': 'connector_not_installed'}))
        return 1
    server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = subprocess.Popen([str(binary), '--no-autoupdate', 'tunnel', '--protocol', 'http2', '--url',
                                f'http://127.0.0.1:{server.server_port}'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    lines = queue.Queue()
    def output():
        for raw in iter(process.stdout.readline, b''):
            lines.put(raw.decode('utf-8', errors='replace'))
    threading.Thread(target=output, daemon=True).start()
    url, success = None, False
    deadline = time.monotonic() + 80
    last_attempt = 0
    try:
        with httpx.Client(timeout=8, follow_redirects=True) as client:
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    line = lines.get(timeout=.2)
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                    if match:
                        url = match.group()
                except queue.Empty:
                    pass
                if url and time.monotonic() - last_attempt > 3:
                    last_attempt = time.monotonic()
                    try:
                        r = client.get(url)
                        if r.status_code == 200 and r.text == marker:
                            success = True
                            break
                    except httpx.HTTPError:
                        pass
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
        server.shutdown()
        server.server_close()
    print(json.dumps({'passed': success, 'hostname_assigned': bool(url), 'temporary_endpoint_closed': True}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
