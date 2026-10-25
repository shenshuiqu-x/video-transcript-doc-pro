#!/usr/bin/env python3
"""Install a private, reproducible media runtime. Compatible with Python 3.7 bootstrap."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    child_env = dict(os.environ)
    # macOS Python 3.7 leaks its launcher path into newer child interpreters.
    for key in ('__PYVENV_LAUNCHER__', 'PYTHONHOME'):
        child_env.pop(key, None)
    candidates = [os.environ.get('VIDEO_TRANSCRIPT_PYTHON'),
                  str(Path.home() / '.workbuddy/binaries/python/envs/default/bin/python'),
                  str(Path.home() / '.local/bin/python3.12'), sys.executable]
    runtime = None
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        p = subprocess.run([candidate, '-c', 'import sys; assert sys.version_info >= (3, 10)'],
                           capture_output=True, env=child_env)
        if p.returncode == 0:
            runtime = candidate
            break
    if not runtime:
        raise SystemExit('需要 Python >= 3.10；可用 VIDEO_TRANSCRIPT_PYTHON 指定。')
    env = ROOT / '.venv'
    python = env / 'bin/python'
    uv = shutil.which('uv')
    if not uv and (Path.home() / '.local/bin/uv').exists():
        uv = str(Path.home() / '.local/bin/uv')
    if not python.exists():
        subprocess.run([uv, '--no-config', 'venv', '--python', runtime, str(env)] if uv else
                       [runtime, '-m', 'venv', str(env)], check=True, env=child_env)
    # Explicit prefix avoids inherited WorkBuddy uv configuration targeting /install.
    cmd = [uv, '--no-config', 'pip', 'install', '--python', str(python), '--prefix', str(env)] if uv else [str(python), '-m', 'pip', 'install']
    subprocess.run(cmd + ['-r', str(ROOT / 'requirements.txt')], check=True, env=child_env)
    subprocess.run([str(python), str(ROOT / 'scripts/pipeline.py'), 'doctor'], check=True, env=child_env)
    print(json.dumps({'python': str(python), 'skill': str(ROOT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
