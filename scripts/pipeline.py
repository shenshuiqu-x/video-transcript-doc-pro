#!/usr/bin/env python3
"""Task-isolated capture, authenticated download, standard remux and audio validation."""
import argparse
from contextlib import contextmanager
import hashlib
import http.cookiejar
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from runtime import data_root, find_browser, find_node, playwright_status

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.part')
    with open(tmp, 'w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def url_key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def http_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('媒体必须是无 URL 用户名密码的 HTTP(S) 地址。')
    return url


def clean_error(value):
    return re.sub(r'https?://[^\s\]\[<>"\']+', lambda m: urllib.parse.urlsplit(m[0]).netloc + '/[redacted]', str(value))[-1800:]


@contextmanager
def locked(task):
    lock = task / '.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('该任务正在处理，或上次异常留下 .lock。先检查锁中的 PID，不要并行改写同一任务。')
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def ffmpeg():
    candidates = [os.environ.get('VIDEO_TRANSCRIPT_FFMPEG'), shutil.which('ffmpeg')]
    broken_bundle = None
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        # This wheel's Linux x86_64 7.0.2 static build crashes on the MPEG-TS regression.
        if sys.platform.startswith('linux') and Path(bundled).name == 'ffmpeg-linux-x86_64-v7.0.2':
            broken_bundle = bundled
        else:
            candidates.append(bundled)
    except (ImportError, RuntimeError):
        pass
    for candidate in candidates:
        if not candidate:
            continue
        if broken_bundle and Path(candidate).resolve() == Path(broken_bundle).resolve():
            continue
        try:
            p = subprocess.run([candidate, '-version'], capture_output=True, text=True, timeout=15)
            if p.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    if broken_bundle:
        raise RuntimeError('imageio 的 Linux 7.0.2 静态构建无法通过 MPEG-TS 回归；请安装系统 FFmpeg（如 sudo apt install ffmpeg），或用 VIDEO_TRANSCRIPT_FFMPEG 指定可用构建。')
    raise RuntimeError('无可运行 FFmpeg；先执行 scripts/bootstrap.py，或设置 VIDEO_TRANSCRIPT_FFMPEG。')


class ScopedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        http_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
            redirected.remove_header('Cookie')
        return redirected


def client(cookies, direct=False):
    policy = http.cookiejar.DefaultCookiePolicy(strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain)
    jar = http.cookiejar.CookieJar(policy=policy)
    for c in cookies:
        # Partitioned cookies need a browser context; do not flatten their scope.
        if c.get('partitionKey'):
            continue
        domain = c.get('domain', '')
        if not domain or any('\n' in str(c.get(k, '')) or '\r' in str(c.get(k, '')) for k in ('name', 'value', 'domain', 'path')):
            continue
        expiry = c.get('expires', -1)
        jar.set_cookie(http.cookiejar.Cookie(0, c['name'], c['value'], None, False,
                       domain, domain.startswith('.'), domain.startswith('.'), c.get('path', '/'), True,
                       c.get('secure', False), int(expiry) if expiry and expiry > 0 else None,
                       not expiry or expiry <= 0, None, None, {}, False))
    proxy = urllib.request.ProxyHandler({}) if direct else urllib.request.ProxyHandler()
    return urllib.request.build_opener(proxy, ScopedRedirect(), urllib.request.HTTPCookieProcessor(jar))


def request_headers(entry):
    headers = {'User-Agent': 'Mozilla/5.0'}
    for k, v in entry.get('headers', {}).items():
        if k.lower() in ('user-agent', 'referer', 'origin', 'authorization'):
            if '\n' in v or '\r' in v:
                raise ValueError('请求头包含非法换行。')
            headers[k] = v
    return headers


def download(opener, url, headers, dest, max_bytes):
    tmp = Path(str(dest) + '.part')
    try:
        with opener.open(urllib.request.Request(http_url(url), headers=headers), timeout=30) as res:
            if res.status != 200:
                raise ValueError('完整下载需要 HTTP 200，实际为 %s' % res.status)
            if any(x in res.headers.get('Content-Type', '').lower() for x in ('text/html', 'application/json')):
                raise ValueError('媒体请求返回了网页或 JSON。')
            length = int(res.headers.get('Content-Length', '0'))
            if length > max_bytes:
                raise ValueError('媒体超过下载上限。')
            size = 0
            with open(tmp, 'wb') as f:
                os.chmod(tmp, 0o600)
                while True:
                    data = res.read(1024 * 1024)
                    if not data:
                        break
                    size += len(data)
                    if size > max_bytes:
                        raise ValueError('媒体超过下载上限。')
                    f.write(data)
            if not size or (length and size != length):
                raise ValueError('下载为空或字节数与 Content-Length 不一致。')
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def scoped_headers(headers, origin, url):
    result = dict(headers)
    if urllib.parse.urlsplit(origin).netloc != urllib.parse.urlsplit(url).netloc or urllib.parse.urlsplit(origin).scheme != urllib.parse.urlsplit(url).scheme:
        result = {k: v for k, v in result.items() if k.lower() not in ('authorization', 'cookie')}
    return result


def hls_input(opener, url, headers, depth=0, origin=None):
    """Select a VOD audio rendition/variant. FFmpeg handles media segments and AES-128."""
    if depth > 5:
        raise ValueError('HLS 播放列表嵌套过深。')
    origin = origin or url
    with opener.open(urllib.request.Request(http_url(url), headers=scoped_headers(headers, origin, url)), timeout=30) as res:
        final_url = res.geturl()
        text = res.read(2 * 1024 * 1024 + 1)
    if len(text) > 2 * 1024 * 1024:
        raise ValueError('HLS 列表过大。')
    text = text.decode('utf-8-sig').strip()
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if not lines or lines[0] != '#EXTM3U':
        raise ValueError('媒体清单不是有效 M3U8。')
    audio = [s for s in lines if s.startswith('#EXT-X-MEDIA:') and 'TYPE=AUDIO' in s and 'URI="' in s]
    if audio:
        preferred = next((s for s in audio if 'DEFAULT=YES' in s), audio[0])
        child = re.search(r'URI="([^"]+)"', preferred)[1]
        return hls_input(opener, urllib.parse.urljoin(final_url, child), headers, depth + 1, origin)
    variants = []
    for i, line in enumerate(lines[:-1]):
        if line.startswith('#EXT-X-STREAM-INF:') and not lines[i + 1].startswith('#'):
            bw = re.search(r'(?:^|,)BANDWIDTH=(\d+)', line.split(':', 1)[1])
            variants.append((int(bw[1]) if bw else 0, lines[i + 1]))
    if variants:
        return hls_input(opener, urllib.parse.urljoin(final_url, min(variants)[1]), headers, depth + 1, origin)
    if '#EXT-X-ENDLIST' not in lines:
        raise ValueError('发现直播/未结束清单；当前只处理完整点播，不能作为完整视频处理。')
    for line in lines:
        if line.startswith('#EXT-X-KEY:') and not re.search(r'METHOD=(NONE|AES-128)(?:,|$)', line):
            raise ValueError('该加密方式不在当前适配范围；不尝试绕过 DRM。')
    duration = sum(float(s.split(':', 1)[1].split(',')[0]) for s in lines if s.startswith('#EXTINF:'))
    if duration <= 0:
        raise ValueError('HLS 缺少有效分片时长。')
    return final_url, duration


class HLSRelay:
    """Let CookieJar enforce full cookie scope; FFmpeg only sees opaque local URLs.

    FFmpeg 7.1's HTTP cookie implementation does not enforce Secure/host-only rules.
    This relay also keeps cookie values and signed URLs out of FFmpeg process args.
    It is not a general proxy: only URIs registered from this playlist are served.
    """
    def __init__(self, opener, origin, headers):
        self.opener, self.origin, self.headers = opener, origin, headers
        self.routes = {}
        self.urls = {}
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                entry = relay.routes.get(self.path)
                if not entry:
                    self.send_error(404)
                    return
                url, playlist = entry
                headers = scoped_headers(relay.headers, relay.origin, url)
                if self.headers.get('Range') and not playlist:
                    headers['Range'] = self.headers['Range']
                try:
                    with relay.opener.open(urllib.request.Request(url, headers=headers), timeout=30) as res:
                        if playlist:
                            text = res.read(2 * 1024 * 1024 + 1)
                            if len(text) > 2 * 1024 * 1024:
                                raise ValueError('HLS 列表过大')
                            content = relay.rewrite(text.decode('utf-8-sig'), res.geturl()).encode()
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                            self.send_header('Content-Length', str(len(content)))
                            self.end_headers()
                            self.wfile.write(content)
                        else:
                            self.send_response(res.status)
                            for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                                if res.headers.get(name):
                                    self.send_header(name, res.headers[name])
                            self.end_headers()
                            while True:
                                block = res.read(65536)
                                if not block:
                                    break
                                self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    # Do not put remote signed URLs, cookies or auth values into local error pages.
                    self.close_connection = True

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def register(self, url, playlist=False, extension=None):
        http_url(url)
        suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        suffix = extension or ('.m3u8' if playlist else suffix if suffix in ('.ts', '.m4s', '.mp4', '.aac', '.key') else '.ts')
        identity = (url, playlist, suffix)
        if identity in self.urls:
            return self.urls[identity]
        key = '/' + uuid.uuid4().hex + suffix
        self.routes[key] = (url, playlist)
        local = 'http://127.0.0.1:%d%s' % (self.server.server_port, key)
        self.urls[identity] = local
        return local

    def rewrite(self, text, base):
        output = []
        for line in text.splitlines():
            value = line.strip()
            if value and not value.startswith('#'):
                line = self.register(urllib.parse.urljoin(base, value))
            elif 'URI="' in line:
                extension = '.key' if line.startswith('#EXT-X-KEY:') else '.mp4' if line.startswith('#EXT-X-MAP:') else None
                line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + self.register(urllib.parse.urljoin(base, m[1]), extension=extension) + '"', line)
            output.append(line)
        return '\n'.join(output) + '\n'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def probe(source):
    import av
    with av.open(str(source)) as c:
        if not c.streams.audio:
            raise ValueError('文件没有音轨。')
        s = c.streams.audio[0]
        duration = float(s.duration * s.time_base) if s.duration is not None else (c.duration / av.time_base if c.duration else None)
        return {'codec': s.codec_context.name, 'sample_rate': s.codec_context.sample_rate,
                'channels': s.codec_context.channels, 'duration': duration}


