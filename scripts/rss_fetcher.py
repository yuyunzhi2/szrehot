#!/usr/bin/env python3
"""
szrehot RSS 数据采集器
从 RSSHub 等公开 RSS 源采集深圳房地产相关资讯，输出统一 JSON 格式。

依赖：feedparser（pip install feedparser）
用法：
    python rss_fetcher.py --output ./data --days 1
    python rss_fetcher.py --output ./data --sources policy,market
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

try:
    import feedparser
except ImportError:
    print("错误：需要 feedparser 库。请运行 pip install feedparser", file=sys.stderr)
    sys.exit(1)

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# ========== RSS 源配置 ==========
DEFAULT_RSSHUB_BASE = "https://rsshub.liumingye.cn"

RSS_SOURCES: List[Dict] = [
    # --- 快讯 ---
    {"slug": "cls/telegraph", "name": "财联社电报", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "yicai/brief", "name": "第一财经快讯", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "36kr/newsflashes", "name": "36氪快讯", "category": "insights", "base": DEFAULT_RSSHUB_BASE},

    # --- 市场 ---
    {"slug": "wallstreetcn/news/global", "name": "华尔街见闻", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "caixin/latest", "name": "财新最新", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "caixin/article", "name": "财新文章", "category": "research", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "yicai/news", "name": "第一财经新闻", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "thepaper/list/25457", "name": "澎湃新闻", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "thepaper/sidebar", "name": "澎湃侧栏", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "ifeng/news", "name": "凤凰新闻", "category": "market", "base": DEFAULT_RSSHUB_BASE},

    # --- 政策 ---
    {"slug": "people/env", "name": "人民网", "category": "policy", "base": DEFAULT_RSSHUB_BASE},

    # --- 深度/观点 ---
    {"slug": "guancha/headline", "name": "观察者网", "category": "research", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "36kr/hot-list", "name": "36氪热榜", "category": "insights", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "zhihu/hot", "name": "知乎热榜", "category": "insights", "base": DEFAULT_RSSHUB_BASE},

    # --- 百度搜索 — 深圳房产定向 ---
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E6%A5%BC%E5%B8%82", "name": "百度-深圳楼市", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E6%88%BF%E4%BB%B7", "name": "百度-深圳房价", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E5%9C%9F%E6%8B%8D", "name": "百度-深圳土拍", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E5%9C%B0%E4%BA%A7%E6%94%BF%E7%AD%96", "name": "百度-地产政策", "category": "policy", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%88%BF%E5%9C%B0%E4%BA%A7", "name": "百度-房地产", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%A5%BC%E5%B8%82", "name": "百度-楼市", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%88%BF%E4%BB%B7", "name": "百度-房价", "category": "market", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E5%9C%9F%E6%8B%8D", "name": "百度-土拍", "category": "market", "base": DEFAULT_RSSHUB_BASE},

    # --- 百度搜索 — 城市更新 ---
    {"slug": "baidu/search/%E5%9F%8E%E5%B8%82%E6%9B%B4%E6%96%B0", "name": "百度-城市更新", "category": "urban_renewal", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E6%97%A7%E6%94%B9", "name": "百度-深圳旧改", "category": "urban_renewal", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E5%9F%8E%E5%B8%82%E6%9B%B4%E6%96%B0", "name": "百度-深圳城市更新", "category": "urban_renewal", "base": DEFAULT_RSSHUB_BASE},

    # --- 百度搜索 — 深圳标杆公司 ---
    {"slug": "baidu/search/%E4%B8%87%E7%A7%91", "name": "百度-万科", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E5%8D%8E%E6%B6%A6%E7%BD%AE%E5%9C%B0", "name": "百度-华润置地", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%8B%9B%E5%95%86%E8%9B%87%E5%8F%A3", "name": "百度-招商蛇口", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E4%B8%AD%E6%B5%B7%E5%9C%B0%E4%BA%A7", "name": "百度-中海地产", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E9%87%91%E5%9C%B0%E9%9B%86%E5%9B%A2", "name": "百度-金地集团", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E5%8D%8E%E4%BE%A8%E5%9F%8E", "name": "百度-华侨城", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E4%B8%9A%E9%9B%86%E5%9B%A2", "name": "百度-深业集团", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%B7%B1%E5%9C%B3%E5%9C%B0%E9%93%81", "name": "百度-深圳地铁", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E9%BE%99%E5%85%89", "name": "百度-龙光", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E4%BD%B3%E5%85%86%E4%B8%9A", "name": "百度-佳兆业", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E5%8D%93%E8%B6%8A%E9%9B%86%E5%9B%A2", "name": "百度-卓越集团", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E9%B8%BF%E8%8D%A3%E6%BA%90", "name": "百度-鸿荣源", "category": "company", "base": DEFAULT_RSSHUB_BASE},
    {"slug": "baidu/search/%E6%98%9F%E6%B2%B3%E9%9B%86%E5%9B%A2", "name": "百度-星河集团", "category": "company", "base": DEFAULT_RSSHUB_BASE},
]

# 深圳房地产关键词（用于从通用 RSS 中过滤相关内容）
SZ_REAL_ESTATE_KEYWORDS = [
    "深圳", "楼市", "房价", "土拍", "成交", "挂牌", "二手房", "新房",
    "限购", "限贷", "公积金", "保障房", "安居房", "人才房",
    "旧改", "城市更新", "预售", "备案价", "去化", "楼面价",
    "万科", "保利", "碧桂园", "龙湖", "恒大", "绿城",
    "招商蛇口", "华润置地", "中海", "金茂", "融创",
    "南山", "福田", "宝安", "龙岗", "罗湖", "光明", "坪山", "盐田",
    "前海", "后海", "蛇口", "龙华", "布吉",
]

# 政策相关关键词
POLICY_KEYWORDS = [
    "住建", "规自", "调控", "松绑", "落户", "利率", "LPR",
    "首付", "契税", "房产税", "棚改", "三道红线",
]

# 城市更新关键词
URBAN_RENEWAL_KEYWORDS = [
    "城市更新", "旧改", "棚改", "拆除重建", "综合整治",
    "旧村改造", "工业上楼", "工改工", "工改居",
    "白石洲", "岗厦", "华富村", "湖贝", "大冲",
    "蔡屋围", "上沙", "下沙", "水围", "皇岗",
]


def generate_id(url: str) -> str:
    """基于 URL 生成唯一 ID"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def parse_published_at(entry) -> Optional[str]:
    """从 feedparser entry 提取发布时间，转为 ISO 8601 UTC"""
    for field in ["published_parsed", "created_parsed", "updated_parsed"]:
        parsed = getattr(entry, field, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue
    for field in ["published", "updated", "created"]:
        val = getattr(entry, field, None)
        if val:
            return val
    return None


def classify_title(title: str, default_category: str) -> str:
    """基于标题关键词微调分类"""
    title_text = title or ""

    # 城市更新关键词覆盖（优先于默认分类）
    for kw in URBAN_RENEWAL_KEYWORDS:
        if kw in title_text:
            return "urban_renewal"

    # 政策关键词覆盖
    policy_kws = ["住建", "规自", "限购", "限贷", "限售", "公积金", "保障房",
                  "调控", "松绑", "落户", "预售", "备案价", "房产税", "契税",
                  "LPR", "利率", "首付", "人才房", "安居房"]
    for kw in policy_kws:
        if kw in title_text:
            return "policy"

    # 标杆公司关键词覆盖
    company_kws = ["万科", "华润置地", "招商蛇口", "中海地产", "金地集团",
                   "华侨城", "深业", "深圳地铁", "龙光", "佳兆业",
                   "卓越集团", "鸿荣源", "星河集团", "特发集团", "恒裕", "合正"]
    for kw in company_kws:
        if kw in title_text:
            return "company"

    # 研究报告关键词覆盖
    research_kws = ["研报", "研究报告", "白皮书", "策略报告", "中指", "克而瑞",
                    "分析师", "评级", "目标价", "市场预测"]
    for kw in research_kws:
        if kw in title_text:
            return "research"

    return default_category


def fetch_rss(source: Dict, max_entries: int = 30, request_delay: float = 1.0) -> List[Dict]:
    """从单个 RSS 源获取数据"""
    import urllib.request
    import ssl

    slug = source["slug"]
    name = source["name"]
    category = source["category"]
    base = source.get("base", DEFAULT_RSSHUB_BASE)
    url = f"{base}/{slug}"

    print(f"📡 [{name}] 获取中...")

    # 请求间延迟
    if request_delay > 0:
        time.sleep(request_delay)

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        feed = feedparser.parse(resp.read())

        items = []
        for entry in feed.entries[:max_entries]:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            summary = getattr(entry, "summary", "")
            published_at = parse_published_at(entry)

            if not title:
                continue

            # 用关键词微调分类
            final_category = classify_title(title, category)

            items.append({
                "id": generate_id(link or title),
                "title": title,
                "url": link,
                "summary": summary[:500] if summary else "",
                "sourceName": name,
                "sourceUrl": url,
                "category": final_category,
                "publishedAt": published_at,
            })

        print(f"   ✅ {name}: {len(items)} 条")
        return items

    except Exception as e:
        print(f"   ❌ {name}: {e}")
        return []


def filter_by_time(items: List[Dict], days: int) -> List[Dict]:
    """按时间窗口过滤条目"""
    if days <= 0:
        return items

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filtered = []
    for item in items:
        published_at = item.get("publishedAt")
        if not published_at:
            filtered.append(item)
            continue
        try:
            if isinstance(published_at, str):
                dt_str = published_at.replace("Z", "+00:00")
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    filtered.append(item)
            else:
                filtered.append(item)
        except (ValueError, TypeError):
            filtered.append(item)

    return filtered


def filter_sz_realestate_related(items: List[Dict]) -> List[Dict]:
    """过滤出深圳房地产相关条目"""
    all_keywords = SZ_REAL_ESTATE_KEYWORDS + POLICY_KEYWORDS + URBAN_RENEWAL_KEYWORDS
    filtered = []
    for item in items:
        text = (item.get("title", "") + " " + (item.get("summary") or "")).lower()
        for kw in all_keywords:
            if kw.lower() in text:
                filtered.append(item)
                break
    return filtered


def deduplicate(items: List[Dict]) -> List[Dict]:
    """按 URL 去重"""
    seen_urls = set()
    unique = []
    for item in items:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)
    return unique


