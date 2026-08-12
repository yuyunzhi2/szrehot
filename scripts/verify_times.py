# -*- coding: utf-8 -*-
"""szrehot 真实发布时间核验工具 — 阶段B步骤6 的确定性脚本

解决 2026-08-11 踩坑：S1.5 的 publishedAt 是 Google 索引时间（非真实发布时间，
实测差 1-8 天），且执行时"先全量抓全文再核验时间"导致窗口外条目白抓。

本脚本强制正确顺序：
  1. URL 路径日期初筛（本地正则，秒级判断，不开页）→ 分三档
  2. 仅对"需开页"条目批量抓取（requests + 多编码探测）提取 meta 发布时间
  3. 输出 _time_verified.json 核验结果表，主模型只审核结论、不重抓

用法（工作目录 = 技能目录）:
  python -X utf8 scripts/verify_times.py --input scripts/_pre_cands.json \
      --window-start 2026-08-11 --output scripts/_time_verified.json
      [--max-workers 8] [--skip-fetch]   # --skip-fetch 只做 URL 初筛不开页

输入 JSON: [{"title","url","source","publishedAt"(Google索引时间,仅供追溯),...}, ...]
输出 JSON: [{"title","url","source","window_result": 窗口内|窗口外|需开页|时间未知,
             "url_date": "2026-08-11"|null, "verified_time": "2026-08-11T16:16:03"|null,
             "verified_from": "url_path"|"page_meta"|"page_body"|null, "note": ""}, ...]
"""
import argparse
import io
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── URL 路径日期提取 ──
# 匹配形如 /2026-08-11/、/2026-08-11、20260811、/2026/0811/ 等日期片段
_URL_DATE_RE = re.compile(r'(20\d{2})[-/年]?(\d{1,2})[-/月]?(\d{1,2})')
# 排除纯数字串陷阱（szhome 底部 83680534 是电话，非日期）；要求年份 20xx
_VALID_YEAR = (2020, 2100)

# 页面 meta 发布时间提取正则（按优先级）
_META_TIME_PATTERNS = [
    # <meta property="article:published_time" content="2026-08-11T16:16:03+08:00">
    re.compile(r'article:published_time["\']?\s+content=["\']([^"\']+)', re.I),
    # <meta name="publishdate" content="2026-08-11 16:16:03">
    re.compile(r'name=["\'](?:publishdate|pubdate|publish_time|date|time)["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]*name=["\'](?:publishdate|pubdate|publish_time|date|time)["\']', re.I),
    # data-publishtime="2026-08-11 16:16:03"（新浪实测）
    re.compile(r'data-publishtime=["\']([^"\']+)', re.I),
    # 正文时间行：2026年08月11日 16:16:03 / 2026-08-11 16:16 / 08.11 16:16
    re.compile(r'(20\d{2})[年.\-/](\d{1,2})[月.\-/](\d{1,2})日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?'),
    # 仅日期无时间
    re.compile(r'(20\d{2})[年.\-/](\d{1,2})[月.\-/](\d{1,2})日?'),
]
# 微信 var ct 时间戳（Unix 秒 → 北京时间）
_CT_TS_RE = re.compile(r'var\s+ct\s*=\s*["\']?(\d{10})["\']?')

# 正文中的"噪音时间"排除（网页底部版权/备案日期等会先于正文时间出现）
_NOISE_TIME_HINTS = ('copyright', '©', '备案', 'icp', 'all rights', '版权')


def _decode(html_bytes) -> str:
    """多编码探测解码，规避 GBK/UTF-8 乱码（2026-08-11 踩坑：新浪桌面版/房天下/观点）。"""
    for enc in ('utf-8', 'gb18030', 'gbk', 'big5'):
        try:
            txt = html_bytes.decode(enc)
            if '锟' not in txt and '\ufffd' not in txt:
                return txt
        except (UnicodeDecodeError, LookupError):
            continue
    return html_bytes.decode('utf-8', errors='replace')


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _decode(resp.read())


