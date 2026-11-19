"""Decision Workbench action for one already-paired student computer."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from connect_students import verify_mcp
from device_dashboard import DATA, DEVICE_ID, load_json, paired_devices, save_json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(target):
    address, port, token = target
    # Send the existing credential directly to the paired Tailscale destination.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    verify_mcp(f'http://{address}:{port}/mcp', token, open_request=opener.open)


def check(tailscale, data_root, host):
    try:
        raw = subprocess.check_output([str(tailscale), 'status', '--json'], timeout=10,
                                      stderr=subprocess.DEVNULL)
        devices, targets = paired_devices(json.loads(raw), load_json(data_root / 'universal.json'), data_root)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
        return 'unknown', '无法从当前 Tailscale 和本机配对资料确认设备状态。'

    device = next((row for row in devices if row['hostName'] == host), None)
    if device is None:
        return 'unknown', '当前 Tailscale 目录未返回这台已配对的学生电脑，在线状态待确认。'
    if not device['online']:
        seen = device.get('lastSeen')
        last_seen = (datetime.fromisoformat(seen).astimezone(timezone(timedelta(hours=8)))
                     .strftime('%Y-%m-%d %H:%M:%S')) if seen else None
        detail = '学生电脑在 Tailscale 上离线。'
        if last_seen:
            detail += f'最近一次在线为北京时间 {last_seen}。'
        return 'completed', detail + '在线后才能验证远程协助连接。'

    try:
        probe(targets[host])
    except HTTPError as error:
        error.close()
        return 'failed', f'学生电脑在 Tailscale 上在线，远程协助握手返回 HTTP {error.code}。'
    except (OSError, RuntimeError, ValueError):
        return 'failed', '学生电脑在 Tailscale 上在线，远程协助服务未返回有效的认证握手。'
    return 'completed', '学生电脑在 Tailscale 上在线，远程协助服务的认证握手成功。'


def run(request, tailscale, data_root, host):
    if (not isinstance(request, dict) or set(request) != {'operationId', 'text', 'fields'}
            or not isinstance(request['operationId'], str) or not 0 < len(request['operationId']) <= 200
            or request['text'] != '' or request['fields'] != {}):
        raise ValueError('此快捷操作只接受无录音的检查请求。')
    if not tailscale.is_absolute() or not data_root.is_absolute() or not DEVICE_ID.fullmatch(host):
        raise ValueError('本机快捷操作的程序路径或配对设备编号无效。')

    identity = hashlib.sha256(request['operationId'].encode('utf-8')).hexdigest()
    receipt_path = data_root / 'connection-checks' / f'{identity}.json'
    if receipt_path.exists():
        receipt = load_json(receipt_path)
        if receipt['device'] != host:
            raise ValueError('该操作编号对应另一台设备。')
        return receipt

    state, detail = check(tailscale, data_root, host)
    receipt = {'state': state, 'detail': detail, 'device': host,
               'checkedAt': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    save_json(receipt_path, receipt)
    return load_json(receipt_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tailscale', required=True, type=Path)
    parser.add_argument('--host', required=True)
    args = parser.parse_args()
    try:
        receipt = run(json.load(sys.stdin), args.tailscale, DATA, args.host)
    except json.JSONDecodeError:
        receipt = {'state': 'failed', 'detail': '快捷操作输入或本机回执格式无效。'}
    except ValueError as error:
        receipt = {'state': 'failed', 'detail': str(error)}
    except (OSError, KeyError, TypeError):
        receipt = {'state': 'unknown', 'detail': '无法写入或读取本次连接检查的回执。'}
    print(json.dumps({key: receipt[key] for key in ('state', 'detail')}))


if __name__ == '__main__':
    main()
