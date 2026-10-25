// Attach listeners BEFORE navigation. Export cookies only for observed media URLs.
const fs = require('fs');
const path = require('path');
const os = require('os');
let pw;
let modulePath;
if (Number(process.versions.node.split('.')[0]) < 20) {
  console.error('需要 Node.js >=20。'); process.exit(1);
}
const names = process.env.VIDEO_TRANSCRIPT_PLAYWRIGHT ? [process.env.VIDEO_TRANSCRIPT_PLAYWRIGHT] :
  ['playwright-core', 'playwright', path.join(os.homedir(), '.workbuddy/binaries/node/workspace/node_modules/playwright-core')];
for (const name of names) {
  try { modulePath = require.resolve(name); pw = require(modulePath); if (pw.chromium) break; pw = null; } catch (_) {}
}
if (!pw) { console.error('找不到 Playwright；运行 scripts/bootstrap.py 或在技能目录运行 npm ci。'); process.exit(1); }
const argv = process.argv.slice(2);
if (argv.includes('--check')) {
  console.log(JSON.stringify({node: process.version, module: modulePath})); process.exit(0);
}
function arg(name, fallback) { const i = argv.indexOf(name); return i < 0 ? fallback : argv[i + 1]; }
const target = arg('--url'), out = arg('--out'), cdp = arg('--cdp');
const wait = Math.max(5, Math.min(120, Number(arg('--wait', '35'))));
function kind(url, ct = '') {
  if (!/^https?:\/\//i.test(url)) return null;
  const pathname = new URL(url).pathname;
  if (/\.m3u8$/i.test(pathname) || /mpegurl/i.test(ct)) return 'hls';
  if (/\.mpd$/i.test(pathname) || /dash\+xml/i.test(ct)) return 'dash';
  // Individual fragments are never accepted as complete media.
  if (/\.(ts|m4s|aac)$/i.test(pathname)) return null;
  if (/video\/mp2t|audio\/aac|video\/iso\.segment/i.test(ct)) return null;
  if (/\.(mp4|m4a|mp3|webm|mov|wav|flac|ogg)$/i.test(pathname) || /^(audio|video)\//i.test(ct)) return 'file';
  return null;
}
(async () => {
  if (!target || !out || !cdp) throw new Error('需要 --url --out --cdp');
  if (!/^https?:$/.test(new URL(target).protocol)) throw new Error('视频 URL 必须为 HTTP(S)');
  const endpoint = new URL(cdp);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname)) throw new Error('CDP 仅连接本机');
  const browser = await pw.chromium.connectOverCDP(cdp, { timeout: 30000 });
  const ctx = browser.contexts()[0];
  const page = await ctx.newPage();
  const media = new Map(), pending = [], videos = [], related = new Set();
  page.on('response', res => {
    const url = res.url(), ct = res.headers()['content-type'] || '', type = kind(url, ct);
    if ([200, 206].includes(res.status()) && /^https?:\/\//i.test(url) &&
        (type || /\.(ts|m4s|aac|key)(\?|$)/i.test(url) || /video\/mp2t|audio\/aac|video\/iso\.segment/i.test(ct))) related.add(url);
    if (!type || ![200, 206].includes(res.status())) return;
    const entry = { url, kind: type, contentType: ct, status: res.status(), headers: {} };
    media.set(url, entry);
    pending.push(res.request().allHeaders().then(headers => {
      for (const k of ['referer', 'origin', 'user-agent', 'authorization']) if (headers[k]) entry.headers[k] = headers[k];
    }).catch(() => {}));
  });
  try {
    await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const deadline = Date.now() + wait * 1000;
    do {
      for (const frame of page.frames()) {
        const found = await frame.evaluate(() => {
          const all = [...document.querySelectorAll('video,audio')];
          for (const v of all) { v.muted = true; v.play().catch(() => {}); }
          return all.map(v => ({ url: v.currentSrc || v.src, duration: Number.isFinite(v.duration) ? v.duration : null,
                                readyState: v.readyState, videoWidth: v.videoWidth || 0 }));
        }).catch(() => []);
        for (const v of found) {
          if (!videos.some(x => x.url === v.url && x.duration === v.duration)) videos.push(v);
          const type = kind(v.url);
          if (type && !media.has(v.url)) media.set(v.url, { url: v.url, kind: type, headers: {}, from: 'dom' });
        }
      }
      if ([...media.values()].some(m => m.status && m.kind !== 'dash') && videos.some(v => v.duration > 0 && v.readyState >= 2)) break;
      await page.waitForTimeout(1000);
    } while (Date.now() < deadline);
    await Promise.allSettled(pending);
    const entries = [...media.values()].filter(m => m.kind !== 'dash');
    const cookies = entries.length ? await ctx.cookies([...new Set([...entries.map(m => m.url), ...related])]) : [];
    const title = await page.title();
    const result = { version: 1, pageUrl: page.url(), title, capturedAt: new Date().toISOString(),
                     videos, media: entries, cookies };
    fs.mkdirSync(path.dirname(out), { recursive: true, mode: 0o700 });
    fs.writeFileSync(out + '.part', JSON.stringify(result, null, 2), { mode: 0o600 });
    fs.renameSync(out + '.part', out); fs.chmodSync(out, 0o600);
    console.log(JSON.stringify({ title, candidates: entries.map((m, i) => ({ index: i, kind: m.kind, host: new URL(m.url).host })),
                                 durations: videos.map(v => v.duration).filter(Boolean) }));
    if (!entries.length) throw new Error(media.size ? '只发现 DASH；当前适配器未支持。' : '没有可下载媒体；检查登录/权限/播放，不能把 blob 当下载地址。');
  } finally {
    await page.close().catch(() => {});
  }
  process.exit(0); // Disconnect CDP; keep the persistent browser and login session.
})().catch(e => { console.error(e.message); process.exit(1); });
