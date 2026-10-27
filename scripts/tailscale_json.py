"""Keep UTF-8 native output out of Windows PowerShell's legacy decoding path."""
import json
import subprocess
import sys


def read_json(executable, mode):
    commands = {'status': ['status', '--json'], 'serve': ['serve', 'status', '--json']}
    if mode not in commands:
        raise ValueError('unsupported status command')
    result = subprocess.run([executable, *commands[mode]], capture_output=True, timeout=15, check=True)
    data = json.loads(result.stdout.decode('utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('status must be an object')
    if mode == 'status':
        # No peer inventory or account details need to reach the shell.
        data = {'BackendState': data.get('BackendState'), 'Self': {'DNSName': data.get('Self', {}).get('DNSName', '')}}
    return data


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    try:
        data = read_json(*args)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        print('Unable to read Tailscale status. Check that Tailscale is running and signed in.', file=sys.stderr)
        return 1
    # ASCII JSON survives legacy console encodings; ConvertFrom-Json restores Unicode.
    print(json.dumps(data, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