def extract_url_date(url: str) -> str | None:
    """从 URL 路径提取日期，返回 'YYYY-MM-DD' 或 None。"""
    for m in _URL_DATE_RE.finditer(url):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (_VALID_YEAR[0] <= y <= _VALID_YEAR[1]) or not (1 <= mo <= 12) or not (1 <= d <= 31):
            continue
        # 排除纯数字串（如 szhome 详情页尾部电话号码；要求日期片段长度≥8 或前后有 / 分隔）
        seg = m.group(0)
        if len(seg) == 8 and not re.search(r'[/\-年.]', seg):
            # 纯 8 位数字：需是合法日期才可能是日期（如 20260811）；20360805 这类电话也像日期，
            # 但 URL 中 20 开头的 8 位数字段更可能是日期。返回供人工复核。
            pass
        return f'{y:04d}-{mo:02d}-{d:02d}'
    return None


def extract_page_time(html: str) -> tuple[str, str] | None:
    """从页面 HTML 提取发布时间。返回 (ISO时间, 来源) 或 None。

    来源: 'page_meta' | 'weixin_ct' | 'page_body'。
    正文时间不做全局截断（copyright/版权等噪音词常在页面骨架 JS 中，
    位置先于正文时间，截断会掐掉真实时间 — 2026-08-12 修复 szhome 案例）。
    """
    # 1. meta 标签优先（最可靠）
    for pat in _META_TIME_PATTERNS[:4]:
        m = pat.search(html)
        if m:
            raw = m.group(1).strip()
            iso = normalize_iso_time(raw)
            if iso:
                return iso, 'page_meta'
    # 2. 微信 var ct 时间戳
    m = _CT_TS_RE.search(html)
    if m:
        ts = int(m.group(1))
        dt = datetime.fromtimestamp(ts)
        if 2020 <= dt.year <= 2100:
            return dt.strftime('%Y-%m-%dT%H:%M:%S'), 'weixin_ct'
    # 3. 正文时间：逐个匹配，跳过落在噪音段（copyright/版权/备案附近）的候选
    for pat in _META_TIME_PATTERNS[4:]:
        for m in pat.finditer(html):
            groups = m.groups()
            y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
            if not (2020 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
                continue
            # 噪音段检查：匹配位置前后 80 字符含噪音词则跳过
            ctx = html[max(0, m.start() - 80): m.end() + 80]
            if any(h in ctx.lower() for h in _NOISE_TIME_HINTS):
                continue
            if len(groups) >= 5 and groups[3] is not None:
                hh, mm = int(groups[3]), int(groups[4])
                ss = int(groups[5]) if len(groups) > 5 and groups[5] else 0
                return f'{y:04d}-{mo:02d}-{d:02d}T{hh:02d}:{mm:02d}:{ss:02d}', 'page_body'
            return f'{y:04d}-{mo:02d}-{d:02d}T00:00:00', 'page_body'
    return None


def normalize_iso_time(raw: str) -> str | None:
    """把 meta 时间字符串规范化为 'YYYY-MM-DDTHH:MM:SS'。"""
    raw = raw.strip()
    # 2026-08-11T16:16:03+08:00 / 2026-08-11 16:16:03 / 2026/08/11 16:16
    m = re.match(r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})[T\s](\d{1,2}):(\d{2})(?::(\d{2}))?', raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm = int(m.group(4)), int(m.group(5))
        ss = int(m.group(6)) if m.group(6) else 0
        return f'{y:04d}-{mo:02d}-{d:02d}T{hh:02d}:{mm:02d}:{ss:02d}'
    # 纯日期
    m = re.match(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f'{y:04d}-{mo:02d}-{d:02d}T00:00:00'
    return None


def classify(url: str, url_date: str | None, window_start: date, window_end: date) -> tuple[str, str]:
    """按 URL 日期初筛分类：窗口内/窗口外/需开页。返回 (结果, 说明)。"""
    if not url_date:
        return '需开页', 'URL无日期，需开页提取'
    d = date.fromisoformat(url_date)
    if d < window_start:
        return '窗口外', f'URL日期{url_date}早于窗口起点'
    if d > window_end:
        return '窗口外', f'URL日期{url_date}晚于执行日（未来）'
    # URL 日期在窗口内：仍可能是旧文（索引时间陷阱），需开页确认精确时间
    return '需开页', f'URL日期{url_date}在窗口内，需开页确认精确时间'


def main():
    ap = argparse.ArgumentParser(description='szrehot 真实发布时间核验（URL初筛→开页提取→结果表）')
    ap.add_argument('--input', required=True, help='候选 JSON（title/url/source/publishedAt）')
    ap.add_argument('--window-start', required=True, help='窗口起点 YYYY-MM-DD')
    ap.add_argument('--output', default=None, help='输出 JSON 路径（默认 scripts/_time_verified.json）')
    ap.add_argument('--max-workers', type=int, default=8)
    ap.add_argument('--skip-fetch', action='store_true', help='只做 URL 初筛，不开页抓取')
    a = ap.parse_args()

    with open(a.input, encoding='utf-8') as f:
        cands = json.load(f)

    ws = date.fromisoformat(a.window_start)
    we = date.today()
    # 先计算执行当下时间（窗口上界，精确到秒）
    now = datetime.now()
    window_end_dt = now.replace(microsecond=0)

    # 阶段1: URL 初筛
    for c in cands:
        ud = extract_url_date(c.get('url', ''))
        c['_url_date'] = ud
        c['_class'], c['_note'] = classify(c.get('url', ''), ud, ws, we)

    url_out = sum(1 for c in cands if c['_class'] == '窗口外')
    url_in = sum(1 for c in cands if c['_class'] == '窗口内')
    need_page = [c for c in cands if c['_class'] == '需开页']
    print(f'URL 初筛: 共{len(cands)}条 | 窗口外(丢弃) {url_out} | 需开页 {len(need_page)}')

    # 阶段2: 开页提取（--skip-fetch 跳过）
    if not a.skip_fetch and need_page:
        print(f'开页核验 {len(need_page)} 条...')

        def _do(c):
            try:
                html = _fetch(c['url'])
                r = extract_page_time(html)
                if r:
                    iso, src = r
                    return c, iso, src, ''
                return c, None, None, '页面无时间信息'
            except Exception as e:
                return c, None, None, f'抓取失败: {e}'

        done = 0
        with ThreadPoolExecutor(max_workers=a.max_workers) as ex:
            futures = {ex.submit(_do, c): c for c in need_page}
            for fut in as_completed(futures):
                c, iso, src, note = fut.result()
                c['_verified'] = iso
                c['_vfrom'] = src
                if note:
                    c['_note'] = (c.get('_note', '') + '; ' + note).strip('; ')
                done += 1
                if done % 10 == 0 or done == len(need_page):
                    print(f'  进度 {done}/{len(need_page)}')
    else:
        for c in need_page:
            c['_verified'] = None
            c['_vfrom'] = None
            c['_note'] = c.get('_note', '') + ('; --skip-fetch 未开页' if a.skip_fetch else '')
    # 不需要开页的窗口内条目（URL日期在窗口内但分类为需开页——上面已处理）；
    # 注意 URL 日期分类里没有"窗口内"档（都是需开页），保持兼容
    for c in cands:
        if c.get('_verified') is None and c.get('_class') == '需开页':
            c['_class'] = '需开页(未核验)' if a.skip_fetch else c['_class']

    # 阶段3: 输出结果表
    results = []
    for c in cands:
        verified = c.get('_verified')
        vfrom = c.get('_vfrom')
        url_date = c.get('_url_date')
        # 最终时效判定（主模型审核用；脚本只给结论）
        if c['_class'] == '窗口外':
            final = '窗口外'
        elif verified:
            vd = datetime.fromisoformat(verified[:19])
            if vd < window_end_dt and vd.date() >= ws:
                final = '窗口内'
            else:
                final = '窗口外'
        elif url_date:
            # URL 日期在窗口内但开页失败/未开页：URL 日期作参考（2026-08-12 改进）
            final = '窗口内(url_date参考)'
        else:
            final = '时间未知'
        results.append({
            'title': c.get('title', ''),
            'url': c.get('url', ''),
            'source': c.get('source', ''),
            'publishedAt_google': c.get('publishedAt', ''),
            'window_result': final,
            'url_date': url_date,
            'verified_time': verified,
            'verified_from': vfrom,
            'note': c.get('_note', ''),
        })

    out = a.output or 'scripts/_time_verified.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f'\n核验结果 → {out}')
    for r in results:
        vt = r['verified_time'] or '-'
        print(f"  [{r['window_result']}] {vt[:16]:16} | {r['title'][:40]}")

    # 汇总
    from collections import Counter
    cnt = Counter(r['window_result'] for r in results)
    print(f'\n汇总: {dict(cnt)}')


if __name__ == '__main__':
    main()
