#!/usr/bin/env python3
"""
szrehot 日报生成器
聚合多源数据（RSS + 微信），生成按六类分版的日报 JSON + Markdown。

用法：
    python daily_generator.py --input ./data --output ./daily
    python daily_generator.py --date 2026-07-21 --output ./daily --markdown
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


# ========== 六类分类关键词映射 ==========

CATEGORY_RULES = {
    "policy": {
        "label": "政策法规",
        "icon": "🏛",
        "keywords": ["限购", "限贷", "限售", "公积金", "保障房", "住建", "规自",
                      "预售", "备案价", "土拍规则", "调控", "松绑", "落户",
                      "人才房", "安居房", "旧改", "城市更新", "棚改",
                      "房产税", "契税", "LPR", "利率", "首付", "三道红线"],
    },
    "market": {
        "label": "市场行情",
        "icon": "📊",
        "keywords": ["房价", "成交", "挂牌", "土拍", "溢价", "流拍", "楼面价",
                      "二手房", "新房", "租金", "去化", "库存", "量价",
                      "区域", "板块", "热点片区", "学位房", "地铁房"],
    },
    "urban_renewal": {
        "label": "城市更新",
        "icon": "🏗",
        "keywords": ["城市更新", "旧改", "棚改", "拆除重建", "综合整治",
                      "旧村改造", "旧住宅区", "工业上楼", "工改工", "工改居",
                      "城市更新单元", "更新意愿", "签约率", "搬迁", "回迁",
                      "规划修改", "法定图则", "专项规划", "实施主体",
                      "大冲旧改", "湖贝旧改", "华富村", "岗厦村",
                      "白石洲", "蔡屋围", "上沙下沙", "水围", "皇岗"],
    },
    "company": {
        "label": "标杆公司",
        "icon": "🏢",
        "keywords": ["万科", "华润置地", "招商蛇口", "中海地产", "金地集团",
                      "华侨城", "深业集团", "深圳地铁", "龙光", "佳兆业",
                      "卓越集团", "鸿荣源", "星河集团", "特发集团", "恒裕", "合正",
                      "业绩", "拿地", "融资", "偿债", "交付", "暴雷",
                      "重组", "增持", "减持"],
    },
    "research": {
        "label": "研究报告",
        "icon": "📑",
        "keywords": ["研报", "研究报告", "白皮书", "行业报告", "策略报告",
                      "市场预测", "中指", "克而瑞", "贝壳研究院", "戴德梁行",
                      "分析师", "评级", "目标价"],
    },
    "insights": {
        "label": "观点洞察",
        "icon": "💡",
        "keywords": ["观点", "经验", "建议", "如何", "方法论", "踩盘",
                      "选房", "避坑", "投资", "自住", "改善", "刚需",
                      "学区", "通勤", "配套"],
    },
}


# ========== 双视角评分 ==========

SZ_REAL_ESTATE_KWS = [
    "深圳", "楼市", "房价", "土拍", "成交", "二手房", "新房",
    "南山", "福田", "宝安", "龙岗", "罗湖", "光明", "坪山", "盐田",
    "前海", "后海", "蛇口", "龙华",
]

AUTHORITY_SOURCES = {
    "high": ["住建局", "规自委", "政府", "官方", "公告", "央行", "银保监"],
    "medium": ["财新", "第一财经", "财联社", "华尔街见闻", "澎湃", "人民网", "凤凰"],
    "low": ["公众号", "自媒体", "知乎", "36氪", "观察者网"],
}


def _score_relevance(title: str, summary: str) -> int:
    """行业相关性 / 购房相关性（0-25）"""
    text = f"{title} {summary}"
    score = 0
    sz_count = sum(1 for kw in SZ_REAL_ESTATE_KWS if kw in text)
    if sz_count >= 3:
        score = 25
    elif sz_count >= 2:
        score = 20
    elif sz_count >= 1:
        score = 15
    else:
        # 通用房产相关
        general_kws = ["房地产", "楼市", "房价", "土拍", "限购", "公积金"]
        if any(kw in text for kw in general_kws):
            score = 10
    return score


def _score_authority(source_name: str) -> int:
    """来源权威性（0-20）"""
    for kw in AUTHORITY_SOURCES["high"]:
        if kw in source_name:
            return 20
    for kw in AUTHORITY_SOURCES["medium"]:
        if kw in source_name:
            return 15
    for kw in AUTHORITY_SOURCES["low"]:
        if kw in source_name:
            return 8
    return 10


def _score_impact_practitioner(title: str, summary: str) -> int:
    """从业者业务影响（0-20）"""
    text = f"{title} {summary}"
    high_impact = ["限购", "限贷", "土拍", "预售", "调控", "松绑", "城市更新",
                   "旧改", "签约率", "交付", "暴雷", "重组"]
    medium_impact = ["房价", "成交", "去化", "挂牌", "楼面价", "融资"]
    if any(kw in text for kw in high_impact):
        return 20
    if any(kw in text for kw in medium_impact):
        return 12
    return 5


def _score_impact_homebuyer(title: str, summary: str) -> int:
    """购房者决策影响（0-20）"""
    text = f"{title} {summary}"
    high_impact = ["限购", "限贷", "公积金", "首付", "利率", "保障房", "人才房",
                   "房价", "地铁房", "学位房", "旧改"]
    medium_impact = ["成交", "挂牌", "土拍", "租金", "落户", "安居房"]
    if any(kw in text for kw in high_impact):
        return 20
    if any(kw in text for kw in medium_impact):
        return 12
    return 5


def _score_timeliness(published_at: str) -> int:
    """时效性（0-15）"""
    if not published_at:
        return 5
    try:
        dt_str = published_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours_ago <= 6:
            return 15
        elif hours_ago <= 24:
            return 12
        elif hours_ago <= 72:
            return 8
        else:
            return 3
    except (ValueError, TypeError):
        return 5


def _score_depth(title: str, summary: str) -> int:
    """内容深度 / 信息实用度（0-10）"""
    text = f"{title} {summary}"
    depth_indicators = ["亿", "万", "%", "套", "元", "㎡", "万/㎡", "条", "项"]
    count = sum(1 for ind in depth_indicators if ind in text)
    if count >= 3:
        return 10
    elif count >= 1:
        return 7
    return 3


def _score_actionability(title: str, summary: str, perspective: str) -> int:
    """可行动性（0-10）"""
    text = f"{title} {summary}"
    if perspective == "practitioner":
        action_kws = ["调整", "发布", "实施", "执行", "通知", "公告", "成交", "交付"]
    else:
        action_kws = ["开盘", "加推", "降价", "优惠", "利率", "首付", "公积金", "限购"]
    if any(kw in text for kw in action_kws):
        return 10
    return 3


def score_practitioner(item: dict) -> int:
    """从业者视角评分（0-100）"""
    title = item.get("title", "")
    summary = item.get("summary", "")
    source = item.get("sourceName", "")
    pub_at = item.get("publishedAt", "")

    return (
        _score_relevance(title, summary) +
        _score_authority(source) +
        _score_impact_practitioner(title, summary) +
        _score_timeliness(pub_at) +
        _score_depth(title, summary) +
        _score_actionability(title, summary, "practitioner")
    )


def score_homebuyer(item: dict) -> int:
    """购房者/投资者视角评分（0-100）"""
    title = item.get("title", "")
    summary = item.get("summary", "")
    source = item.get("sourceName", "")
    pub_at = item.get("publishedAt", "")

    return (
        _score_relevance(title, summary) +
        _score_authority(source) +
        _score_impact_homebuyer(title, summary) +
        _score_timeliness(pub_at) +
        _score_depth(title, summary) +
        _score_actionability(title, summary, "homebuyer")
    )


# ========== 分类与日报生成 ==========

def classify_item(title: str, summary: str = "", default_category: str = "market") -> str:
    """基于规则对条目进行分类"""
    text = f"{title} {summary}".lower()

    for cat_slug, cat_info in CATEGORY_RULES.items():
        for kw in cat_info["keywords"]:
            if kw.lower() in text:
                return cat_slug

    return default_category


def generate_lead(sections: List[Dict]) -> Dict:
    """生成日报导语"""
    total_items = sum(len(s.get("items", [])) for s in sections)
    active_sections = [s for s in sections if s.get("items")]

    if not active_sections:
        return {"title": "暂无重大动态", "leadParagraph": "今日深圳楼市暂无重大动态更新。"}

    section_summary = "、".join(
        f"{s['label']}{len(s['items'])}条" for s in active_sections
    )

    top_section = max(active_sections, key=lambda s: len(s["items"]))

    lead_title = f"今日深圳楼市共{total_items}条动态，{top_section['label']}最活跃"
    lead_paragraph = f"今日深圳楼市共收录{total_items}条动态，涉及{section_summary}。重点关注{top_section['label']}板块。"

    return {"title": lead_title, "leadParagraph": lead_paragraph}


def load_rss_data(input_dir: str) -> List[Dict]:
    """从 RSS 采集结果加载数据"""
    latest_file = os.path.join(input_dir, "latest_rss_data.json")
    if not os.path.exists(latest_file):
        files = [f for f in os.listdir(input_dir) if f.startswith("rss_data_") and f.endswith(".json")]
        if files:
            files.sort(reverse=True)
            latest_file = os.path.join(input_dir, files[0])
        else:
            print(f"[日报] 未找到 RSS 数据文件在 {input_dir}")
            return []

    with open(latest_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("items", [])


def load_weixin_data(input_dir: str) -> List[Dict]:
    """从微信采集结果加载数据"""
    latest_file = os.path.join(input_dir, "latest_weixin_data.json")
    if not os.path.exists(latest_file):
        files = [f for f in os.listdir(input_dir) if f.startswith("weixin_data_") and f.endswith(".json")]
        if files:
            files.sort(reverse=True)
            latest_file = os.path.join(input_dir, files[0])
        else:
            return []

    with open(latest_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("items", [])


def merge_data(rss_items: List[Dict], weixin_items: List[Dict]) -> List[Dict]:
    """合并多个数据源的条目，按 URL 去重"""
    seen_urls = set()
    merged = []

    for item in rss_items:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(item)

    for item in weixin_items:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(item)
        elif not url:
            merged.append(item)

    return merged


def build_daily(items: List[Dict], date_str: Optional[str] = None) -> Dict:
    """将条目按六类分版构建日报"""
    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    # 分类
    for item in items:
        if "category" not in item or not item["category"]:
            item["category"] = classify_item(
                item.get("title", ""),
                item.get("summary", "")
            )
        # 双视角评分
        item["score_practitioner"] = score_practitioner(item)
        item["score_homebuyer"] = score_homebuyer(item)

    # 按分类分组
    sections = []
    for cat_slug, cat_info in CATEGORY_RULES.items():
        cat_items = [item for item in items if item.get("category") == cat_slug]
        # 按从业者评分排序
        cat_items.sort(key=lambda x: x.get("score_practitioner", 0), reverse=True)
        sections.append({
            "slug": cat_slug,
            "label": cat_info["label"],
            "icon": cat_info["icon"],
            "items": cat_items,
        })

    # 导语
    lead = generate_lead(sections)

    # 快讯（市场行情中评分最高的前5条）
    flashes = []
    for section in sections:
        if section["slug"] == "market":
            for item in section["items"][:5]:
                flashes.append({
                    "title": item["title"],
                    "sourceName": item["sourceName"],
                    "sourceUrl": item.get("url", ""),
                    "publishedAt": item.get("publishedAt", ""),
                })

    # 双视角 TOP5
    sorted_by_practitioner = sorted(items, key=lambda x: x.get("score_practitioner", 0), reverse=True)[:5]
    sorted_by_homebuyer = sorted(items, key=lambda x: x.get("score_homebuyer", 0), reverse=True)[:5]

    daily = {
        "date": target_date,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "windowStart": f"{target_date}T00:00:00.000Z",
        "windowEnd": f"{target_date}T23:59:59.999Z",
        "lead": lead,
        "sections": sections,
        "flashes": flashes,
        "top_practitioner": [{"title": i["title"], "score": i["score_practitioner"]} for i in sorted_by_practitioner],
        "top_homebuyer": [{"title": i["title"], "score": i["score_homebuyer"]} for i in sorted_by_homebuyer],
    }

    return daily


def daily_to_markdown(daily: Dict) -> str:
    """将日报 JSON 转为 Markdown 格式"""
    lines = []
    lines.append(f"**深圳楼市日报 · {daily['date']}**")
    lines.append("")

    # 导语
    if daily.get("lead"):
        lead = daily["lead"]
        if lead.get("title"):
            lines.append(f"> {lead['title']}")
            lines.append(">")
            if lead.get("leadParagraph"):
                lines.append(f"> {lead['leadParagraph']}")
            lines.append("")

    # 各版块
    counter = 1
    for section in daily.get("sections", []):
        items = section.get("items", [])
        if not items:
            continue

        lines.append(f"## {section['icon']} {section['label']}")
        for item in items:
            title = item.get("title", "")
            source = item.get("sourceName", "")
            summary = item.get("summary", "")
            url = item.get("url", "")
            score_p = item.get("score_practitioner", 0)
            score_h = item.get("score_homebuyer", 0)

            line = f"{counter}. **{title}** — {source}"
            # 高分标注
            if score_p >= 70 or score_h >= 70:
                line += " ⭐"
            lines.append(line)

            if summary:
                short_summary = summary[:50] + ("..." if len(summary) > 50 else "")
                lines.append(f"   {short_summary}")

            if url:
                lines.append(f"   {url}")

            counter += 1

        lines.append("")

    # 快讯
    flashes = daily.get("flashes", [])
    if flashes:
        lines.append("## ⚡ 快讯")
        for flash in flashes:
            title = flash.get("title", "")
            source = flash.get("sourceName", "")
            url = flash.get("sourceUrl", "")
            lines.append(f"- **{title}** — {source}")
            if url:
                lines.append(f"  {url}")
        lines.append("")

    # 双视角 TOP5
    top_p = daily.get("top_practitioner", [])
    top_h = daily.get("top_homebuyer", [])
    if top_p:
        lines.append("---")
        lines.append("")
        lines.append("📊 **从业者 TOP5**")
        for i, item in enumerate(top_p, 1):
            lines.append(f"  {i}. {item['title']}（{item['score']}分）")
        lines.append("")

    if top_h:
        lines.append("🏠 **购房者 TOP5**")
        for i, item in enumerate(top_h, 1):
            lines.append(f"  {i}. {item['title']}（{item['score']}分）")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="szrehot 日报生成器")
    parser.add_argument("--input", "-i", default="./data", help="数据目录")
    parser.add_argument("--output", "-o", default="./daily", help="日报输出目录")
    parser.add_argument("--date", "-d", default=None, help="指定日报日期 YYYY-MM-DD")
    parser.add_argument("--markdown", "-m", action="store_true", help="同时输出 Markdown 格式")
    args = parser.parse_args()

    # 加载数据
    rss_items = load_rss_data(args.input)
    print(f"[日报] 加载 RSS 数据: {len(rss_items)} 条")

    weixin_items = load_weixin_data(args.input)
    print(f"[日报] 加载微信数据: {len(weixin_items)} 条")

    # 合并去重
    all_items = merge_data(rss_items, weixin_items)
    print(f"[日报] 合并去重后: {len(all_items)} 条")

    # 重新分类（确保一致性）
    for item in all_items:
        if "category" not in item or not item["category"]:
            item["category"] = classify_item(
                item.get("title", ""),
                item.get("summary", "")
            )

    # 构建日报
    daily = build_daily(all_items, args.date)

    # 统计
    total = sum(len(s.get("items", [])) for s in daily["sections"])
    print(f"[日报] 生成日报: {daily['date']}, 共 {total} 条")

    # 保存 JSON
    os.makedirs(args.output, exist_ok=True)
    json_file = os.path.join(args.output, f"daily_{daily['date']}.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)
    print(f"[日报] JSON 已保存: {json_file}")

    # 保存最新日报
    latest_file = os.path.join(args.output, "latest_daily.json")
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)

    # 保存 Markdown
    if args.markdown:
        md_content = daily_to_markdown(daily)
        md_file = os.path.join(args.output, f"daily_{daily['date']}.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"[日报] Markdown 已保存: {md_file}")

    print(f"\n✅ 日报生成完成: {total} 条动态, {len(daily['sections'])} 个版块")


if __name__ == "__main__":
    main()