def validate_audio(path, expected=None):
    import av
    info = probe(path)
    samples = 0
    with av.open(str(path)) as c:
        for frame in c.decode(audio=0):
            samples += frame.samples
    decoded = samples / info['sample_rate']
    if samples <= 0 or not info['channels']:
        raise ValueError('输出音频为空。')
    if expected and abs(decoded - expected) > max(1.0, expected * 0.001):
        raise ValueError('音频与源时长不一致：decoded=%.3f expected=%.3f' % (decoded, expected))
    return {**info, 'decoded_samples': samples, 'decoded_duration': decoded,
            'bytes': path.stat().st_size, 'sha256': sha(path), 'validated': True}


def run_extract(task, state, index, direct, max_bytes):
    capture = load(task / 'capture.json')
    media = capture.get('media', [])
    if index is None:
        if len(media) != 1:
            raise ValueError('发现 %d 个候选媒体，核对 capture.json 后用 --media-index 选择。' % len(media))
        index = 0
    entry = media[index]
    url = http_url(entry['url'])
    headers = request_headers(entry)
    cookies = capture.get('cookies', [])
    opener = client(cookies, direct)
    binary = ffmpeg()
    relay = None
    expected = None
    matches = [v.get('duration') for v in capture.get('videos', []) if v.get('url') == url and v.get('duration')]
    if matches:
        expected = matches[-1]
    elif len(media) == 1:
        durations = {v.get('duration') for v in capture.get('videos', []) if v.get('duration') and v['duration'] > 0}
        if len(durations) == 1:
            expected = durations.pop()
    if entry['kind'] == 'file':
        source = task / 'source.media'
        cached = state.get('source', {})
        if not (source.exists() and cached.get('url_sha256') == url_key(url) and cached.get('sha256') == sha(source)):
            download(opener, url, headers, source, max_bytes)
        info = probe(source)
        if expected and info['duration'] and abs(info['duration'] - expected) > max(1.0, expected * .001):
            raise ValueError('下载的媒体与播放器时长不符，可能是预览片。')
        expected = expected or info['duration']
        state['source'] = {'url_sha256': url_key(url), 'sha256': sha(source), 'bytes': source.stat().st_size}
        input_args = ['-i', str(source)]
        copy = info['codec'] in ('aac', 'alac')
    elif entry['kind'] == 'hls':
        selected, playlist_duration = hls_input(opener, url, headers)
        if expected and abs(playlist_duration - expected) > max(1.0, expected * .001):
            raise ValueError('HLS 清单与播放器时长不符，可能是预览或错误候选。')
        expected = playlist_duration
        relay = HLSRelay(opener, url, headers)
        input_args = ['-rw_timeout', '30000000', '-protocol_whitelist', 'http,https,tcp,tls,crypto,data']
        input_args += ['-i', relay.register(selected, playlist=True)]
        copy = False  # Lossless FLAC handles AAC/AC-3/Opus without a fragile codec assumption.
    else:
        raise ValueError('未支持的媒体类型。')
    if not expected or expected <= 0:
        raise ValueError('无法确定完整点播时长；先确认媒体，不自动生成完整视频的转录。')
    filename = 'audio.m4a' if copy else 'audio.flac'
    dest, part = task / filename, task / (filename + '.part')
    cmd = [binary, '-hide_banner', '-v', 'error', '-xerror', '-nostdin', '-y'] + input_args
    cmd += ['-map', '0:a:0', '-vn', '-c:a', 'copy' if copy else 'flac', '-f', 'ipod' if copy else 'flac', str(part)]
    env = dict(os.environ)
    proxies = {} if direct else urllib.request.getproxies()
    for scheme in ('http', 'https'):
        env.pop(scheme + '_proxy', None)
        env.pop(scheme.upper() + '_PROXY', None)
        if proxies.get(scheme):
            env[scheme + '_proxy'] = proxies[scheme]
    if relay:
        for scheme in ('http', 'https'):
            env.pop(scheme + '_proxy', None)
        env['no_proxy'] = '127.0.0.1,localhost,::1'
    try:
        start = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                timeout=max(180, min(7200, expected * 2 + 120)))
        if result.returncode:
            raise ValueError('FFmpeg 失败（退出码 %s）：%s' % (result.returncode, clean_error(result.stderr)))
        report = validate_audio(part, expected)
        part.replace(dest)
        report.update({'file': filename, 'expected_duration': expected, 'media_index': index,
                       'capture_sha256': sha(task / 'capture.json'), 'seconds': round(time.monotonic() - start, 3)})
        state['audio'] = report
        state['stage'] = 'audio_validated'
        state.pop('chunks', None)
        save(task / 'manifest.json', state)
        return report
    finally:
        part.unlink(missing_ok=True)
        if relay:
            relay.close()


