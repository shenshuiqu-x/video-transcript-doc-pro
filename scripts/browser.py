#!/usr/bin/env python3
"""Start/reuse an isolated Chrome; never copy a profile or kill an existing browser."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from runtime import data_root, detached_options, find_browser, isolated_profile


def version(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:%d/json/version' % port, timeout=1) as res:
        return json.load(res)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', default=str(data_root() / 'browser'), help='专用配置目录；默认存于技能数据目录')
    p.add_argument('--chrome', help='Chrome / Edge / Chromium 可执行文件；默认自动发现')
    p.add_argument('--direct', action='store_true', help='明确需要直连时才关闭 Chrome 的代理')
    p.add_argument('--headless', action='store_true', help='无图形界面运行；首次交互登录时不要使用')
    args = p.parse_args()
    profile = isolated_profile(args.profile)
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
    chrome = find_browser(args.chrome)
    if not chrome:
        raise SystemExit('找不到 Chrome / Edge / Chromium；使用 --chrome 或 VIDEO_TRANSCRIPT_BROWSER 指定。')
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    cmd = [chrome, '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=%d' % port,
           '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check']
    if args.direct:
        cmd.append('--no-proxy-server')
    if args.headless:
        cmd.append('--headless=new')
    log = profile / 'transcript-browser.log'
    with open(log, 'ab') as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                **detached_options())
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
