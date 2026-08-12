#!/usr/bin/env python3
"""szrehot 跨日去重工具 — 维护 dedup_index.jsonl 索引

五轨去重：URL指纹 + 标题2-gram + 实体组合 + 结构化标题 + 实体指纹

用法:
  python dedup.py check        --date YYYY-MM-DD --url URL --title TITLE
  python dedup.py batch-check  --input candidates.json --date YYYY-MM-DD
  python dedup.py record       --date YYYY-MM-DD --url URL --title TITLE
  python dedup.py batch-record --input final.json --date YYYY-MM-DD
  python dedup.py prune        [--days N]
  python dedup.py stats
  python dedup.py rebuild                      # 从已有日报重建索引
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──
OUTPUT_DIR = Path(r"D:\Administrator\Documents\大瑀MD")
INDEX_PATH = OUTPUT_DIR / "dedup_index.jsonl"
DEFAULT_RETENTION_DAYS = 7
TITLE_SIMILARITY_THRESHOLD = 0.6  # 0-1, 越高越宽松


def normalize_url(url: str) -> str:
    """URL 规范化：去 fragment、去常见 tracking 参数、去 trailing slash"""
    if not url:
        return ""
    # 去 fragment
    url = url.split("#")[0]
    # 去 trailing slash
    url = url.rstrip("/")
    # 去常见 tracking 参数
    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                       "utm_content", "from", "isfrom", "nsukey", "sp"}
    if "?" in url:
        base, qs = url.split("?", 1)
        params = [p for p in qs.split("&")
                  if p.split("=")[0] not in tracking_params]
        url = base + ("?" + "&".join(params) if params else "")
    # 去空 query
    url = url.rstrip("?")
    return url


def url_hash(url: str) -> str:
    """URL 的 SHA256 前 16 位"""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def title_shingle_set(title: str) -> set:
    """标题 2-gram 集合，用于 simhash 近似匹配"""
    # 去除标点空格
    cleaned = re.sub(r"[^\w]", "", title)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i+2] for i in range(len(cleaned) - 1)}


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard 相似度"""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0



# ── 实体去重（第三轨） ──

_COMPANY_RE = re.compile(
    r'(?:绿联|万科|华润|招商|中海|金地|华侨城|深业|龙光|佳兆业|卓越|鸿荣源|星河|特发|融创|'
    r'深圳大学|深大|振业)'
    r'(?:科技|集团|地产|控股|置地)?'
)
# 公司名别名归一化（提取后统一，跨媒体不同称呼判同主体）
_COMPANY_ALIAS = {
    '深圳大学': '深大', '深大': '深大',
    '振业': '振业', '深振业': '振业',
}
_PLACE_RE = re.compile(r'(?:前海|南山|福田|罗湖|宝安|龙岗|龙华|光明|坪山|盐田|大鹏|西丽|岗厦|布心|螺岭|皇岗'
                       r'|民治|安托山|长圳|玉塘|沙河|坪地|坑梓|福城|大浪|龙城|横岗|坂田|布吉|南湾|新安|西乡)')
_EVENT_MAP = {
    '竞得': '购地', '受让': '购地', '摘地': '购地', '中标': '购地', '出让': '购地',
    '抢地': '购地', '揽金': '购地', '拍地': '购地',
    '签约': '签约', '获批': '获批', '公示': '公示', '启动': '启动',
    '开工': '开工', '交付': '交付', '入市': '入市', '成交': '成交',
    '完工': '交付', '投用': '交付', '通车': '通车', '奠基': '开工',
}
_EVENT_RE = re.compile(r'(?:' + '|'.join(_EVENT_MAP.keys()) + r')')
_TYPE_MAP = {
    '城市更新': '旧改', '旧改': '旧改', '工改工': '旧改', '宅地': '宅地', '商地': '商地',
    '工业用地': '产业用地', '土地使用权': '产业用地',
    '宿舍': '宿舍', '学生宿舍': '宿舍', '学校': '学校', '九年制学校': '学校', '校区': '学校',
    '铁路': '交通', '高铁': '交通', '地铁': '交通',
}
_TYPE_RE = re.compile(r'(?:' + '|'.join(_TYPE_MAP.keys()) + r')')


