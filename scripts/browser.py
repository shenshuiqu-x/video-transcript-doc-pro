#!/usr/bin/env python3
"""Start/reuse an isolated Chrome; never copy a profile or kill an existing browser."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request


def version(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:%d/json/version' % port, timeout=1) as res:
        return json.load(res)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True, help='专用配置目录；可复用已有登录态的专用目录')
    p.add_argument('--chrome', default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    p.add_argument('--direct', action='store_true', help='明确需要直连时才关闭 Chrome 的代理')
    args = p.parse_args()
    profile = Path(args.profile).expanduser().resolve()
    default = Path.home() / 'Library/Application Support/Google/Chrome'
    if profile == default or default in profile.parents:
        raise SystemExit('不能调试日常 Chrome 的默认配置目录；使用专用目录。')
    active = profile / 'DevToolsActivePort'
    metadata = profile / 'transcript-cdp.json'
    ports = []
    for record in (metadata, active):
        try:
            ports.append(int(json.loads(record.read_text())['port']) if record == metadata else int(record.read_text().splitlines()[0]))
        except (OSError, ValueError, KeyError, IndexError):
            pass
    for port in ports:
        try:
            info = version(port)
            print(json.dumps({'cdp': 'http://127.0.0.1:%d' % port, 'reused': True,
                              'profile': str(profile), 'browser': info.get('Browser')}))
            return
        except (OSError, ValueError, IndexError):
            pass
    chrome = args.chrome if Path(args.chrome).exists() else shutil.which('google-chrome')
    if not chrome:
        raise SystemExit('找不到 Chrome；使用 --chrome 指定。')
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    cmd = [chrome, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=%d' % port,
           '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check']
    if args.direct:
        cmd.append('--no-proxy-server')
    log = profile / 'transcript-browser.log'
    with open(log, 'ab') as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                start_new_session=True)
    os.chmod(log, 0o600)
    for _ in range(40):
        try:
            info = version(port)
            metadata.write_text(json.dumps({'port': port, 'pid': proc.pid}), encoding='utf-8')
            os.chmod(metadata, 0o600)
            print(json.dumps({'cdp': 'http://127.0.0.1:%d' % port, 'reused': False,
                              'pid': proc.pid, 'profile': str(profile), 'browser': info.get('Browser')}))
            return
        except (OSError, ValueError):
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    raise SystemExit('Chrome 未就绪。检查 %s；若目录正在被占用，请连接原实例，不要杀进程。' % log)


if __name__ == '__main__':
    main()
