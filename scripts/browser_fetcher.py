#!/usr/bin/env python3
"""
szrehot Codex 浏览器辅助脚本
生成 Codex 浏览器操作指令序列，供 SKILL.md 引用。

不直接执行浏览器操作（由 Agent 通过 Codex 浏览器 skill 执行），
而是提供目标站点 URL 列表和抓取提示词。

用法：
    python browser_fetcher.py --list-sites
    python browser_fetcher.py --generate-urls
"""

import json
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BROWSER_SITES = [
    {
        "name": "深圳住建局",
        "url": "https://zjj.sz.gov.cn",
        "category": "policy",
        "extract_prompt": "提取页面中与房地产相关的最新政策文件、通知公告，包括标题、发布日期、链接",
    },
    {
        "name": "深圳规自委",
        "url": "https://szpl.gov.cn",
        "category": "policy",
        "extract_prompt": "提取土地出让公告、规划公示，包括地块编号、位置、面积、出让方式、日期",
    },
    {
        "name": "贝壳深圳",
        "url": "https://sz.ke.com",
        "category": "market",
        "extract_prompt": "提取深圳二手房/新房市场数据：成交量、挂牌量、均价、热门区域",
    },
    {
        "name": "乐居深圳",
        "url": "https://sz.leju.com",
        "category": "market",
        "extract_prompt": "提取深圳楼盘动态、市场分析文章，包括标题、摘要、日期",
    },
    {
        "name": "房天下深圳",
        "url": "https://sz.fang.com",
        "category": "market",
        "extract_prompt": "提取深圳房产资讯、房价走势、楼盘信息",
    },
]

# 搜狗微信搜索提示词（供 SKILL.md 引用）
WEIXIN_BROWSER_PROMPT = {
    "description": "搜狗微信搜索 — 深圳房产公众号",
    "search_url_template": "https://weixin.sogou.com/weixin?type=2&query={keyword}",
    "time_suffix": "{year}年{month}月",
    "keywords": [
        "深圳楼市", "深圳房价", "深圳土拍", "深圳买房",
        "深圳保障房", "深圳公积金", "深圳旧改", "深圳城市更新",
        "深圳工改工", "深圳棚改",
    ],
    "steps": [
        "1. 打开搜狗微信搜索页面",
        "2. 输入关键词 + 时间限定（如'深圳楼市 2026年7月'）",
        "3. 等待结果加载",
        "4. 提取搜索结果：标题、公众号名、摘要、文章链接",
        "5. 对每篇文章：打开 mp.weixin.qq.com 链接，提取全文",
        "6. 全文获取失败时：使用 Tavily 兜底搜索",
        "7. 搜狗频控/验证码 → 跳过，不阻塞",
    ],
}


def list_sites():
    """列出所有可抓取站点"""
    print("=== szrehot Codex 浏览器可抓取站点 ===\n")
    for site in BROWSER_SITES:
        print(f"📍 {site['name']}")
        print(f"   URL: {site['url']}")
        print(f"   分类: {site['category']}")
        print(f"   提取提示: {site['extract_prompt']}")
        print()

    print("=== 搜狗微信搜索配置 ===\n")
    print(f"关键词: {', '.join(WEIXIN_BROWSER_PROMPT['keywords'])}")
    print(f"时间后缀: {WEIXIN_BROWSER_PROMPT['time_suffix']}")
    print(f"\n步骤:")
    for step in WEIXIN_BROWSER_PROMPT['steps']:
        print(f"  {step}")


def generate_urls():
    """生成今日待抓 URL 列表（JSON 格式）"""
    now = datetime.now()
    year = now.year
    month = now.month

    urls = []
    for site in BROWSER_SITES:
        urls.append({
            "name": site["name"],
            "url": site["url"],
            "category": site["category"],
            "extract_prompt": site["extract_prompt"],
        })

    # 搜狗微信搜索 URL
    for kw in WEIXIN_BROWSER_PROMPT["keywords"]:
        query = f"{kw} {year}年{month}月"
        search_url = WEIXIN_BROWSER_PROMPT["search_url_template"].format(
            keyword=__import__('urllib.parse', fromlist=['quote']).quote(query)
        )
        urls.append({
            "name": f"搜狗微信-{kw}",
            "url": search_url,
            "category": "weixin_search",
            "extract_prompt": f"提取微信文章搜索结果：标题、公众号名、摘要、文章链接。搜索词: {query}",
        })

    result = {
        "generated_at": now.isoformat(),
        "total_urls": len(urls),
        "sites": len(BROWSER_SITES),
        "weixin_searches": len(WEIXIN_BROWSER_PROMPT["keywords"]),
        "urls": urls,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print("用法: python browser_fetcher.py [--list-sites|--generate-urls]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "--list-sites":
        list_sites()
    elif command == "--generate-urls":
        generate_urls()
    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
