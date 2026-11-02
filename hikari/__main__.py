import getpass
import sys

from .core import Config, InstanceLock, password_hash


def port_conflict_message(port):
    import httpx
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f'http://127.0.0.1:{port}/api/health')
            response.raise_for_status()
            health = response.json()
        if health.get('service') == 'hikari' and health.get('demo') is False:
            return (f'Hikari 已在运行：http://127.0.0.1:{port}，无需重复启动。'
                    '如需加载代码更新，请在原启动窗口按 Ctrl+C 停止，再重新运行 scripts/start.ps1。')
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return f'端口 {port} 已被其他程序占用。请检查占用程序，或在 data/config.json 中修改 port 后重新启动。'


def main():
    cfg = Config()
    if len(sys.argv) > 1 and sys.argv[1] == 'email-setup':
        from .mail_transport import configure
        lock = InstanceLock(cfg.home / 'instance.lock')
        try:
            configure(cfg)
        finally:
            lock.close()
        return
    if len(sys.argv) > 1 and sys.argv[1] == 'setup':
        lock = InstanceLock(cfg.home / 'instance.lock')
        print('创建或重设 Hikari 个人账户（密码不会显示）')
        username = input('账户名 [owner]: ').strip() or 'owner'
        password = getpass.getpass('密码（至少 10 位）: ')
        if len(password) < 10 or password != getpass.getpass('再次输入密码: '):
            raise SystemExit('密码不足 10 位或两次不一致')
        cfg.save({'username': username, 'password': password_hash(password)})
        from .core import DB
        DB(cfg.home / 'app.sqlite3').execute('DELETE FROM sessions')
        lock.close()
        print('账户已保存。')
        return
    import uvicorn
    from .app import create_app
    port = int(cfg.get('port'))
    import socket
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    except OSError:
        raise SystemExit(port_conflict_message(port)) from None
    print(f'Hikari: http://127.0.0.1:{port}')
    if not cfg.get('password'):
        print('尚未创建账户，请先运行 python -m hikari setup')
    uvicorn.run(create_app(), host='127.0.0.1', port=port, proxy_headers=False, access_log=False)


if __name__ == '__main__':
    main()
