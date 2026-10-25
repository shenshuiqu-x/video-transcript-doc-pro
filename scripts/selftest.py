#!/usr/bin/env python3
"""Offline regression tests. Local HTTP fixtures only; no real cookies, ASR or online documents."""
import functools
import http.server
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave

import pipeline as p
import transcript as t


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
        cls.tmp.cleanup()

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
        (task / 'source.media').write_bytes(b'old wrong course')
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
        (folder / 'master.m3u8').write_text('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\nchild.m3u8\n')
        task = self.task('hls-task')
        p.save(task / 'capture.json', {'media': [{'url': self.base + '/hls/master.m3u8', 'kind': 'hls'}], 'cookies': []})
        report = p.run_extract(task, p.load(task / 'manifest.json'), None, True, 1024**2)
        self.assertEqual(report['file'], 'audio.flac')
        self.assertAlmostEqual(report['decoded_duration'], 22, delta=.2)

    def test_hls_aes128_transport_streams(self):
        folder = self.root / 'encrypted'
        folder.mkdir()
        (folder / 'key.bin').write_bytes(bytes(range(16)))
        (folder / 'keyinfo.txt').write_text('key.bin\n' + str(folder / 'key.bin') + '\n' + '0' * 31 + '1\n')
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
        source.write_text('# 标题\n\n> **原话** <tag> {x}\n\n`**代码**`\n\n```python\nx = "**literal**"\n```\n')
        output = self.root / 'publish'
        t.render_mdx(source, output)
        result = (output / 'content.mdx').read_text()
        self.assertIn('> <Mark bold>原话</Mark>', result)
        self.assertIn('&lt;tag&gt; &#123;x&#125;', result)
        self.assertIn('`**代码**`', result)
        self.assertIn('x = "**literal**"', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
