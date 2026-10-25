#!/usr/bin/env python3
"""Import timestamped ASR segments, assemble complete transcripts, render safe Tencent MDX."""
import argparse
import html
import json
import math
from pathlib import Path
import re
import sys

from pipeline import clean_error, load, locked, save, sha


def audio_state(task):
    state = load(task / 'manifest.json')
    audio = state.get('audio', {})
    source = task / audio.get('file', 'missing')
    if not audio.get('validated') or not source.is_file() or sha(source) != audio.get('sha256'):
        raise ValueError('完整音频未通过校验或已改变。')
    if audio.get('capture_sha256') and sha(task / 'capture.json') != audio['capture_sha256']:
        raise ValueError('抓流记录已改变，请重新抽取并校验音频。')
    return state, audio


def parts_for(task, state, audio):
    if state.get('chunks'):
        chunks = state['chunks']
        if chunks['audio_sha256'] != audio['sha256']:
            raise ValueError('切片属于旧音频。')
        for row in chunks['items']:
            if sha(task / row['file']) != row['sha256']:
                raise ValueError('ASR 切片文件已改变。')
        return chunks['items']
    return [{'index': 0, 'file': audio['file'], 'offset': 0, 'duration': audio['decoded_duration'],
             'keep_from': 0, 'keep_until': audio['decoded_duration']}]


def check_segments(data, duration):
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        raise ValueError('输入格式必须为 {"segments":[{"start":秒,"end":秒,"text":"原文"}]}。')
    last = -1
    for row in data['segments']:
        start, end = row.get('start'), row.get('end')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (start, end)):
            raise ValueError('时间戳必须是有限数字，单位为秒。')
        if start < 0 or end <= start or end > duration + 0.5 or start < last:
            raise ValueError('时间戳倒序、越界或空区间。')
        if not isinstance(row.get('text'), str) or not row['text'].strip():
            raise ValueError('片段文字为空；静音片使用空 segments 数组。')
        last = start
    return data['segments']


def import_asr(task, source, raw, provider, index, replace=False):
    state, audio = audio_state(task)
    parts = parts_for(task, state, audio)
    part = next((p for p in parts if p['index'] == index), None)
    if part is None:
        raise ValueError('切片编号不存在。')
    data = load(source)
    segments = check_segments(data, part['duration'])
    if not raw.is_file() or not raw.stat().st_size:
        raise ValueError('必须保存工具的完整原始响应，再通过 --raw 指定。')
    chunk_key = sha(task / 'chunks.json')[:12] if state.get('chunks') else 'whole'
    folder = task / 'asr' / audio['sha256'][:16] / chunk_key
    dest = folder / ('%04d.json' % index)
    raw_bytes = raw.read_bytes()
    record = {'audio_sha256': audio['sha256'], 'provider': provider, 'part': part,
              'input_sha256': sha(source), 'raw_sha256': sha(raw), 'segments': segments}
    if dest.exists() and load(dest) != record and not replace:
        raise ValueError('该片已有不同的识别结果；确认后加 --replace，避免无意覆盖。')
    raw_dest = folder / ('%04d.raw' % index)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_dest.write_bytes(raw_bytes)
    raw_dest.chmod(0o600)
    save(dest, record)
    state['asr_folder'] = str(folder.relative_to(task))
    state['stage'] = 'asr_partial'
    save(task / 'manifest.json', state)
    return {'imported_part': index, 'segments': len(segments), 'expected_parts': len(parts)}


def stamp(seconds, srt=False):
    ms = round(seconds * 1000)
    h, remainder = divmod(ms, 3600000)
    m, remainder = divmod(remainder, 60000)
    s, ms = divmod(remainder, 1000)
    return '%02d:%02d:%02d%s%03d' % (h, m, s, ',' if srt else '.', ms)