def extract_entity_pairs(title: str) -> tuple[list, list, list, list]:
    """提取标题中的实体对：公司名、区名、事件词、项目类型（公司名做别名归一化）"""
    companies = [_COMPANY_ALIAS.get(w, w) for w in _COMPANY_RE.findall(title)]
    places = _PLACE_RE.findall(title)
    events = [_EVENT_MAP.get(w, w) for w in _EVENT_RE.findall(title)]
    types = [_TYPE_MAP.get(w, w) for w in _TYPE_RE.findall(title)]
    return companies, places, events, types


def entity_dedup(title1: str, title2: str) -> bool:
    """基于实体组合的语义去重：同一公司+同一区+同类事件 = 重复"""
    c1, p1, e1, tp1 = extract_entity_pairs(title1)
    c2, p2, e2, tp2 = extract_entity_pairs(title2)
    common_c = set(c1) & set(c2)
    common_p = set(p1) & set(p2)
    common_e = set(e1) & set(e2)
    common_tp = set(tp1) & set(tp2)
    # 条件1: 共同公司+共同区+(共同事件 or 共同类型)
    if common_c and common_p and (common_e or common_tp):
        return True
    # 条件2: 共同区+共同事件+共同类型
    if common_p and common_e and common_tp:
        return True
    # 条件3: 共同区+共同事件+有公司出现在任一侧 (单侧公司匹配)
    if common_p and common_e and (c1 or c2):
        return True
    # 条件4: 共同公司(别名归一化后)+共同类型（无区也可；跨媒体同主体+同性质 = 同事件）
    #   e.g. 深大购宿舍楼：深圳大学≡深大 + 宿舍类型
    if common_c and common_tp:
        return True
    return False


# ── 实体指纹去重（第五轨） ──
# 同一事件不同媒体标题去重（2026-08-12 实测：深大购楼 4 稿、香蜜湖宅地 3 稿、红山中学 2 稿
# 标题差异极大，轨2 2-gram / 轨3 实体组合 / 轨4 结构化标题全部识别不到，全靠主模型人工合并）
# 思路：提取标题中的金额/数量指纹（量级+整数部分归一化），同金额+同实体上下文 = 同一事件。
# 防误伤：带小数金额(5.3亿)为强信号；整数金额(68亿)/纯数量(3000套)需实体上下文佐证。

_NUMBER_FP_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(亿|万|套|户|宗|栋|层|席|班|个|所|间|座|㎡|平米|平方米|元)'
)
# 数字+单位前的量词忽略词（"第2周"的"第"、"1-8公里"范围等不含于此正则）
# 量级分组：金额(亿/万) / 数量(套/户/宗/栋/层/席/班/个/所/间/座/㎡/平米/平方米)
_AMOUNT_UNITS = {'亿', '万'}
_MONEY_DEC_RE = re.compile(r'\d+\.\d+')  # 判断是否为带小数金额（高特异性）


def number_fingerprint(title: str) -> list[tuple]:
    """提取标题中的强数字指纹。

    返回 [(kind, 量级, 整数部分, 是否带小数), ...]
      kind: 'money'(金额) | 'count'(数量)
    归一化规则:
      5.3亿 → ('money','亿',5,True)    5.336亿 → ('money','亿',5,True)
      11.31亿 → ('money','亿',11,True)  68亿 → ('money','亿',68,False)
      8057万 → ('money','万',8057,False)  3000套 → ('count','套',3000,False)
      72班 → ('count','班',72,False)    3360个 → ('count','个',3360,False)
    过滤: 不匹配"元"单独出现（泛化）; 只保留单位明确的数字。
    """
    fps = []
    for m in _NUMBER_FP_RE.finditer(title):
        num_str, unit = m.group(1), m.group(2)
        if unit == '元':
            continue  # "元"太泛化，剔除（金额大多带 亿/万）
        try:
            val = float(num_str)
        except ValueError:
            continue
        is_dec = '.' in num_str
        if unit in _AMOUNT_UNITS:
            base = int(val)
            fps.append(('money', unit, base, is_dec))
        else:
            fps.append(('count', unit, int(val), is_dec))
    # 去重（同标题内重复数字只算一次）
    seen = set()
    out = []
    for f in fps:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _clean_title(title: str) -> str:
    """清洗标题：去标点/空格/括号，保留中英文数字，用于 n-gram"""
    return re.sub(r'[\W_]+', '', title)


