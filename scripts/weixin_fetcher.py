#!/usr/bin/env python3
"""
szrehot 微信公众号文章采集器
通过搜狗微信搜索获取深圳房产相关公众号文章。

主力：Codex 浏览器（由 SKILL.md 指令驱动 Agent 操作）
脚本模式：Scrapling StealthyFetcher + Tavily 兜底（可选）

用法：
    python weixin_fetcher.py --output ./data --keywords "深圳楼市 2026年7月"
    python weixin_fetcher.py --output ./data --keywords "深圳楼市 2026年7月" --with-tavily
"""

import argparse
import json
import os
import re
import sys
import time
import html as _html_mod
import urllib.parse as _up
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# ========== 配置 ==========

WEIXIN_SEARCH_KEYWORDS = [
    "深圳楼市", "深圳房价", "深圳土拍", "深圳买房",
    "深圳保障房", "深圳公积金", "深圳旧改", "深圳城市更新",
    "深圳工改工", "深圳棚改",
]

# 默认搜索词模板（脚本模式使用）
DEFAULT_KEYWORD_TEMPLATE = "{keyword} {year}年{month}月"


def _load_codex_env() -> dict:
    """读取 codex_env.json 配置"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codex_env.json")
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8-sig') as f:
            return json.load(f)
    return {}


def _get_tavily_key() -> str:
    """获取 Tavily API Key"""
    key = os.environ.get('TAVILY_API_KEY', '')
    if not key:
        key = _load_codex_env().get('tavily_api_key', '')
    return key


def build_search_keywords(year: int = None, month: int = None) -> List[str]:
    """构建带时间限定的搜索关键词列表"""
    now = datetime.now()
    y = year or now.year
    m = month or now.month
    return [DEFAULT_KEYWORD_TEMPLATE.format(keyword=kw, year=y, month=m) for kw in WEIXIN_SEARCH_KEYWORDS]


def build_sogou_url(query: str) -> str:
    """构造搜狗微信搜索 URL"""
    encoded = _up.quote(query)
    return f"https://weixin.sogou.com/weixin?type=2&query={encoded}"


def _parse_sogou_results(page_html: str) -> List[Dict]:
    """从搜狗微信搜索结果页 HTML 提取文章元数据"""
    articles = []
    seen_hrefs = set()

    h3_pattern = re.compile(
        r'<h3>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>\s*</h3>'
        r'(.*?)'
        r'(?=<h3>|</div>\s*<div|</div>\s*</div>|$)',
        re.DOTALL
    )

    for m in h3_pattern.finditer(page_html):
        href = _html_mod.unescape(m.group(1))
        title_html = m.group(2)
        rest_html = m.group(3)

        title = re.sub(r'<[^>]+>', '', title_html).strip()
        title = title.replace('red_beg', '').replace('red_end', '').strip()
        if not title or href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        summary = ''
        sum_m = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', rest_html, re.DOTALL)
        if sum_m:
            summary = re.sub(r'<[^>]+>', '', sum_m.group(1)).strip()
            summary = summary.replace('red_beg', '').replace('red_end', '').strip()

        account = ''
        acc_m = re.search(r'class="all-time-y2"[^>]*>(.*?)</span>', rest_html, re.DOTALL)
        if acc_m:
            account = re.sub(r'<[^>]+>', '', acc_m.group(1)).strip()

        pub_time = ''
        time_m = re.search(r'class="s2"[^>]*>(.*?)</span>', rest_html, re.DOTALL)
        if time_m:
            pub_time = re.sub(r'<[^>]+>', '', time_m.group(1)).strip()

        # 只保留 mp.weixin.qq.com 链接
        full_url = href
        if not full_url.startswith('http'):
            full_url = 'https://weixin.sogou.com' + href

        articles.append({
            "title": title,
            "url": full_url,
            "summary": summary,
            "account": account,
            "publishedAt": pub_time,
            "sourceName": f"微信公众号·{account}" if account else "微信公众号",
            "category": "insights",
            "content_source": "sogou_metadata",
            "full_content": "",
        })

    return articles


def _scrapling_sogou_search(query: str) -> List[Dict]:
    """使用 Scrapling 抓取搜狗微信搜索结果页（脚本模式）"""
    search_url = build_sogou_url(query)
    try:
        import scrapling
        resp = scrapling.StealthyFetcher.fetch(search_url, headless=True, timeout=20000)
        page_html = resp.html_content
    except Exception:
        try:
            resp = scrapling.DynamicFetcher.fetch(search_url, headless=True, timeout=20000)
            page_html = resp.html_content
        except Exception:
            return []

    if not page_html or 'antispider' in page_html.lower():
        return []

    return _parse_sogou_results(page_html)


def _tavily_find_fulltext(title: str, api_key: str) -> Tuple[str, str]:
    """使用 Tavily 按标题跨站搜索全文（兜底）"""
    import urllib.request
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    payload = json.dumps({
        'api_key': api_key,
        'query': title,
        'search_depth': 'advanced',
        'max_results': 3,
        'include_raw_content': True,
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.tavily.com/search',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return '', ''

    results = data.get('results', [])
    if not results:
        return '', ''

    for best in results:
        source_url = best.get('url', '')
        raw = best.get('raw_content', '') or ''
        content = best.get('content', '') or ''
        text = raw if len(raw) > len(content) else content
        if text and len(text) > 100:
            return text[:5000], source_url

    return '', ''


def _scrapling_fetch_fulltext(url: str) -> str:
    """使用 Scrapling 获取微信文章全文（脚本模式）"""
    try:
        import scrapling
        resp = scrapling.DynamicFetcher.fetch(url, headless=True, timeout=20000)
        html_content = resp.html_content
        # 简单提取正文
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:5000] if text else ''
    except Exception:
        return ''


def classify_weixin_article(title: str, summary: str = "") -> str:
    """根据标题和摘要分类微信文章"""
    text = f"{title} {summary}"

    policy_kws = ["限购", "限贷", "公积金", "保障房", "住建", "调控", "松绑", "落户", "人才房", "安居房", "利率", "首付", "契税"]
    for kw in policy_kws:
        if kw in text:
            return "policy"

    urban_kws = ["城市更新", "旧改", "棚改", "拆除重建", "综合整治", "旧村改造", "工业上楼", "工改工", "白石洲", "岗厦", "华富村"]
    for kw in urban_kws:
        if kw in text:
            return "urban_renewal"

    market_kws = ["房价", "成交", "土拍", "挂牌", "二手房", "新房", "租金", "去化", "楼面价", "溢价", "流拍"]
    for kw in market_kws:
        if kw in text:
            return "market"

    company_kws = ["万科", "华润置地", "招商蛇口", "中海", "金地", "华侨城", "深业", "龙光", "佳兆业",
                   "卓越", "鸿荣源", "星河", "特发", "恒裕", "合正"]
    for kw in company_kws:
        if kw in text:
            return "company"

    research_kws = ["研报", "白皮书", "行业报告", "中指", "克而瑞", "贝壳研究院", "市场预测"]
    for kw in research_kws:
        if kw in text:
            return "research"

    return "insights"


def search_weixin(query: str, with_tavily: bool = False, max_results: int = 10) -> List[Dict]:
    """搜狗微信搜索 + 全文获取"""
    print(f"🔍 搜狗微信搜索: {query}")

    # Step 1: 搜索结果
    articles = _scrapling_sogou_search(query)
    if not articles:
        print(f"   ⚠️ 搜索无结果或被频控")
        return []

    print(f"   搜索结果: {len(articles)} 条")

    # Step 2: 分类
    for art in articles:
        art["category"] = classify_weixin_article(art.get("title", ""), art.get("summary", ""))

    # Step 3: 全文获取
    tavily_key = _get_tavily_key() if with_tavily else ""

    for art in articles[:max_results]:
        url = art.get("url", "")
        title = art.get("title", "")

        # 主力：Scrapling 获取全文
        full_content = ""
        if url and "mp.weixin.qq.com" in url:
            full_content = _scrapling_fetch_fulltext(url)
            if full_content:
                art["full_content"] = full_content
                art["content_source"] = "scrapling_fulltext"
                continue

        # 兜底：Tavily 跨站搜索全文
        if with_tavily and tavily_key and title:
            content, source_url = _tavily_find_fulltext(title, tavily_key)
            if content:
                art["full_content"] = content
                art["content_source"] = "tavily_crosspost"
                if source_url:
                    art["crosspost_url"] = source_url
                continue

        # 都失败：只用摘要
        art["content_source"] = "summary_fallback"

        time.sleep(1)

    return articles[:max_results]


def main():
    parser = argparse.ArgumentParser(description="szrehot 微信公众号文章采集器")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--keywords", "-k", default=None, help="搜索关键词（逗号分隔），留空使用默认+当前时间")
    parser.add_argument("--year", type=int, default=None, help="搜索年份（默认当前年）")
    parser.add_argument("--month", type=int, default=None, help="搜索月份（默认当前月）")
    parser.add_argument("--with-tavily", action="store_true", help="启用 Tavily 全文兜底")
    parser.add_argument("--max-results", type=int, default=10, help="每个关键词最多结果数")
    args = parser.parse_args()

    # 构建搜索关键词
    if args.keywords:
        queries = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        queries = build_search_keywords(args.year, args.month)

    print(f"=== szrehot 微信公众号采集器 ===")
    print(f"搜索关键词: {len(queries)} 个")
    print(f"Tavily 兜底: {'是' if args.with_tavily else '否'}")
    print()

    # 采集
    all_articles = []
    for query in queries:
        articles = search_weixin(query, with_tavily=args.with_tavily, max_results=args.max_results)
        all_articles.extend(articles)
        time.sleep(2)  # 关键词间延迟，尊重限流

    # 去重（按标题）
    seen_titles = set()
    unique_articles = []
    for art in all_articles:
        title = art.get("title", "")
        if title not in seen_titles:
            seen_titles.add(title)
            unique_articles.append(art)

    print(f"\n原始采集: {len(all_articles)} 条")
    print(f"去重后: {len(unique_articles)} 条")

    # 保存
    os.makedirs(args.output, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(args.output, f"weixin_data_{timestamp}.json")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "count": len(unique_articles),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "weixin",
            "queries": queries,
            "items": unique_articles,
        }, f, ensure_ascii=False, indent=2)

    latest_file = os.path.join(args.output, "latest_weixin_data.json")
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump({
            "count": len(unique_articles),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "weixin",
            "queries": queries,
            "items": unique_articles,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 微信数据已保存:")
    print(f"   {output_file}")
    print(f"   {latest_file}")


if __name__ == "__main__":
    main()