def chunks(task, state, seconds, overlap):
    audio = state.get('audio', {})
    path = task / audio.get('file', 'missing')
    if not audio.get('validated') or not path.is_file() or sha(path) != audio['sha256']:
        raise ValueError('需要已校验且未变化的完整音频。')
    if audio.get('capture_sha256') and sha(task / 'capture.json') != audio['capture_sha256']:
        raise ValueError('抓流记录已改变，请重新抽取音频。')
    if seconds < 10 or not 0 <= overlap < seconds / 2:
        raise ValueError('切片时长至少 10 秒，重叠必须小于半片时长。')
    total, offset, rows = audio['decoded_duration'], 0.0, []
    folder = task / 'chunks' / uuid.uuid4().hex[:8]
    folder.mkdir(parents=True, mode=0o700)
    while offset < total:
        end = min(total, offset + seconds)
        dest = folder / ('%04d.wav' % len(rows))
        subprocess.run([ffmpeg(), '-v', 'error', '-xerror', '-nostdin', '-y', '-ss', str(offset),
                        '-i', str(path), '-t', str(end - offset), '-ar', '16000', '-ac', '1',
                        '-c:a', 'pcm_s16le', str(dest)], check=True, capture_output=True, timeout=180)
        os.chmod(dest, 0o600)
        rows.append({'index': len(rows), 'file': str(dest.relative_to(task)), 'offset': offset,
                     'duration': end - offset, 'sha256': sha(dest)})
        if end == total:
            break
        offset = end - overlap
    for i, row in enumerate(rows):
        row['keep_from'] = 0 if i == 0 else (row['offset'] + rows[i-1]['offset'] + rows[i-1]['duration']) / 2
        row['keep_until'] = total if i + 1 == len(rows) else (row['offset'] + row['duration'] + rows[i+1]['offset']) / 2
    report = {'audio_sha256': audio['sha256'], 'overlap': overlap, 'items': rows}
    save(task / 'chunks.json', report)
    state['chunks'] = report
    state['stage'] = 'chunks_ready'
    save(task / 'manifest.json', state)
    return {'count': len(rows), 'index': str(task / 'chunks.json')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    doctor = sub.add_parser('doctor')
    doctor.add_argument('--require-browser', action='store_true', help='浏览器采集依赖缺失时非零退出')
    init = sub.add_parser('init')
    init.add_argument('--url', required=True)
    init.add_argument('--output-root', default=str(data_root() / 'tasks'))
    for name in ('capture', 'extract', 'chunk'):
        q = sub.add_parser(name)
        q.add_argument('--task', required=True)
        if name == 'capture':
            q.add_argument('--cdp', required=True)
            q.add_argument('--wait', type=int, default=35)
        elif name == 'extract':
            q.add_argument('--media-index', type=int)
            q.add_argument('--direct', action='store_true')
            q.add_argument('--max-bytes', type=int, default=4 * 1024**3)
        else:
            q.add_argument('--seconds', type=float, default=900)
            q.add_argument('--overlap', type=float, default=2)
    args = p.parse_args()
    if args.command == 'doctor':
        import av
        node = find_node()
        browser = find_browser()
        playwright = playwright_status(node, ROOT)
        print(json.dumps({'python': sys.executable, 'av': av.__version__, 'ffmpeg': ffmpeg(),
                          'node': node, 'playwright': playwright, 'browser': browser,
                          'data_root': str(data_root()),
                          'asr': '由宿主或用户配置，需单独核验', 'tencent_docs': '由宿主连接器提供，需单独核验'}, ensure_ascii=False))
        if args.require_browser and (not browser or not playwright['available']):
            raise SystemExit('浏览器采集依赖未就绪；检查上述 doctor 输出。')
        return
    if args.command == 'init':
        http_url(args.url)
        task = Path(args.output_root).expanduser().resolve() / (url_key(args.url)[:12] + '-' + uuid.uuid4().hex[:8])
        task.mkdir(parents=True, mode=0o700)
        save(task / 'manifest.json', {'version': 1, 'url': args.url, 'stage': 'initialized', 'created_at': time.time()})
        print(json.dumps({'task': str(task)}, ensure_ascii=False))
        return
    task = Path(args.task).expanduser().resolve()
    with locked(task):
        state = load(task / 'manifest.json')
        if args.command == 'capture':
            node = find_node()
            if not node:
                raise ValueError('找不到 Node。')
            env = dict(os.environ)
            for key in ('NO_PROXY', 'no_proxy'):
                env[key] = ','.join(filter(None, [env.get(key, ''), '127.0.0.1,localhost,::1']))
            result = subprocess.run([node, str(ROOT / 'scripts/capture.js'), '--url', state['url'],
                                     '--cdp', args.cdp, '--out', str(task / 'capture.json'), '--wait', str(args.wait)],
                                    env=env, capture_output=True, text=True, timeout=210)
            if result.returncode:
                raise ValueError('抓流失败：' + clean_error(result.stderr))
            for key in ('audio', 'chunks'):
                state.pop(key, None)
            state['stage'] = 'captured'
            save(task / 'manifest.json', state)
            print(result.stdout.strip())
        elif args.command == 'extract':
            print(json.dumps(run_extract(task, state, args.media_index, args.direct, args.max_bytes), ensure_ascii=False))
        elif args.command == 'chunk':
            print(json.dumps(chunks(task, state, args.seconds, args.overlap), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR: ' + clean_error(e), file=sys.stderr)
        sys.exit(1)