def ngram_shingle_set(title: str, n: int = 6) -> set:
    """标题 n-gram 集合。n=6 捕捉专有名词级重合（红山中学民治校区/光明序安澜府）。"""
    cleaned = _clean_title(title)
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i+n] for i in range(len(cleaned) - n + 1)}


def fingerprint_dedup(title1: str, title2: str) -> bool:
    """第五轨：实体指纹去重。同金额/同数量/同专有名词 = 同一事件不同标题。

    判定规则:
      - 共同带小数金额指纹(≥1) → 直接判重（5.3亿/11.31亿高特异性）
      - 共同整数金额指纹(≥1) 且 实体上下文重叠 → 判重（68亿需区/公司/类型佐证）
      - 共同数量指纹(≥2) → 判重（3000套+264套 多数量重合基本同事件）
      - 共同数量指纹(==1) 且 实体上下文重叠 → 判重
      - 6-gram Jaccard ≥ 0.35 → 判重（专有名词/长短语级重合）
    """
    fp1, fp2 = number_fingerprint(title1), number_fingerprint(title2)
    if fp1 and fp2:
        set1, set2 = set(fp1), set(fp2)
        common = set1 & set2
        if common:
            # 实体上下文：区名/公司/类型任一重叠
            c1, p1, e1, tp1 = extract_entity_pairs(title1)
            c2, p2, e2, tp2 = extract_entity_pairs(title2)
            ctx_overlap = bool(set(c1) & set(c2) or set(p1) & set(p2) or set(tp1) & set(tp2))

            money_common = [f for f in common if f[0] == 'money']
            dec_money = [f for f in money_common if f[3]]       # 带小数金额（强信号）
            int_money = [f for f in money_common if not f[3]]   # 整数金额（需上下文）
            count_common = [f for f in common if f[0] == 'count']

            if dec_money:
                return True  # 5.3亿/11.31亿：同金额几乎必然同事件
            if int_money and ctx_overlap:
                return True
            if len(count_common) >= 2:
                return True
            if count_common and ctx_overlap:
                return True

    # 6-gram 专有名词级重合（红山中学民治校区 / 光明序安澜府 等）
    g1, g2 = ngram_shingle_set(title1), ngram_shingle_set(title2)
    if g1 and g2:
        inter = len(g1 & g2)
        union = len(g1 | g2)
        if union and inter / union >= 0.35:
            return True
    return False
# 周报/月报/数据报等结构化标题，同一报告类型+同一时间范围 = 重复

_REPORT_TYPE_MAP = {
    '周报': 'weekly', '周度': 'weekly', '一周': 'weekly', '周刊': 'weekly',
    '月报': 'monthly', '月度': 'monthly', '月刊': 'monthly',
    '季报': 'quarterly', '季度': 'quarterly',
    '年报': 'yearly', '年度': 'yearly',
    '半年报': 'semiannual', '半年': 'semiannual',
    '数据': 'data', '成交数据': 'data', '行情': 'data',
    '情报局': 'data', '第一时间': 'daily_brief',
}

