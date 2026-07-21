#!/usr/bin/env python3
"""
szrehot 全量数据采集入口
依次调用 RSS 采集器 + 微信公众号采集器，合并输出统一 JSON。

用法：
    python fetch_all.py --output ./data --days 1
    python fetch_all.py --output ./data --sources policy,market --with-weixin --with-tavily
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from rss_fetcher import (fetch_rss, deduplicate, filter_by_time,
                          filter_sz_realestate_related, RSS_SOURCES)
from weixin_fetcher import search_weixin, WEIXIN_SEARCH_KEYWORDS, DEFAULT_KEYWORD_TEMPLATE


def main():
    parser = argparse.ArgumentParser(description="szrehot 全量数据采集器")
    parser.add_argument("--output", "-o", default="./data", help="输出目录")
    parser.add_argument("--days", "-d", type=int, default=1, help="采集最近 N 天数据")
    parser.add_argument("--sources", "-s", default=None, help="只采集指定分类，逗号分隔")
    parser.add_argument("--all", action="store_true", help="不过滤深圳房产相关，保留所有条目")
    parser.add_argument("--with-weixin", action="store_true", help="同时采集微信公众号")
    parser.add_argument("--with-tavily", action="store_true", help="微信全文使用 Tavily 兜底")
    parser.add_argument("--weixin-keywords", default=None, help="自定义微信搜索关键词（逗号分隔）")
    parser.add_argument("--max-entries", type=int, default=30, help="每个 RSS 源最多条目数")
    parser.add_argument("--delay", type=float, default=1.0, help="RSS 源间延迟秒数")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # ========== Step 1: RSS 采集 ==========
    print("=" * 50)
    print("Step 1: RSS 采集")
    print("=" * 50)

    sources = RSS_SOURCES
    if args.sources:
        cats = set(args.sources.split(","))
        sources = [s for s in sources if s["category"] in cats]

    rss_items = []
    for source in sources:
        items = fetch_rss(source, max_entries=args.max_entries, request_delay=args.delay)
        rss_items.extend(items)

    print(f"\nRSS 原始采集: {len(rss_items)} 条")

    # ========== Step 2: 微信公众号采集（可选）==========
    weixin_items = []
    if args.with_weixin:
        print("\n" + "=" * 50)
        print("Step 2: 微信公众号采集")
        print("=" * 50)

        if args.weixin_keywords:
            queries = [k.strip() for k in args.weixin_keywords.split(",") if k.strip()]
        else:
            now = datetime.now()
            queries = [DEFAULT_KEYWORD_TEMPLATE.format(keyword=kw, year=now.year, month=now.month)
                       for kw in WEIXIN_SEARCH_KEYWORDS]

        for query in queries:
            articles = search_weixin(query, with_tavily=args.with_tavily)
            weixin_items.extend(articles)

        print(f"\n微信采集: {len(weixin_items)} 条")

    # ========== Step 3: 合并 + 去重 + 过滤 ==========
    print("\n" + "=" * 50)
    print("Step 3: 合并处理")
    print("=" * 50)

    all_items = rss_items + weixin_items
    print(f"合并: {len(all_items)} 条")

    all_items = deduplicate(all_items)
    print(f"去重后: {len(all_items)} 条")

    all_items = filter_by_time(all_items, args.days)
    print(f"时间过滤后: {len(all_items)} 条")

    if not args.all:
        all_items = filter_sz_realestate_related(all_items)
        print(f"深圳房产相关: {len(all_items)} 条")

    # 按发布时间排序
    all_items.sort(key=lambda x: datetime.fromisoformat(
        (x.get("publishedAt") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
    ), reverse=True)

    # ========== Step 4: 保存 ==========
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(args.output, f"szrehot_data_{timestamp}.json")

    result = {
        "count": len(all_items),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "rss_sources": len(sources),
        "weixin_enabled": args.with_weixin,
        "tavily_enabled": args.with_tavily,
        "items": all_items,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    latest_file = os.path.join(args.output, "latest_szrehot_data.json")
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 全量采集完成:")
    print(f"   总计: {len(all_items)} 条")
    print(f"   文件: {output_file}")
    print(f"   Latest: {latest_file}")


if __name__ == "__main__":
    main()