def main():
    parser = argparse.ArgumentParser(description="szrehot RSS 数据采集器")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--days", "-d", type=int, default=1, help="采集最近 N 天的数据（默认 1 天）")
    parser.add_argument("--sources", "-s", default=None, help="只采集指定分类的源，逗号分隔（如 policy,market）")
    parser.add_argument("--all", action="store_true", help="不过滤深圳房产相关，保留所有条目")
    parser.add_argument("--max-entries", type=int, default=30, help="每个源最多采集条目数（默认 30）")
    parser.add_argument("--delay", type=float, default=1.0, help="源间请求延迟秒数（默认 1.0）")
    args = parser.parse_args()

    # 筛选源
    sources = RSS_SOURCES
    if args.sources:
        cats = set(args.sources.split(","))
        sources = [s for s in sources if s["category"] in cats]

    print(f"=== szrehot RSS 采集器 ===")
    print(f"源数量: {len(sources)} | 时间窗口: {args.days} 天")
    print()

    # 采集所有源
    all_items = []
    for source in sources:
        items = fetch_rss(source, max_entries=args.max_entries, request_delay=args.delay)
        all_items.extend(items)

    print(f"\n原始采集: {len(all_items)} 条")

    # 去重
    all_items = deduplicate(all_items)
    print(f"去重后: {len(all_items)} 条")

    # 时间过滤
    all_items = filter_by_time(all_items, args.days)
    print(f"时间过滤后: {len(all_items)} 条")

    # 深圳房产相关过滤（除非 --all）
    if not args.all:
        all_items = filter_sz_realestate_related(all_items)
        print(f"深圳房产相关: {len(all_items)} 条")

    # 按发布时间排序
    all_items.sort(
        key=lambda x: datetime.fromisoformat((x.get("publishedAt") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")),
        reverse=True
    )

    # 保存
    os.makedirs(args.output, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(args.output, f"rss_data_{timestamp}.json")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "count": len(all_items),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "rss",
            "days": args.days,
            "items": all_items,
        }, f, ensure_ascii=False, indent=2)

    latest_file = os.path.join(args.output, "latest_rss_data.json")
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump({
            "count": len(all_items),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "rss",
            "days": args.days,
            "items": all_items,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据已保存:")
    print(f"   {output_file}")
    print(f"   {latest_file}")


if __name__ == "__main__":
    main()