# 第N周 / 第N期
_WEEK_NUM_RE = re.compile(r'第(\d+)[周期]')
# 日期范围 M.D-M.D 或 M月D日-M月D日
_DATE_RANGE_RE = re.compile(
    r'(\d{1,2})[\/\-.月](\d{1,2})[日号]?[\-~至到]'
    r'(\d{1,2})[\/\-.月](\d{1,2})[日号]?'
)
# 年月 YYYY年M月 / YYYY.MM
_YEAR_MONTH_RE = re.compile(r'(\d{4})[\/\-.年](\d{1,2})[\/\-.月]')


def structured_title_key(title: str) -> str | None:
    """Extract dedup key from structured report titles.

    Prioritizes date ranges over week numbers for cross-source consistency.
    """
    report_type = None
    for kw, rtype in _REPORT_TYPE_MAP.items():
        if kw in title:
            report_type = rtype
            break
    if report_type is None:
        return None

    time_key = ""

    # Priority 1: date range (most precise, best cross-source consistency)
    range_m = _DATE_RANGE_RE.search(title)
    if range_m:
        m1, d1, m2, d2 = range_m.group(1), range_m.group(2), range_m.group(3), range_m.group(4)
        time_key = f"{m1}.{d1}-{m2}.{d2}"

    # Priority 2: full date YYYY.MM.DD (for daily_brief etc)
    if not time_key:
        ymd_re = re.compile(r'(\d{4})[\/\-.\u5e74](\d{1,2})[\/\-.\u6708](\d{1,2})')
        ymd_m = ymd_re.search(title)
        if ymd_m:
            y, mo, d = ymd_m.group(1), ymd_m.group(2), ymd_m.group(3)
            if report_type == 'daily_brief':
                time_key = f"{y}-{mo}-{d}"
            else:
                time_key = f"{y}-{mo}"

    # Priority 3: week number (only when no date range)
    if not time_key:
        week_m = _WEEK_NUM_RE.search(title)
        if week_m:
            ym_m = _YEAR_MONTH_RE.search(title)
            year = ym_m.group(1) if ym_m else ""
            time_key = f"{year}:W{week_m.group(1)}"

    # Priority 4: year-month
    if not time_key:
        ym_m = _YEAR_MONTH_RE.search(title)
        if ym_m:
            time_key = f"{ym_m.group(1)}-{ym_m.group(2)}"

    if not time_key:
        return None

    return f"{report_type}:{time_key}"


def structured_dedup(title1: str, title2: str) -> bool:
    """Based on structured title track 4: same report type + same time range = duplicate"""
    key1 = structured_title_key(title1)
    key2 = structured_title_key(title2)
    if key1 is None or key2 is None:
        return False
    return key1 == key2


