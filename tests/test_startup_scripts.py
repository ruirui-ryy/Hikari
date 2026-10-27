import json
import subprocess

import httpx
import pytest

from hikari.__main__ import port_conflict_message
from scripts import tailscale_json


def test_utf8_tailscale_status_survives_legacy_console(capsys, monkeypatch):
    data = {'BackendState': 'Running', 'Self': {'DNSName': 'desktop.example.ts.net.'},
            'User': {'1': {'DisplayName': '中文测试用户'}}, 'Peer': {'private': {}}}
    def run(args, **kwargs):
        assert args == ['tailscale.exe', 'status', '--json']
        assert kwargs == {'capture_output': True, 'timeout': 15, 'check': True}
        return subprocess.CompletedProcess(args, 0, json.dumps(data, ensure_ascii=False).encode('utf-8'), b'')
    monkeypatch.setattr(subprocess, 'run', run)
    assert tailscale_json.main(['tailscale.exe', 'status']) == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert json.loads(output) == {'BackendState': 'Running', 'Self': {'DNSName': 'desktop.example.ts.net.'}}
    assert 'DisplayName' not in output and 'Peer' not in output


def test_serve_status_retains_conflict_and_funnel_fields(monkeypatch, capsys):
    data = {'TCP': {'443': {'HTTPS': True}}, 'Web': {'desktop:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:18765'}, '/笔记': {'Path': 'D:/笔记'}}}},
            'AllowFunnel': {'desktop:443': True}}
    def run(args, **kwargs):
        assert args[1:] == ['serve', 'status', '--json']
        return subprocess.CompletedProcess(args, 0, json.dumps(data, ensure_ascii=False).encode('utf-8'), b'')
    monkeypatch.setattr(subprocess, 'run', run)
    assert tailscale_json.main(['tailscale.exe', 'serve']) == 0
    output = capsys.readouterr().out
    assert output.isascii() and json.loads(output) == data


@pytest.mark.parametrize('failure', [ValueError('private JSON'), subprocess.TimeoutExpired('tailscale', 15),
                                   subprocess.CalledProcessError(1, 'tailscale', stderr=b'private details')])
def test_status_failure_is_short_and_does_not_dump_private_data(monkeypatch, capsys, failure):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(subprocess, 'run', run)
    assert tailscale_json.main(['tailscale.exe', 'status']) == 1
    output = capsys.readouterr()
    assert not output.out and 'private' not in output.err


@pytest.mark.parametrize('health,expected', [({'service': 'hikari', 'demo': False}, '已在运行'),
                                          ({'service': 'other', 'demo': False}, '其他程序'),
                                          ({'service': 'hikari', 'demo': True}, '其他程序'),
                                          ([], '其他程序')])
def test_startup_distinguishes_hikari_from_other_listeners(monkeypatch, health, expected):
    actual_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **options: actual_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=health))))
    message = port_conflict_message(18765)
    assert expected in message
    if expected == '已在运行':
        assert 'Ctrl+C' in message and '修改 port' not in message


def test_startup_probe_timeout_keeps_other_service_message(monkeypatch):
    actual_client = httpx.Client
    def timeout(request):
        raise httpx.ReadTimeout('no response')
    monkeypatch.setattr(httpx, 'Client', lambda **options: actual_client(transport=httpx.MockTransport(timeout)))
    assert '其他程序' in port_conflict_message(18765)
