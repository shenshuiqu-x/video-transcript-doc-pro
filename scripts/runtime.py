"""Host-independent paths and executable discovery (bootstrap accepts Python 3.7)."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def venv_python(folder):
    return Path(folder) / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def data_root():
    return Path(os.environ.get('VIDEO_TRANSCRIPT_DATA_DIR', '~/.video-transcript-doc-pro')).expanduser()


def find_node():
    explicit = os.environ.get('VIDEO_TRANSCRIPT_NODE')
    if explicit:
        return shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
    found = shutil.which('node')
    if found:
        return found
    # Optional reuse of host-bundled tools; never required by the skill.
    bundled = Path.home() / '.workbuddy/binaries/node/versions'
    candidates = sorted(bundled.glob('*/bin/node')) + sorted(bundled.glob('*/node.exe'))
    return str(candidates[-1]) if candidates else None


def browser_candidates():
    home = Path.home()
    if sys.platform == 'win32':
        roots = [os.environ.get(k) for k in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')]
        relative = ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe',
                    'Chromium/Application/chrome.exe')
        paths = [str(Path(root) / part) for root in roots if root for part in relative]
    elif sys.platform == 'darwin':
        apps = ('Google Chrome.app/Contents/MacOS/Google Chrome', 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
                'Chromium.app/Contents/MacOS/Chromium')
        paths = [str(root / app) for root in (Path('/Applications'), home / 'Applications') for app in apps]
    else:
        paths = []
    return paths + [shutil.which(name) for name in
                    ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'msedge')]


def find_browser(explicit=None):
    override = explicit or os.environ.get('VIDEO_TRANSCRIPT_BROWSER')
    candidates = [override] if override else browser_candidates()
    for candidate in candidates:
        if candidate:
            resolved = shutil.which(candidate) or str(Path(candidate).expanduser())
            if Path(resolved).is_file():
                return resolved
    return None


def default_browser_profiles():
    home = Path.home()
    if sys.platform == 'win32':
        root = Path(os.environ.get('LOCALAPPDATA', str(home / 'AppData/Local')))
        return [root / part for part in ('Google/Chrome/User Data', 'Microsoft/Edge/User Data', 'Chromium/User Data')]
    if sys.platform == 'darwin':
        root = home / 'Library/Application Support'
        return [root / part for part in ('Google/Chrome', 'Microsoft Edge', 'Chromium')]
    root = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config')))
    return [root / part for part in ('google-chrome', 'google-chrome-beta', 'chromium', 'microsoft-edge')]


def isolated_profile(profile):
    profile = Path(profile).expanduser().resolve()
    for default in default_browser_profiles():
        default = default.resolve()
        # Also reject an ancestor: it could contain the everyday profile.
        if profile == default or default in profile.parents or profile in default.parents:
            raise ValueError('不能调试日常浏览器的默认配置目录；使用专用目录。')
    return profile


def detached_options():
    if sys.platform == 'win32':
        return {'creationflags': 0x00000008 | 0x00000200}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {'start_new_session': True}


def playwright_status(node, root):
    if not node:
        return {'available': False, 'error': '找不到 Node.js >=20'}
    result = subprocess.run([node, str(Path(root) / 'scripts/capture.js'), '--check'],
                            capture_output=True, text=True)
    if result.returncode:
        return {'available': False, 'error': result.stderr.strip()[-800:]}
    import json
    return dict(json.loads(result.stdout), available=True)
