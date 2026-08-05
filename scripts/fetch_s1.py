#!/usr/bin/env python3
"""S1 RSSHub 采集器 — Codex 适配版

拉取 14 路 RSSHub 通用财经媒体，过滤深圳相关条目，输出统一 JSON。

用法:
    python fetch_s1.py                 # 打印人类可读摘要到 stdout
    python fetch_s1.py --json          # 输出统一 JSON 到 stdout（供主模型消费）
    python fetch_s1.py --json -o out.json   # 写入文件

镜像：首选 https://hub.slarker.me，备用 https://rsshub.liumingye.cn
路由返回 HTML 欢迎页 = 路由不存在，跳过。串行 + 0.5s 间隔，尊重源站限流。
已从 curl.exe 迁移到 requests（与 fetch_s15.py 一致），避免 schannel SSL 问题。
"""
import argparse, json, re, sys, time
sys.stdout.reconfigure(encoding='utf-8')

try:
    import requests
except ImportError:
    print('ERROR: requests 未安装，请 pip install requests', flush=True)
    sys.exit(3)

TIMEOUT = 15
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/150.0 Safari/537.36'
}

ROUTES = [
    ('/cls/telegraph', '财联社电报'),
    ('/wallstreetcn/news/global', '华尔街见闻'),
    ('/caixin/latest', '财新最新'),
    ('/caixin/article', '财新文章'),
    ('/yicai/brief', '第一财经快讯'),
    ('/yicai/news', '第一财经新闻'),
    ('/36kr/newsflashes', '36氪快讯'),
    ('/36kr/hot-list', '36氪热榜'),
    ('/guancha/headline', '观察者网'),
    ('/thepaper/list/25457', '澎湃新闻'),
    ('/thepaper/sidebar', '澎湃侧栏'),
    ('/ifeng/news', '凤凰新闻'),
    ('/people/env', '人民网'),
    ('/zhihu/hot', '知乎热榜'),
]
BASE = 'https://hub.slarker.me'
BASE_FALLBACK = 'https://rsshub.liumingye.cn'

# 深圳地域 + 房产主题关键词（用于从通用财经 RSS 中过滤）
SZ_KW = [
    '深圳', 'Shenzhen', 'szhome', '前海', '南山', '福田', '罗湖', '宝安',
    '龙岗', '龙华', '光明', '坪山', '盐田', '大鹏',
    '住建', '公积金', '城市更新', '旧改', '土拍', '安居', '商品房',
    '保障房', '楼市', '房价', '万科', '华润置地', '招商蛇口', '房地产', '地产',
]


def fetch_route(route, media_name, base):
    """拉取单一路由，返回 (items, status_str)。items 为统一格式列表。"""
    url = base + route
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
    except requests.exceptions.Timeout:
        return [], f'TIMEOUT {route}'
    except Exception as e:
        return [], f'ERR {route}: {type(e).__name__}'

    if r.status_code != 200:
        return [], f'HTTP {r.status_code} {route}'

    text = r.text
    head = text[:500].lower()
    # RSSHub 路由不存在时返回 HTML 欢迎页
    if not text or '<!doctype html' in head or ('<html' in head and '<rss' not in head and '<feed' not in head):
        return [], f'SKIP {route} (HTML/empty)'

    items = re.findall(r'<item>.*?</item>', text, re.DOTALL)
    if not items:
        items = re.findall(r'<entry>.*?</entry>', text, re.DOTALL)

    result = []
    for item in items:
        title_m = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.DOTALL)
        link = ''
        link_m = re.search(r'<link[^>]*href=["\x27]([^"\x27>]+)["\x27]', item, re.DOTALL)
        if link_m:
            link = link_m.group(1).strip()
        else:
            link_m2 = re.search(r'<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>', item, re.DOTALL)
            if link_m2:
                link = link_m2.group(1).strip()

        pub_m = re.search(r'<pubDate>(.*?)</pubDate>|<published>(.*?)</published>|<updated>(.*?)</updated>', item, re.DOTALL)
        desc_m = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', item, re.DOTALL)

        title = title_m.group(1).strip() if title_m else ''
        pub = ''
        if pub_m:
            pub = (pub_m.group(1) or pub_m.group(2) or pub_m.group(3) or '').strip()
        desc = desc_m.group(1).strip() if desc_m else ''
        desc_clean = re.sub(r'<[^>]+>', '', desc)[:200]

        if not title:
            continue
        # 深圳相关过滤（标题+摘要联合判断）
        combined = title + ' ' + desc_clean
        if not any(k in combined for k in SZ_KW):
            continue

        result.append({
            'title': title,
            'url': link,
            'source': media_name,
            'publishedAt': pub,        # RSS 原始时间，主模型后续从全文覆盖
            'summary': desc_clean,
            'origin': f'S1:{route}',
        })

    return result, f'OK {route}: {len(result)} SZ items'


def run(base=None):
    """采集全部路由，base 失败则回退备用镜像。返回统一 items 列表。

    回退判定：路由本身失败（SKIP/ERR/TIMEOUT/HTTP非200 = HTML欢迎页/超时/异常）才换镜像；
    路由成功但 0 条深圳相关不算失败，不回退。
    """
    bases = [base] if base else [BASE, BASE_FALLBACK]
    all_items = []
    for route, media in ROUTES:
        got = False
        for b in bases:
            items, status = fetch_route(route, media, b)
            print(status, flush=True)
            # SKIP/ERR/TIMEOUT/HTTP = 路由失败，试下一个镜像；OK（含0条）= 路由成功，不回退
            if not status.startswith(('SKIP', 'ERR', 'TIMEOUT', 'HTTP')):
                all_items.extend(items)
                got = True
                break
        if not got:
            print(f'FAIL {route} (all mirrors)', flush=True)
        time.sleep(0.5)
    return all_items


def main():
    ap = argparse.ArgumentParser(description='S1 RSSHub 采集器')
    ap.add_argument('--json', action='store_true', help='输出统一 JSON 到 stdout')
    ap.add_argument('-o', '--output', help='写入指定文件（JSON）')
    ap.add_argument('--base', help='指定 RSSHub 镜像 base，默认主备自动回退')
    args = ap.parse_args()

    items = run(args.base)
    print(f'\n=== TOTAL SZ items from S1: {len(items)} ===', flush=True)

    if args.json or args.output:
        payload = {'source': 'S1', 'count': len(items), 'items': items}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f'写入 {args.output}', flush=True)
        if args.json:
            print(text)
    else:
        for i, it in enumerate(items, 1):
            print(f'{i}. [{it["source"]}] {it["title"][:80]} | {it["url"][:80]}')


if __name__ == '__main__':
    main()