def load_index(path: Path = INDEX_PATH) -> list[dict]:
    """加载索引"""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def save_index(records: list[dict], path: Path = INDEX_PATH) -> None:
    """保存索引（JSONL 格式）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def check_duplicate(url: str, title: str, days: int = DEFAULT_RETENTION_DAYS) -> dict | None:
    """检查是否重复，返回匹配的旧记录或 None。五轨去重：URL指纹 + 标题2-gram + 实体组合 + 结构化标题 + 实体指纹"""
    records = load_index()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    uh = url_hash(url)
    ts = title_shingle_set(title)

    for r in records:
        if r.get("date", "9999") < cutoff:
            continue
        # 轨1: URL 精确匹配
        if r.get("url_hash") == uh:
            return r
        # 轨2: 标题 2-gram Jaccard 模糊匹配
        old_ts = set(r.get("title_shingles", []))
        if old_ts and ts:
            sim = jaccard_similarity(ts, old_ts)
            if sim >= TITLE_SIMILARITY_THRESHOLD:
                return r
        # 轨3: 实体组合语义匹配（同一公司+同一区+同类事件）
        if entity_dedup(title, r.get("title", "")):
            return r
        # 轨4: 结构化标题匹配（同一报告类型+同一时间范围）
        if structured_dedup(title, r.get("title", "")):
            return r
        # 轨5: 实体指纹匹配（同金额/同数量+实体上下文 = 同一事件不同标题）
        if fingerprint_dedup(title, r.get("title", "")):
            return r
    return None


def record_entry(date: str, url: str, title: str) -> None:
    """记录一条新闻到索引。

    同日同 URL 覆盖（先删旧再插新），保证同日重跑 render 不产生重复索引记录。
    """
    records = load_index()
    uh = url_hash(url)
    # 同日同 URL 覆盖：剔除已有同日记录后追加（同日覆盖语义）
    records = [r for r in records if not (r.get('date') == date and r.get('url_hash') == uh)]
    companies, places, events, types = extract_entity_pairs(title)
    entry = {
        "date": date,
        "url": normalize_url(url),
        "url_hash": uh,
        "title": title,
        "title_shingles": sorted(title_shingle_set(title)),
        "structured_key": structured_title_key(title),
        "number_fingerprints": number_fingerprint(title),
        "entities": {"companies": companies, "places": places, "events": events, "types": types},
    }
    records.append(entry)
    save_index(records)


def batch_check(input_path: str, date: str, days: int = DEFAULT_RETENTION_DAYS) -> list[dict]:
    """批量检查候选条目是否重复，返回每条的去重结果。

    输入 JSON 格式: [{"title": ..., "url": ..., "source": ..., "publishedAt": ...}, ...]
    输出: [{"title": ..., "url": ..., "is_duplicate": bool, "matched": ...|None}, ...]
    """
    with open(input_path, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    # 同时做候选间互斥去重（同一批内不同条目也可能重复）
    seen_keys = []  # list of (url_hash, title_shingle_set, structured_key)
    results = []

    for item in candidates:
        title = item.get("title", "")
        url = item.get("url", "")

        # 1. 跨日去重（检查索引）
        dup = check_duplicate(url, title, days=days)

        # 2. 批内互斥去重
        batch_dup = None
        if not dup:
            uh = url_hash(url)
            ts = title_shingle_set(title)
            sk = structured_title_key(title)
            for prev_uh, prev_ts, prev_sk, prev_title in seen_keys:
                if prev_uh == uh:
                    batch_dup = {"title": prev_title, "reason": "batch_url"}
                    break
                if ts and prev_ts and jaccard_similarity(ts, prev_ts) >= TITLE_SIMILARITY_THRESHOLD:
                    batch_dup = {"title": prev_title, "reason": "batch_2gram"}
                    break
                if sk and prev_sk and sk == prev_sk:
                    batch_dup = {"title": prev_title, "reason": "batch_structured"}
                    break
                if entity_dedup(title, prev_title):
                    batch_dup = {"title": prev_title, "reason": "batch_entity"}
                    break
                if fingerprint_dedup(title, prev_title):
                    batch_dup = {"title": prev_title, "reason": "batch_fingerprint"}
                    break
            seen_keys.append((uh, ts, sk, title))

        is_dup = dup is not None or batch_dup is not None
        matched = dup or batch_dup
        results.append({
            "title": title,
            "url": url,
            "source": item.get("source", ""),
            "publishedAt": item.get("publishedAt", ""),
            "is_duplicate": is_dup,
            "matched": matched,
        })

    return results


def batch_record(input_path: str, date: str) -> int:
    """批量记录最终入选条目到索引。

    输入 JSON 格式: [{"title": ..., "url": ..., ...}, ...]
    返回: 记录条数
    """
    with open(input_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    count = 0
    for item in items:
        title = item.get("title", "")
        url = item.get("url", "")
        if title and url:
            record_entry(date, url, title)
            count += 1
    return count


def prune_index(days: int = DEFAULT_RETENTION_DAYS) -> int:
    """清理过期记录，返回删除数量"""
    records = load_index()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    before = len(records)
    records = [r for r in records if r.get("date", "0000") >= cutoff]
    save_index(records)
    return before - len(records)


def stats() -> dict:
    """统计索引信息"""
    records = load_index()
    dates = {}
    for r in records:
        d = r.get("date", "unknown")
        dates[d] = dates.get(d, 0) + 1
    return {"total": len(records), "by_date": dates}


def rebuild_from_reports() -> int:
    """从已有日报文件重建索引"""
    records = []
    pattern = re.compile(r"深圳楼市日报_(\d{4}-\d{2}-\d{2})\.md")
    for f in OUTPUT_DIR.glob("深圳楼市日报_*.md"):
        m = pattern.match(f.name)
        if not m:
            continue
        date = m.group(1)
        content = f.read_text(encoding="utf-8")
        entries = re.findall(
            r'\d+\.\s*\*\*(.+?)\*\*\s*—\s*.+?\n(?:.*\n)*?\s*(https?://\S+)',
            content, re.MULTILINE
        )
        for title, url in entries:
            companies, places, events, types = extract_entity_pairs(title.strip())
            entry = {
                "date": date,
                "url": normalize_url(url),
                "url_hash": url_hash(url),
                "title": title.strip(),
                "title_shingles": sorted(title_shingle_set(title.strip())),
                "structured_key": structured_title_key(title.strip()),
                "number_fingerprints": number_fingerprint(title.strip()),
                "entities": {"companies": companies, "places": places, "events": events, "types": types},
            }
            records.append(entry)
    save_index(records)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description="szrehot 跨日去重工具（五轨去重）")
    sub = parser.add_subparsers(dest="cmd")

    # check
    p_check = sub.add_parser("check", help="检查是否重复")
    p_check.add_argument("--date", required=True)
    p_check.add_argument("--url", required=True)
    p_check.add_argument("--title", required=True)

    # batch-check
    p_batch_check = sub.add_parser("batch-check", help="批量检查候选条目是否重复")
    p_batch_check.add_argument("--input", required=True, help="候选 JSON 文件路径")
    p_batch_check.add_argument("--date", required=True, help="当前日期 YYYY-MM-DD")
    p_batch_check.add_argument("--output", default=None, help="输出结果 JSON 文件路径")
    p_batch_check.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS, help="去重回溯天数")

    # record
    p_rec = sub.add_parser("record", help="记录到索引")
    p_rec.add_argument("--date", required=True)
    p_rec.add_argument("--url", required=True)
    p_rec.add_argument("--title", required=True)

    # batch-record
    p_batch_rec = sub.add_parser("batch-record", help="批量记录到索引")
    p_batch_rec.add_argument("--input", required=True, help="最终入选 JSON 文件路径")
    p_batch_rec.add_argument("--date", required=True, help="当前日期 YYYY-MM-DD")

    # prune
    p_prune = sub.add_parser("prune", help="清理过期记录")
    p_prune.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)

    # stats
    sub.add_parser("stats", help="统计索引信息")

    # rebuild
    sub.add_parser("rebuild", help="从已有日报重建索引")

    args = parser.parse_args()

    if args.cmd == "check":
        dup = check_duplicate(args.url, args.title)
        if dup:
            print(f"DUPLICATE|{dup['date']}|{dup['title']}|{dup['url']}")
        else:
            print("NEW")
    elif args.cmd == "batch-check":
        results = batch_check(args.input, args.date, days=args.days)
        new_count = sum(1 for r in results if not r["is_duplicate"])
        dup_count = sum(1 for r in results if r["is_duplicate"])
        print(f"Batch check: {len(results)} candidates, {new_count} new, {dup_count} duplicate")
        for r in results:
            status = "NEW" if not r["is_duplicate"] else f"DUP|{r['matched'].get('title','')[:40]}"
            print(f"  {status} | {r['title'][:50]}")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"Results saved to {args.output}")
    elif args.cmd == "record":
        record_entry(args.date, args.url, args.title)
        print("OK")
    elif args.cmd == "batch-record":
        count = batch_record(args.input, args.date)
        print(f"Recorded {count} entries")
    elif args.cmd == "prune":
        removed = prune_index(args.days)
        print(f"Pruned {removed} records older than {args.days} days")
    elif args.cmd == "stats":
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif args.cmd == "rebuild":
        count = rebuild_from_reports()
        print(f"Rebuilt index with {count} entries")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()