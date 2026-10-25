#!/usr/bin/env python3
"""Install a private, reproducible media runtime. Compatible with Python 3.7 bootstrap."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from runtime import find_node, playwright_status, venv_python

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-only', action='store_true', help='仅安装音频处理依赖，跳过 Node/Playwright')
    args = parser.parse_args()
    child_env = dict(os.environ)
    # macOS Python 3.7 leaks its launcher path into newer child interpreters.
    for key in ('__PYVENV_LAUNCHER__', 'PYTHONHOME'):
        child_env.pop(key, None)
    explicit = os.environ.get('VIDEO_TRANSCRIPT_PYTHON')
    candidates = [explicit] if explicit else [sys.executable] + [shutil.which(name) for name in
                  ('python3.14', 'python3.13', 'python3.12', 'python3', 'python')] + [
                  str(Path.home() / '.workbuddy/binaries/python/envs/default/bin/python'),
                  str(Path.home() / '.local/bin/python3.12')]
    if not explicit and sys.platform == 'win32' and shutil.which('py'):
        for version in ('3.14', '3.13', '3.12'):
            probe = subprocess.run(['py', '-' + version, '-c', 'import sys; print(sys.executable)'],
                                   capture_output=True, text=True, env=child_env)
            if probe.returncode == 0:
                candidates.append(probe.stdout.strip())
    runtime = None
    for candidate in candidates:
        if not candidate:
            continue
        candidate = shutil.which(candidate) or str(Path(candidate).expanduser())
        if not Path(candidate).is_file():
            continue
        p = subprocess.run([candidate, '-c', 'import sys; assert sys.version_info >= (3, 12)'],
                           capture_output=True, env=child_env)
        if p.returncode == 0:
            runtime = candidate
            break
    if not runtime:
        raise SystemExit('需要 Python >= 3.12（PyAV 19 的要求）；可用 VIDEO_TRANSCRIPT_PYTHON 指定。')
    env = ROOT / '.venv'
    python = venv_python(env)
    uv = shutil.which('uv')
    if not uv and (Path.home() / '.local/bin/uv').exists():
        uv = str(Path.home() / '.local/bin/uv')
    if not python.exists():
        subprocess.run([uv, '--no-config', 'venv', '--python', runtime, str(env)] if uv else
                       [runtime, '-m', 'venv', str(env)], check=True, env=child_env)
    # Explicit prefix avoids inherited WorkBuddy uv configuration targeting /install.
    cmd = [uv, '--no-config', 'pip', 'install', '--python', str(python), '--prefix', str(env)] if uv else [str(python), '-m', 'pip', 'install']
    subprocess.run(cmd + ['-r', str(ROOT / 'requirements.txt')], check=True, env=child_env)
    if not args.media_only:
        node = find_node()
        if not node:
            raise SystemExit('请安装 Node.js >=20（推荐 22 或更新 LTS），再运行 bootstrap；仅处理已有媒体可加 --media-only。')
        if not playwright_status(node, ROOT)['available']:
            # Invoke npm's JS entry directly: npm.cmd cannot be executed as an exe on Windows.
            npm = shutil.which('npm')
            entries = [Path(node).resolve().parent / 'node_modules/npm/bin/npm-cli.js']
            if npm:
                entries += [Path(npm).resolve(), Path(npm).parent / 'node_modules/npm/bin/npm-cli.js']
            npm_cli = next((x for x in entries if x.is_file() and x.name == 'npm-cli.js'), None)
            if not npm_cli:
                raise SystemExit('找不到 npm-cli.js；在技能目录手动运行 npm ci，再重试 bootstrap。')
            subprocess.run([node, str(npm_cli), 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                           cwd=str(ROOT), check=True, env=child_env)
        if not playwright_status(node, ROOT)['available']:
            raise SystemExit('Playwright 未就绪；Node 需 >=20，可用 VIDEO_TRANSCRIPT_NODE 指定。')
    subprocess.run([str(python), str(ROOT / 'scripts/pipeline.py'), 'doctor'], check=True, env=child_env)
    print(json.dumps({'python': str(python), 'skill': str(ROOT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