def assemble(task):
    state, audio = audio_state(task)
    parts = parts_for(task, state, audio)
    folder = task / state.get('asr_folder', 'missing')
    rows, warnings = [], []
    for part in parts:
        record = load(folder / ('%04d.json' % part['index']))
        if record['audio_sha256'] != audio['sha256'] or record['part'] != part:
            raise ValueError('识别记录与当前音频或切片不匹配。')
        raw = folder / ('%04d.raw' % part['index'])
        if sha(raw) != record['raw_sha256']:
            raise ValueError('ASR 原始响应已改变。')
        segments = check_segments(record, part['duration'])
        if not segments:
            warnings.append({'type': 'empty_part', 'part': part['index']})
        for seg in segments:
            start, end = seg['start'] + part['offset'], seg['end'] + part['offset']
            midpoint = (start + end) / 2
            if not part['keep_from'] <= midpoint < part['keep_until']:
                continue
            row = {**seg, 'start': round(start, 3), 'end': round(end, 3), 'part': part['index']}
            rows.append(row)
            if re.search(r'(.)\1{3,}', seg['text']):
                warnings.append({'type': 'repetition', 'start': start, 'text': seg['text']})
            if isinstance(seg.get('avg_logprob'), (int, float)) and seg['avg_logprob'] < -1.2:
                warnings.append({'type': 'low_logprob', 'start': start})
    if not rows:
        raise ValueError('完整 ASR 结果没有有效语音片段，不能生成逐字稿。')
    rows.sort(key=lambda s: (s['start'], s['end']))
    cursor = 0
    for row in rows:
        if row['start'] - cursor > 90:
            warnings.append({'type': 'long_gap', 'start': cursor, 'end': row['start']})
        cursor = max(cursor, row['end'])
    if audio['decoded_duration'] - cursor > 90:
        warnings.append({'type': 'long_gap', 'start': cursor, 'end': audio['decoded_duration']})
    result = {'audio_sha256': audio['sha256'], 'duration': audio['decoded_duration'], 'segments': rows}
    save(task / 'transcript.raw.json', result)
    text = '\n'.join('[%s–%s] %s' % (stamp(r['start']), stamp(r['end']), r['text']) for r in rows)
    srt = '\n\n'.join('%d\n%s --> %s\n%s' % (i, stamp(r['start'], True), stamp(r['end'], True), r['text']) for i, r in enumerate(rows, 1))
    for name, value in [('transcript.raw.txt', text), ('transcript.raw.srt', srt + '\n')]:
        dest = task / name
        dest.write_text(value, encoding='utf-8')
        dest.chmod(0o600)
    save(task / 'transcript.qa.json', {'all_parts_present': True, 'automatic_checks_only': True,
          'review_required': True, 'warnings': warnings, 'note': '结构检查不能证明逐字准确；长静音警告需回听判断。'})
    state['stage'] = 'asr_assembled_needs_review'
    state['transcript'] = {'sha256': sha(task / 'transcript.raw.json'), 'segments': len(rows)}
    save(task / 'manifest.json', state)
    return {'segments': len(rows), 'warnings': len(warnings), 'qa': str(task / 'transcript.qa.json')}


def inline_mdx(line):
    # Preserve inline code literally. ASR text cannot become JSX/executable expressions.
    quote = re.match(r'^(\s*(?:>\s*)+)(.*)$', line)
    if quote:
        return quote[1] + inline_mdx(quote[2])
    tokens = re.split(r'(`+[^`]*`+)', line)
    output = []
    for token in tokens:
        if token.startswith('`'):
            output.append(token)
            continue
        value = html.escape(token, quote=False).replace('{', '&#123;').replace('}', '&#125;')
        if value.count('**') % 2:
            raise ValueError('加粗标记不成对；先修复 Markdown。')
        value = re.sub(r'\*\*(.+?)\*\*', r'<Mark bold>\1</Mark>', value)
        output.append(value)
    return ''.join(output)


def render_mdx(source, outdir):
    text = source.read_text(encoding='utf-8')
    if re.search(r'!\[[^\]]*\]\(|\{\{FIG\d+\}\}', text):
        raise ValueError('当前技能仅交付文字，请先清理图片占位符。')
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            lines.append(line)
        elif fence:
            lines.append(line)
        else:
            lines.append(inline_mdx(line))
    if fence:
        raise ValueError('代码围栏未闭合。')
    outdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = outdir / 'content.mdx'
    dest.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    dest.chmod(0o600)
    save(outdir / 'content-index.json', {'source': str(source), 'source_sha256': sha(source),
          'mdx_sha256': sha(dest), 'characters': len(dest.read_text(encoding='utf-8')),
          'headings': [s for s in text.splitlines() if re.match(r'^#{1,6}\s', s)]})
    return {'mdx': str(dest), 'characters': len(dest.read_text(encoding='utf-8'))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('import-asr')
    q.add_argument('--task', required=True)
    q.add_argument('--input', required=True)
    q.add_argument('--raw', required=True)
    q.add_argument('--provider', required=True)
    q.add_argument('--part', type=int, default=0)
    q.add_argument('--replace', action='store_true')
    q = sub.add_parser('assemble')
    q.add_argument('--task', required=True)
    q = sub.add_parser('mdx')
    q.add_argument('--input', required=True)
    q.add_argument('--outdir', required=True)
    args = p.parse_args()
    if args.command == 'mdx':
        report = render_mdx(Path(args.input).resolve(), Path(args.outdir).resolve())
    else:
        task = Path(args.task).resolve()
        with locked(task):
            if args.command == 'assemble':
                report = assemble(task)
            else:
                report = import_asr(task, Path(args.input), Path(args.raw), args.provider, args.part, args.replace)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR: ' + clean_error(e), file=sys.stderr)
        sys.exit(1)
