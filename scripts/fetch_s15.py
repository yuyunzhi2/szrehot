#!/usr/bin/env python3
"""S1.5 Google Alerts 采集器 — Claude Code 适配版

通过 socks5h://127.0.0.1:10808 代理拉取 8 个 Google Alerts ATOM feed，
解析后输出统一 JSON。代理不可用时以非零退出码退出（主模型据此跳过 S1.5）。

注意：必须用 requests + PySocks（OpenSSL 后端）。curl.exe 的 schannel 后端
对代理出口节点的 TLS 证书验证会失败（exit 35），即使代理本身可用。

用法:
    python fetch_s15.py                 # 打印人类可读摘要
    python fetch_s15.py --json          # 输出统一 JSON 到 stdout
    python fetch_s15.py --json -o out.json

退出码:
    0  成功（含部分 feed 空结果）
    2  代理不可用（10808 端口未监听或代理不通）
    3  requests/PySocks 未安装
"""
import argparse, json, re, sys, time
import urllib.parse as up
sys.stdout.reconfigure(encoding='utf-8')

try:
    import requests
except ImportError:
    print('ERROR: requests 未安装，请 pip install requests', flush=True)
    sys.exit(3)

PROXY = 'socks5h://127.0.0.1:10808'
PROXIES = {'http': PROXY, 'https': PROXY}
TIMEOUT = 20
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/150.0 Safari/537.36'
}

# (Alert 名称, Feed URL) — 见 references/google_alerts.md
ALERTS = [
    ('深圳 城市更新', 'https://www.google.com/alerts/feeds/02703135011315330991/17679522208773290885'),
    ('深圳 房地产 开发', 'https://www.google.com/alerts/feeds/02703135011315330991/6102386517983312706'),
    ('深圳 规划', 'https://www.google.com/alerts/feeds/02703135011315330991/331647959170947056'),
    ('深圳 商品房 销售', 'https://www.google.com/alerts/feeds/02703135011315330991/11832382345329285490'),
    ('深圳 商业', 'https://www.google.com/alerts/feeds/02703135011315330991/4220399693512442495'),
    ('深圳 土地', 'https://www.google.com/alerts/feeds/02703135011315330991/6277879696444141962'),
    ('深圳 消费', 'https://www.google.com/alerts/feeds/02703135011315330991/14096619598796495203'),
    ('深圳 租金', 'https://www.google.com/alerts/feeds/02703135011315330991/12848020173352227376'),
]


def proxy_alive():
    """检测 socks5 代理是否可用（requests + OpenSSL 后端）。

    用 Google generate_204 检测：成功返回 204 空 body。
    超时或非 2xx 均视为不可用。
    """
    try:
        r = requests.get('https://www.google.com/generate_204',
                         proxies=PROXIES, timeout=10, headers=HEADERS)
        return r.status_code in (200, 204)
    except Exception:
        return False


def parse_feed(xml_text, alert_name):
    """解析 ATOM XML，提取 entry 的 title/link/published。返回统一格式列表。"""
    items = []
    for entry in re.findall(r'<entry>.*?</entry>', xml_text, re.DOTALL):
        title_m = re.search(r'<title[^>]*>(.*?)</title>', entry, re.DOTALL)
        link_m = re.search(r'<link[^>]*href=["\x27]([^"\x27>]+)["\x27]', entry, re.DOTALL)
        pub_m = re.search(r'<published>(.*?)</published>|<updated>(.*?)</updated>', entry, re.DOTALL)

        title = title_m.group(1).strip() if title_m else ''
        title = re.sub(r'<[^>]+>', '', title)  # 清理 <b> 高亮标签
        if not title:
            continue

        link = link_m.group(1).strip() if link_m else ''
        # Google 重定向 URL，提取 url= 参数获取真实 URL
        # XML 属性中 & 以 &amp; 实体形式出现，需先还原再做 ?& 匹配
        real_url = link
        link_norm = link.replace('&amp;', '&')
        if 'url=' in link_norm:
            m = re.search(r'[?&]url=([^&]+)', link_norm)
            if m:
                real_url = up.unquote(m.group(1))

        pub = ''
        if pub_m:
            pub = (pub_m.group(1) or pub_m.group(2) or '').strip()

        items.append({
            'title': title,
            'url': real_url,
            'source': f'Google Alert·{alert_name}',
            'publishedAt': pub,
            'summary': '',
            'origin': f'S1.5:{alert_name}',
        })
    return items


def fetch_alert(name, url):
    """拉取单个 Alert feed。返回 (items, status_str)。"""
    try:
        r = requests.get(url, proxies=PROXIES, timeout=TIMEOUT, headers=HEADERS)
    except requests.exceptions.Timeout:
        return [], f'TIMEOUT {name}'
    except Exception as e:
        return [], f'ERR {name}: {type(e).__name__}'
    if r.status_code != 200:
        return [], f'HTTP {r.status_code} {name}'
    text = r.text
    if not text:
        return [], f'EMPTY {name}'
    items = parse_feed(text, name)
    return items, f'OK {name}: {len(items)} items'


def run():
    all_items = []
    for name, url in ALERTS:
        items, status = fetch_alert(name, url)
        print(status, flush=True)
        all_items.extend(items)
        time.sleep(0.5)
    return all_items


def main():
    ap = argparse.ArgumentParser(description='S1.5 Google Alerts 采集器')
    ap.add_argument('--json', action='store_true', help='输出统一 JSON 到 stdout')
    ap.add_argument('-o', '--output', help='写入指定文件（JSON）')
    ap.add_argument('--skip-check', action='store_true', help='跳过代理存活检测（已确认代理可用时用）')
    args = ap.parse_args()

    if not args.skip_check and not proxy_alive():
        print('PROXY_DOWN: socks5h://127.0.0.1:10808 不可用，跳过 S1.5', flush=True)
        sys.exit(2)

    items = run()
    print(f'\n=== TOTAL items from S1.5: {len(items)} ===', flush=True)

    if args.json or args.output:
        payload = {'source': 'S1.5', 'count': len(items), 'items': items}
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
