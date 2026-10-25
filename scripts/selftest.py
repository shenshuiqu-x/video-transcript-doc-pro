#!/usr/bin/env python3
"""Offline regression tests. Local HTTP fixtures only; no real cookies, ASR or online documents."""
import functools
import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave

import pipeline as p
import transcript as t
import runtime as r


class Portability(unittest.TestCase):
    def test_broken_linux_bundle_cannot_pass_version_only_detection(self):
        bad = '/bundle/ffmpeg-linux-x86_64-v7.0.2'
        with patch.object(p.sys, 'platform', 'linux'), patch.object(p.shutil, 'which', return_value=None), \
             patch.dict(os.environ, {'VIDEO_TRANSCRIPT_FFMPEG': bad}), \
             patch.dict(sys.modules, {'imageio_ffmpeg': SimpleNamespace(get_ffmpeg_exe=lambda: bad)}), \
             patch.object(p.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'MPEG-TS'):
                p.ffmpeg()
            command.assert_not_called()

    def test_windows_runtime_and_browser_paths(self):
        with patch.object(r.sys, 'platform', 'win32'), patch.object(r.shutil, 'which', return_value=None), patch.dict(os.environ, {'LOCALAPPDATA': '/local'}):
            self.assertEqual(r.venv_python('/skill/.venv').as_posix(), '/skill/.venv/Scripts/python.exe')
            self.assertIn('/local/Microsoft/Edge/Application/msedge.exe', [Path(x).as_posix() for x in r.browser_candidates() if x])
            self.assertIn('creationflags', r.detached_options())
            with self.assertRaises(ValueError):
                r.isolated_profile('/local/Google/Chrome/User Data/Default')

    def test_linux_default_profile_is_protected(self):
        with patch.object(r.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_CONFIG_HOME': '/config'}):
            with self.assertRaises(ValueError):
                r.isolated_profile('/config/chromium/Default')
            self.assertEqual(r.isolated_profile('/dedicated-browser'), Path('/dedicated-browser').resolve())
            self.assertEqual(r.venv_python('/venv').as_posix(), '/venv/bin/python')
            self.assertTrue(r.detached_options()['start_new_session'])

    def test_explicit_executable_overrides_and_custom_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'custom-browser'
            exe.touch()
            with patch.dict(os.environ, {'VIDEO_TRANSCRIPT_BROWSER': str(exe), 'VIDEO_TRANSCRIPT_NODE': str(exe),
                                         'VIDEO_TRANSCRIPT_DATA_DIR': folder}):
                self.assertEqual(r.find_browser(), str(exe))
                self.assertEqual(r.find_node(), str(exe))
                self.assertEqual(r.data_root(), Path(folder))
                self.assertIsNone(r.find_browser(str(exe) + '-absent'))


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/denied.mp4':
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'<html>denied</html>')
        elif self.path == '/truncated.mp4':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'partial')
        elif self.headers.get('Range'):
            source = Path(self.translate_path(self.path))
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers['Range'])
            if not source.is_file() or not match:
                self.send_error(416)
                return
            data = source.read_bytes()
            start = int(match[1])
            end = min(len(data) - 1, int(match[2]) if match[2] else len(data) - 1)
            self.send_response(206)
            self.send_header('Content-Type', self.guess_type(str(source)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, len(data)))
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            self.wfile.write(data[start:end + 1])
        else:
            super().do_GET()


class Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='video-transcript-pro-test-')
        cls.root = Path(cls.tmp.name)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(cls.root)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%s' % cls.server.server_port
        cls.ff = p.ffmpeg()
        source = cls.root / 'tone.wav'
        with wave.open(str(source), 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            # Quiet PCM is sufficient to validate container/timestamp behavior.
            w.writeframes(b'\x10\x00' * (16000 * 22))
        cls.media = cls.root / 'tone.m4a'
        subprocess.run([cls.ff, '-v', 'error', '-y', '-i', str(source), '-c:a', 'aac', str(cls.media)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        # Browser.close acknowledges shutdown before helper processes release all files.
        deadline = time.monotonic() + 15
        while True:
            try:
                cls.tmp.cleanup()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.25)

    def task(self, name):
        folder = self.root / name
        folder.mkdir()
        p.save(folder / 'manifest.json', {'version': 1, 'url': self.base + '/tone.m4a', 'stage': 'captured'})
        return folder

    def test_http_failure_and_partial_never_become_completed_files(self):
        for remote in ('denied.mp4', 'truncated.mp4'):
            output = self.root / (remote + '.result')
            with self.assertRaises(Exception):
                p.download(p.client([], True), self.base + '/' + remote, {}, output, 1000)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + '.part').exists())

    def test_cookie_scope(self):
        opener = p.client([
            {'name': 'other', 'value': 'fake', 'domain': '.other.invalid', 'path': '/', 'secure': False},
            {'name': 'secure', 'value': 'fake', 'domain': '127.0.0.1', 'path': '/', 'secure': True},
            {'name': 'allowed', 'value': 'fake', 'domain': '127.0.0.1', 'path': '/', 'secure': False},
            {'name': 'wrong_path', 'value': 'fake', 'domain': '127.0.0.1', 'path': '/private', 'secure': False},
        ], True)
        jar = next(x.cookiejar for x in opener.handlers if isinstance(x, urllib.request.HTTPCookieProcessor))
        req = urllib.request.Request(self.base + '/tone.m4a')
        jar.add_cookie_header(req)
        self.assertEqual(req.get_header('Cookie'), 'allowed=fake')
        self.assertNotIn('Authorization', p.scoped_headers({'Authorization': 'synthetic'}, 'https://a.invalid', 'https://b.invalid'))

    def test_file_extract_replaces_stale_nonempty_source(self):
        task = self.task('file')
        (task / 'source.media').write_bytes(b'old wrong video')
        p.save(task / 'capture.json', {'media': [{'url': self.base + '/tone.m4a', 'kind': 'file'}], 'cookies': [], 'videos': []})
        state = p.load(task / 'manifest.json')
        report = p.run_extract(task, state, None, True, 1024**2)
        self.assertTrue(report['validated'])
        self.assertAlmostEqual(report['decoded_duration'], 22, delta=.1)
        self.assertEqual(p.sha(task / 'source.media'), p.sha(self.media))
        with self.assertRaises(ValueError):
            p.validate_audio(task / report['file'], 30)
        p.save(task / 'capture.json', {'media': [{'url': self.base + '/tone.m4a', 'kind': 'file'}],
                                     'videos': [{'url': 'blob:synthetic', 'duration': 30}], 'cookies': []})
        with self.assertRaises(ValueError):
            p.run_extract(task, state, None, True, 1024**2)
        with self.assertRaises(ValueError):
            t.audio_state(task)

    def test_hls_master_and_fragmented_mp4(self):
        folder = self.root / 'hls'
        folder.mkdir()
        subprocess.run([self.ff, '-v', 'error', '-y', '-i', str(self.media), '-c:a', 'copy',
                        '-f', 'hls', '-hls_time', '5', '-hls_list_size', '0', '-hls_segment_type', 'fmp4', '-hls_flags', 'single_file',
                        str(folder / 'child.m3u8')], check=True, capture_output=True)
        (folder / 'master.m3u8').write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\nchild.m3u8\n', encoding='utf-8')
        task = self.task('hls-task')
        p.save(task / 'capture.json', {'media': [{'url': self.base + '/hls/master.m3u8', 'kind': 'hls'}], 'cookies': []})
        report = p.run_extract(task, p.load(task / 'manifest.json'), None, True, 1024**2)
        self.assertEqual(report['file'], 'audio.flac')
        self.assertAlmostEqual(report['decoded_duration'], 22, delta=.2)

    def test_hls_aes128_transport_streams(self):
        folder = self.root / 'encrypted'
        folder.mkdir()
        (folder / 'key.bin').write_bytes(bytes(range(16)))
        (folder / 'keyinfo.txt').write_text('key.bin\n' + (folder / 'key.bin').as_posix() + '\n' + '0' * 31 + '1\n', encoding='utf-8')
        subprocess.run([self.ff, '-v', 'error', '-y', '-i', str(self.media), '-c:a', 'copy',
                        '-f', 'hls', '-hls_time', '5', '-hls_list_size', '0',
                        '-hls_key_info_file', str(folder / 'keyinfo.txt'), str(folder / 'index.m3u8')],
                       check=True, capture_output=True)
        task = self.task('aes-task')
        p.save(task / 'capture.json', {'media': [{'url': self.base + '/encrypted/index.m3u8', 'kind': 'hls'}], 'cookies': []})
        report = p.run_extract(task, p.load(task / 'manifest.json'), None, True, 1024**2)
        self.assertAlmostEqual(report['decoded_duration'], 22, delta=.2)

    def test_asr_requires_all_chunks_and_keeps_offsets(self):
        task = self.task('asr')
        audio = task / 'audio.m4a'
        audio.write_bytes(self.media.read_bytes())
        report = p.validate_audio(audio, 22)
        state = p.load(task / 'manifest.json')
        state['audio'] = {**report, 'file': 'audio.m4a'}
        p.save(task / 'manifest.json', state)
        p.chunks(task, state, 12, 2)
        rows = p.load(task / 'chunks.json')['items']
        for i, row in enumerate(rows):
            canonical = task / ('input-%d.json' % i)
            p.save(canonical, {'segments': [{'start': 1, 'end': 2, 'text': '原话%d' % i}]})
            t.import_asr(task, canonical, canonical, 'synthetic', i)
            if i == 0:
                with self.assertRaises(FileNotFoundError):
                    t.assemble(task)
        t.assemble(task)
        result = p.load(task / 'transcript.raw.json')
        self.assertEqual(len(result['segments']), len(rows))
        self.assertEqual(result['segments'][1]['start'], rows[1]['offset'] + 1)
        (task / state['chunks']['items'][0]['file']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            t.assemble(task)

    def test_mdx_formats_bold_but_preserves_code_and_escapes_jsx(self):
        source = self.root / 'draft.md'
        source.write_text('# 标题\n\n> **原话** <tag> {x}\n\n`**代码**`\n\n```python\nx = "**literal**"\n```\n', encoding='utf-8')
        output = self.root / 'publish'
        t.render_mdx(source, output)
        result = (output / 'content.mdx').read_text(encoding='utf-8')
        self.assertIn('> <Mark bold>原话</Mark>', result)
        self.assertIn('&lt;tag&gt; &#123;x&#125;', result)
        self.assertIn('`**代码**`', result)
        self.assertIn('x = "**literal**"', result)

    @unittest.skipUnless(os.environ.get('VIDEO_TRANSCRIPT_BROWSER_TEST') == '1', 'opt-in installed-browser test')
    def test_real_browser_iframe_capture_and_extract(self):
        (self.root / 'video.html').write_text('<title>Synthetic video</title><iframe src="/player.html"></iframe>', encoding='utf-8')
        (self.root / 'player.html').write_text('<audio src="/tone.wav" controls></audio>', encoding='utf-8')
        profile = self.root / 'browser-profile'
        info = json.loads(subprocess.check_output([sys.executable, str(p.ROOT / 'scripts/browser.py'),
                          '--profile', str(profile), '--headless', '--direct'], text=True))
        node = r.find_node()
        status = r.playwright_status(node, p.ROOT)
        try:
            task = self.task('browser-task')
            subprocess.run([node, str(p.ROOT / 'scripts/capture.js'), '--cdp', info['cdp'],
                            '--url', self.base + '/video.html', '--out', str(task / 'capture.json'), '--wait', '5'],
                           check=True, capture_output=True, text=True, timeout=45)
            capture = p.load(task / 'capture.json')
            self.assertTrue(any(row['url'] == self.base + '/tone.wav' for row in capture['media']))
            report = p.run_extract(task, p.load(task / 'manifest.json'), None, True, 1024**2)
            self.assertAlmostEqual(report['decoded_duration'], 22, delta=.1)
        finally:
            # Only close the dedicated test browser; never touch a user's browser.
            close = "const p=require(process.argv[1]);(async()=>{const b=await p.chromium.connectOverCDP(process.argv[2]);const gone=new Promise(r=>b.once('disconnected',r));const s=await b.newBrowserCDPSession();await s.send('Browser.close').catch(()=>{});await gone;})().then(()=>process.exit(0)).catch(()=>process.exit(1));"
            subprocess.run([node, '-e', close, status['module'], info['cdp']], timeout=15, capture_output=True, check=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
