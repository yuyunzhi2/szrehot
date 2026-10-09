# -*- coding: utf-8 -*-
"""
深圳阳光租赁平台 — 租赁项目抓取(住宅/商业/办公,三类合并同表)

按用途类型、面积下限、发布时间范围、区域筛选,输出含详情的 xlsx + 核对清单。
三类查询结果合并到同一个 xlsx 同一张表,用"用途类别"列区分。

三类用途(--lease-use-type):
  - 住宅公寓 : 住宅/公寓/宿舍 + 酒店/宾馆 + 别墅/庄园(≥500㎡)
               住宅走 leaseUseType=住宅公寓;酒店/别墅/庄园走 projectName 关键词搜索(leaseUseType 无酒店值)
  - 商业     : 商铺类。leaseUseType=商业 或 名称含商业词;排除工业/公共服务/居住(含酒店)
  - 办公写字楼: 写字楼/办公。leaseUseType=办公写字楼 或 名称含办公词;排除工业/公共服务/居住

筛选策略(双层):
  API端: leaseUseType/projectName + structureAreaMin + 11区 areaCode
  客户端: areaCode 4403前缀 + listingStartDate>=cutoff + 用途匹配规则 + leaseNotices只留交易公告

详见技能 SKILL.md。
"""
import urllib.request
import json
import time
import sys
import io
import os
import re
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

# Windows 终端默认 GBK,项目名含生僻字会 UnicodeEncodeError;强制 stdout/stderr 走 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = "https://rent.szexgrp.com/cms"
SZ_AREA_CODES_DEFAULT = "440303,440304,440305,440306,440307,440308,440309,440310,440311,440312,440313"
PAGE_SIZE = 200

AREA_MIN_DEFAULTS = {"住宅公寓": 500, "商业": 1000, "办公写字楼": 1000}

# areaCode → 区名 映射(11区 + 市本级)。街道级编码按前缀归区(见 area_code_to_name)。
AREA_CODE_MAP = {
    "440303": "罗湖区", "440304": "福田区", "440305": "南山区", "440306": "宝安区",
    "440307": "龙岗区", "440308": "盐田区", "440309": "龙华区", "440310": "坪山区",
    "440311": "光明区", "440312": "大鹏新区", "440313": "深汕特别合作区", "440300": "深圳市(市级)",
}
# 街道级编码前缀 → 区(6位以上编码按前6位匹配)
AREA_PREFIX_MAP = {
    "440303": "罗湖区", "440304": "福田区", "440305": "南山区", "440306": "宝安区",
    "440307": "龙岗区", "440308": "盐田区", "440309": "龙华区", "440310": "坪山区",
    "440311": "光明区", "440312": "大鹏新区", "440313": "深汕特别合作区",
}

# 名称校验关键词
RESIDENTIAL_NAME_KEYS = ("住宅", "公寓", "宿舍")
HOTEL_NAME_KEYS = ("酒店", "宾馆")
VILLA_NAME_KEYS = ("别墅", "庄园")
COMMERCIAL_NAME_KEYS = ("商铺", "商业", "店面", "店铺", "商场", "购物中心", "菜市场", "市场", "铺位", "门店", "商业街")
OFFICE_NAME_KEYS = ("写字楼", "办公", "大厦", "科技园", "科创", "创新中心", "总部", "商务中心", "产业园", "研发")
# 商业/办公类排除词:工业 + 公共服务 + 居住(后者用住宅类查询去拿,不混入商业/办公)
NON_COMMERCIAL_OFFICE_EXCLUDE_KEYS = (
    "厂房", "工厂", "厂区", "工业园", "工业区", "仓储", "物流",
    "食堂", "幼儿园", "学校", "医院",
    "酒店", "别墅", "庄园", "宿舍", "住宅", "公寓",
)
# 住宅类排除的混合词对(宿舍+厂房)
RESIDENTIAL_EXCLUDE_MIX = (("宿舍", "厂房"),)
# 住宅类 projectName 搜索关键词(leaseUseType 无酒店值,靠名称搜)
RESIDENTIAL_SEARCH_KEYWORDS = ("酒店", "宾馆", "别墅", "庄园")
VILLA_MIN_AREA = 500  # 别墅/庄园面积下限

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def _request(url, body=None, retries=3):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data, headers=HEADERS,
                method="POST" if body is not None else "GET",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


def area_code_to_name(code):
    """areaCode → 区名。先精确匹配,再按前缀(6位)匹配街道级编码。"""
    if not code:
        return ""
    if code in AREA_CODE_MAP:
        return AREA_CODE_MAP[code]
    for prefix, name in AREA_PREFIX_MAP.items():
        if code.startswith(prefix):
            return name
    return code  # 未知编码保留原值


def fetch_list_by_use_type(lease_use_type, area_min, area_codes, cutoff=None):
    """按 leaseUseType 翻页抓取列表。

    优化: orderBy=listingStartDate DESC + 早停。cutoff 不为 None 时,
    翻到本页末尾 listingStartDate < cutoff 即停(后续页更早,全在截止日前)。
    避免翻完全量再客户端筛的低效。cutoff=None 时回退为全量翻页(向后兼容)。
    """
    items = []
    page = 1
    while True:
        body = {
            "pageNum": page, "pageSize": PAGE_SIZE,
            "leaseUseType": lease_use_type, "structureAreaMin": area_min,
            "areaCode": area_codes, "orderBy": "listingStartDate", "direction": "DESC",
        }
        r = _request(f"{BASE}/api/v1/lease/project/page", body=body)
        data = r.get("data") or {}
        content = data.get("content") or []
        items.extend(content)
        total = data.get("totalElements", 0)
        print(f"  [{lease_use_type}] 第{page}页: 取{len(content)}条, 累计{len(items)}/{total}")
        if not content or len(items) >= total:
            break
        # 早停: 本页最后一条已早于 cutoff 下界,后续页更早,无需再翻
        if cutoff and content:
            newest_listing = (content[0].get("listingStartDate") or "").strip()
            if newest_listing and newest_listing < cutoff:
                print(f"  [{lease_use_type}] 早停: 本页最新 listing={newest_listing} < cutoff={cutoff}")
                break
        page += 1
        time.sleep(0.2)
    return items


def fetch_list_by_name(keyword, area_min, area_codes, cutoff=None):
    """按 projectName 关键词翻页抓取(不传 leaseUseType,避免 AND 关系)。

    同 fetch_list_by_use_type,orderBy=listingStartDate DESC + 早停。
    """
    items = []
    page = 1
    while True:
        body = {
            "pageNum": page, "pageSize": PAGE_SIZE,
            "projectName": keyword, "structureAreaMin": area_min,
            "areaCode": area_codes, "orderBy": "listingStartDate", "direction": "DESC",
        }
        r = _request(f"{BASE}/api/v1/lease/project/page", body=body)
        data = r.get("data") or {}
        content = data.get("content") or []
        items.extend(content)
        total = data.get("totalElements", 0)
        print(f"  [名称搜:{keyword}] 第{page}页: 取{len(content)}条, 累计{len(items)}/{total}")
        if not content or len(items) >= total or page >= 20:
            break
        if cutoff and content:
            newest_listing = (content[0].get("listingStartDate") or "").strip()
            if newest_listing and newest_listing < cutoff:
                print(f"  [名称搜:{keyword}] 早停: listing={last_listing} < cutoff={cutoff}")
                break
        page += 1
        time.sleep(0.2)
    return items


def _time_check(listing, cutoff, cutoff_end):
    """通用时间上下界校验。listing 为 listingStartDate 字符串。
    cutoff=下界(>=), cutoff_end=上界(<=, 可选)。返回 (是否通过, 排除原因)。"""
    if listing < cutoff:
        return False, f"早于截止下限(listing={listing})"
    if cutoff_end and listing > cutoff_end:
        return False, f"晚于截止上限(listing={listing})"
    return True, ""


def match_residential(it, cutoff, area_min, cutoff_end=None):
    """住宅公寓类匹配(住宅/公寓/宿舍 + 酒店/宾馆 + 别墅/庄园≥500)。

    保留任一:
      - 名称含 住宅/公寓/宿舍(排除宿舍+厂房混合)
      - 名称含 酒店/宾馆
      - 名称含 别墅/庄园 且 面积>=500
    """
    name = it.get("projectName") or ""
    code = it.get("areaCode") or ""
    listing = it.get("listingStartDate") or ""
    if not code.startswith("4403"):
        return False, f"非深圳(areaCode={code})"
    ok, reason = _time_check(listing, cutoff, cutoff_end)
    if not ok:
        return False, reason

    # 住宅/公寓/宿舍
    if any(k in name for k in RESIDENTIAL_NAME_KEYS):
        for a, b in RESIDENTIAL_EXCLUDE_MIX:
            if a in name and b in name:
                return False, f"{a}+{b}混合"
        return True, "住宅/公寓/宿舍"
    # 酒店/宾馆
    if any(k in name for k in HOTEL_NAME_KEYS):
        return True, "酒店/宾馆"
    # 别墅/庄园(面积≥500)
    if any(k in name for k in VILLA_NAME_KEYS):
        area = it.get("leaseObjectCounts") or 0
        try:
            area = float(area)
        except (TypeError, ValueError):
            area = 0
        if area >= VILLA_MIN_AREA:
            return True, f"别墅/庄园({area}㎡)"
        return False, f"别墅/庄园面积不足({area}㎡<{VILLA_MIN_AREA})"
    return False, "名称不含住宅/酒店/别墅等关键词"


def match_commercial_office(it, cutoff, use_type, cutoff_end=None):
    """商业/办公类匹配(宽松优先 + 排除公共服务/居住/工业)。"""
    name = it.get("projectName") or ""
    code = it.get("areaCode") or ""
    listing = it.get("listingStartDate") or ""
    use_field = it.get("leaseUseType") or ""
    if not code.startswith("4403"):
        return False, f"非深圳(areaCode={code})"
    ok, reason = _time_check(listing, cutoff, cutoff_end)
    if not ok:
        return False, reason
    if any(k in name for k in NON_COMMERCIAL_OFFICE_EXCLUDE_KEYS):
        return False, "非商业办公(含公共服务/居住/工业词)"
    if use_type == "商业":
        type_ok = "商业" in use_field
        name_ok = any(k in name for k in COMMERCIAL_NAME_KEYS)
    else:
        type_ok = "办公写字楼" in use_field
        name_ok = any(k in name for k in OFFICE_NAME_KEYS)
    if not (type_ok or name_ok):
        return False, f"非{use_type}"
    return True, use_type


def fetch_detail(content_id):
    try:
        r = _request(f"{BASE}/api/v1/lease/project/detailNew?contentId={content_id}")
        return content_id, (r.get("data") or {})
    except Exception as e:
        return content_id, {"_error": str(e)}


def summarize_lease_objects(objects):
    if not objects:
        return ""
    parts = []
    for o in objects:
        addr = o.get("address") or ""
        area = o.get("leaseCount") or ""
        unit = o.get("leaseCountUnit") or ""
        price = o.get("displayLeaseBasePrice") or ""
        use = o.get("leaseUse") or ""
        parts.append(f"[{addr}|{area}{unit}|底价{price}|{use}]")
    return " ".join(parts)


def summarize_notices(notices):
    """只保留 noticeTypeName=交易公告 的公告,聚合 类型|标题|发布时间。"""
    if not notices:
        return ""
    parts = []
    for n in notices:
        if n.get("noticeTypeName") != "交易公告":
            continue
        title = n.get("noticeTitle") or ""
        pt = n.get("publishTime") or ""
        parts.append(f"[交易公告|{title}|{pt}]")
    return " ".join(parts)


def build_row(it, detail, category, match_reason):
    code = it.get("areaCode") or ""
    return {
        "用途类别": category,
        "匹配原因": match_reason,
        "城区": area_code_to_name(code),
        "projectName": it.get("projectName"),
        "contentId": it.get("contentId"),
        "projectCode": it.get("projectCode"),
        "areaCode": code,
        "leaseUseType": it.get("leaseUseType"),
        "projectBiddingStatus": it.get("projectBiddingStatus"),
        "listingStartDate": it.get("listingStartDate"),
        "listingEndDate": it.get("listingEndDate"),
        "lastSyncTime": it.get("lastSyncTime"),
        "leaseObjectCounts": it.get("leaseObjectCounts"),
        "leaseMonthlyPriceMin": it.get("leaseMonthlyPriceMin"),
        "leaseMonthlyPriceMax": it.get("leaseMonthlyPriceMax"),
        "leaser": detail.get("leaser"),
        "legalNature": detail.get("legalNature"),
        "listedOrg": detail.get("listedOrg") or it.get("listedOrg"),
        "minLeaseBasePrice": detail.get("minLeaseBasePrice"),
        "leaseObjects_summary": summarize_lease_objects(detail.get("leaseObjects")),
        "leaseNotices_summary": summarize_notices(detail.get("leaseNotices")),
        "detail_url": f"https://rent.szexgrp.com/cms/lease/project/detailNew?contentId={it.get('contentId')}",
    }


def parse_lease_objects_summary(summary):
    """解析 leaseObjects_summary 字符串,拆成标的列表。

    输入格式(summarize_lease_objects 产出):
      [地址|面积单位|底价XX|用途] [地址2|面积单位2|底价XX2|用途2]
    每个标的 4 字段,以 | 分隔;多标的以空格分隔(单个标的内部不含空格分隔)。

    返回 [{地址, 面积, 底价, 用途}, ...]。底价去掉"底价"前缀。
    解析失败时返回 [{"地址": 原文, "面积":"", "底价":"", "用途":""}] 兜底。
    """
    if not summary:
        return []
    import re
    # 匹配每个 [....] 块(非贪婪,到第一个 ])
    blocks = re.findall(r"\[([^\]]*)\]", summary)
    if not blocks:
        return [{"地址": summary, "面积": "", "底价": "", "用途": ""}]
    result = []
    for b in blocks:
        parts = b.split("|")
        # parts: [地址, 面积单位, 底价XX, 用途]
        addr = parts[0].strip() if len(parts) > 0 else ""
        area = parts[1].strip() if len(parts) > 1 else ""
        price = parts[2].strip() if len(parts) > 2 else ""
        use = parts[3].strip() if len(parts) > 3 else ""
        # 底价去掉"底价"前缀
        if price.startswith("底价"):
            price = price[2:]
        result.append({"地址": addr, "面积": area, "底价": price, "用途": use})
    return result


def _md_cell(text):
    """md 表格单元格: 转义管道符/空格 + 轻量清洗(去"广东省"前缀、去开头"深圳市"、单位 平方米→㎡)。

    清洗对全部单元格安全: 地址/用途文本不含"平方米"单位词; "广东省"/开头"深圳市"前缀仅地址出现。
    2026-08-26: 地址统一去掉开头「深圳市」前缀（城区信息由地址中区名承载）。
    """
    s = str(text or "")
    s = s.replace("广东省", "").replace("深圳市深圳市", "深圳市")
    s = re.sub(r"^深圳市?", "", s.strip())
    s = s.replace("平方米", "㎡").replace("元/㎡/月", "元/㎡/月")
    return s.replace("|", "\\|").replace(" ", "&nbsp;")


# ---------- 同楼/同项目多宗合并（2026-09-04 新增；归一规则参考法拍2000万汇总） ----------
_MERGE_BASE_RE = re.compile(
    r"(\d+\s*号楼|"                        # 3号楼
    r"\d+\s*[栋座幢楼单元层房室铺]|"            # 15栋 / 8楼 / 3层 / 1501房/室
    r"[A-Z]\s*[栋座]|"                      # A栋 / B座
    r"[A-Z]\d+\s*[房室]?|"                # A15房 / B1501室
    r"之\s*\d+|"                            # 之18
    r"\d+[A-Z]?\s*房|\d+[A-Z]?\s*室)"   # 1501房 / 15A房
)


def _merge_base(addr: str) -> str:
    """归一基准地址：去「广东省/深圳市」前缀后截断首个栋/座/幢/层/房/室标识及其后房号。

    如 `广东省深圳市宝安区石街大厦8楼` → `宝安区石街大厦`；归一后过短(<4字)回退原名防误伤。
    同楼多宗（石街大厦8楼/10楼等）据此判同址，视为一宗。
    注意：不截断「号」（松岗大道30号是街道门牌，不同门牌=不同楼栋，不得误并）。"""
    s = str(addr or "").strip().replace("广东省", "").replace("深圳市深圳市", "深圳市")
    s = re.sub(r"^深圳市?", "", s.strip())
    m = _MERGE_BASE_RE.search(s)
    if m:
        base = s[:m.start()].strip(" ，,、；;")
        if len(base) >= 4:
            return base
    return s


def _parse_area_num(area) -> float | None:
    """面积数值：`1884.7平方米` → 1884.7；解析失败返回 None。"""
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", str(area or "").strip())
    return float(m.group(1)) if m else None


def _parse_price_num(price) -> float | None:
    """底价(元/㎡/月)数值：纯数字直接取；`XX元/月(单价Y元/㎡/月)`取括号单价Y；
    `…人民币Y元/㎡/月…`取首个Y；解析失败返回 None。"""
    s = str(price or "").strip()
    for pat in (r"单价\s*([0-9]+(?:\.[0-9]+)?)",
                r"人民币\s*([0-9]+(?:\.[0-9]+)?)\s*元/㎡/月",
                r"([0-9]+(?:\.[0-9]+)?)"):
        m = re.search(pat, s)
        if m:
            return float(m.group(1))
    return None


def _fmt_num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def merge_same_building_rows(rows):
    """同一栋楼（归一基准地址相同）或同一项目（项目名相同）的多宗视为一宗，合并统计。

    输入 rows: [{项目, 地址, 面积, 底价, 用途}, ...]（每宗一行）。
    1) 逐字重复的标的先去重——同项目被多用途类别重复命中、或跨项目重复公告，会产生
       完全相同的行，不得重复计入宗数（如 青湖山庄青泉苑 在办公/商业两类各命中一次）；
    2) 按 (归一基准地址 | 项目名) 做连通归并：同基准地址或同项目即并为一宗；
    3) 每组输出一行汇总：地址=基准地址（过短回退项目名）、宗数=N、面积=Σ各宗面积、
       底价=全部同价→单值 / 有差异→区间（低-高）/ 含无法解析→去重原文，用途=去重合并。
       单宗行原样保留（宗数=1）。
    """
    # 1) 完全重复标的去重
    seen = set()
    uniq = []
    for r in rows:
        key = (str(r.get("项目", "")), str(r.get("地址", "")), str(r.get("面积", "")).strip(),
               str(r.get("底价", "")).strip(), str(r.get("用途", "")).strip())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(dict(r))
    rows = uniq
    if not rows:
        return []

    # 2) 连通归并：同基准地址 或 同项目
    n = len(rows)
    parent = list(range(n))

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    base_map, proj_map = {}, {}
    for i, r in enumerate(rows):
        base = _merge_base(r.get("地址", ""))
        proj = (r.get("项目") or "").strip()
        r["_base"] = base
        r["_proj"] = proj
        if len(base) >= 4:
            if base in base_map:
                _union(i, base_map[base])
            else:
                base_map[base] = i
        if proj:
            if proj in proj_map:
                _union(i, proj_map[proj])
            else:
                proj_map[proj] = i

    groups: dict[int, list] = {}
    for i, r in enumerate(rows):
        groups.setdefault(_find(i), []).append(r)

    # 3) 聚合输出
    out = []
    for members in groups.values():
        if len(members) == 1:
            m = members[0]
            out.append({"地址": m["地址"], "宗数": 1, "面积": m["面积"],
                        "底价": m["底价"], "用途": m["用途"]})
            continue
        base = members[0]["_base"] if len(members[0]["_base"]) >= 4 else members[0]["_proj"]
        area_total, n_area = 0.0, 0
        prices, raw_prices, uses = [], [], []
        for m in members:
            a = _parse_area_num(m.get("面积"))
            if a is not None:
                area_total += a
                n_area += 1
            p = _parse_price_num(m.get("底价"))
            if p is not None:
                prices.append(p)
            else:
                raw_prices.append(str(m.get("底价") or "").strip())
            u = str(m.get("用途") or "").strip()
            if u and u not in uses:
                uses.append(u)
        area_cell = _fmt_num(area_total) if n_area else "-"
        if prices and not raw_prices:
            lo, hi = min(prices), max(prices)
            price_cell = f"{hi:.2f}" if lo == hi else f"{lo:.2f}-{hi:.2f}"
        else:
            price_cell = "<br>".join(dict.fromkeys(raw_prices + [f"{p:.2f}" for p in prices])) or "-"
        use_cell = "<br>".join(uses) if len(uses) > 1 else (uses[0] if uses else "-")
        out.append({"地址": base, "宗数": len(members), "面积": area_cell,
                    "底价": price_cell, "用途": use_cell})
    return out

def query_residential(area_min, area_codes, cutoff, cutoff_end=None):
    """住宅类查询:leaseUseType=住宅公寓 + projectName 搜酒店/宾馆/别墅/庄园,合并去重。"""
    print("[住宅] 路径A: leaseUseType=住宅公寓")
    items_a = fetch_list_by_use_type("住宅公寓", area_min, area_codes, cutoff)
    print("[住宅] 路径B: projectName 搜酒店/宾馆/别墅/庄园")
    items_b = []
    for kw in RESIDENTIAL_SEARCH_KEYWORDS:
        items_b.extend(fetch_list_by_name(kw, area_min, area_codes, cutoff))

    # 合并去重(按 contentId),保留首次出现
    seen = set()
    all_items = []
    for it in items_a + items_b:
        cid = it.get("contentId")
        if cid in seen:
            continue
        seen.add(cid)
        all_items.append(it)
    print(f"[住宅] 合并去重后: {len(all_items)} (A={len(items_a)} B去重前={len(items_b)})")

    matcher = lambda it: match_residential(it, cutoff, area_min, cutoff_end)
    return all_items, matcher, "住宅公寓"


def query_commercial_office(use_type, area_min, area_codes, cutoff, cutoff_end=None):
    items = fetch_list_by_use_type(use_type, area_min, area_codes, cutoff)
    matcher = lambda it: match_commercial_office(it, cutoff, use_type, cutoff_end)
    return items, matcher, use_type


def main():
    ap = argparse.ArgumentParser(description="深圳阳光租赁平台租赁项目抓取(住宅/商业/办公,三类合并)")
    # 默认: cutoff=昨天, 三类合并, 深圳11区, 住宅≥500/商业≥1000/办公≥1000
    # 今天 2026-07-31 → 昨天 2026-07-30
    default_cutoff = (datetime(2026, 7, 31) - timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00"
    ap.add_argument("--lease-use-type", default="全部",
                    choices=["住宅公寓", "商业", "办公写字楼", "全部"],
                    help="用途类型(全部=三类合并输出同表)。默认全部")
    ap.add_argument("--cutoff", default=default_cutoff,
                    help=f"发布时间下限 listingStartDate>=该值 (YYYY-MM-DD HH:MM:SS)。默认昨天起: {default_cutoff}")
    ap.add_argument("--cutoff-end", default=None,
                    help="发布时间上限 listingStartDate<=该值 (YYYY-MM-DD HH:MM:SS)。不传则无上限")
    ap.add_argument("--area-min", type=int, default=None,
                    help="面积下限㎡(不传则按用途默认:住宅500/商业1000/办公1000)")
    ap.add_argument("--area-codes", default=SZ_AREA_CODES_DEFAULT, help="区域编码,逗号分隔")
    ap.add_argument("--output", default=None, help="输出xlsx路径(不传则按用途+日期自动命名)")
    ap.add_argument("--max-workers", type=int, default=8, help="详情并发线程数")
    args = ap.parse_args()

    types_to_run = ["住宅公寓", "商业", "办公写字楼"] if args.lease_use_type == "全部" else [args.lease_use_type]
    cutoff_date = args.cutoff[:10]
    # 输出目录：技能根/outputs/{日期}/（gitignore 已排除 outputs），按窗口起点日期隔离
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(skill_root, "outputs", cutoff_date)
    os.makedirs(output_dir, exist_ok=True)
    output = args.output or (os.path.join(output_dir, f"深圳阳光租赁_三类_{cutoff_date}起.xlsx") if args.lease_use_type == "全部"
                             else os.path.join(output_dir, f"深圳阳光租赁_{args.lease_use_type}_{cutoff_date}起.xlsx"))
    md_output = output.rsplit(".", 1)[0] + ".md"

    all_rows = []
    per_type_excluded = {}

    for use_type in types_to_run:
        area_min = args.area_min if args.area_min is not None else AREA_MIN_DEFAULTS[use_type]
        print(f"\n=== 查询 {use_type} (areaMin={area_min}㎡) ===")
        if use_type == "住宅公寓":
            items, matcher, category = query_residential(area_min, args.area_codes, args.cutoff, args.cutoff_end)
        else:
            items, matcher, category = query_commercial_office(use_type, area_min, args.area_codes, args.cutoff, args.cutoff_end)

        hit, excluded = [], []
        for it in items:
            ok, reason = matcher(it)
            if ok:
                hit.append((it, reason))
            else:
                excluded.append((it.get("projectName"), reason))
        per_type_excluded[category] = excluded
        print(f"  [{category}] 命中: {len(hit)}, 排除: {len(excluded)}")

        # 详情抓取
        if hit:
            print(f"  [{category}] 抓取 {len(hit)} 条详情...")
            details = {}
            with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
                futures = {ex.submit(fetch_detail, it["contentId"]): it for it, _ in hit}
                done = 0
                for fut in as_completed(futures):
                    cid, detail = fut.result()
                    details[cid] = detail
                    done += 1
                    if done % 20 == 0 or done == len(hit):
                        print(f"    进度: {done}/{len(hit)}")
            for it, reason in hit:
                all_rows.append(build_row(it, details.get(it["contentId"], {}), category, reason))

    if not all_rows:
        print("\n无命中项目,退出。")
        return

    df = pd.DataFrame(all_rows)
    # 按用途类别+发布时间排序
    df = df.sort_values(["用途类别", "listingStartDate"], ascending=[True, False]).reset_index(drop=True)

    # === 输出 xlsx: 删除 6 个字段 ===
    drop_cols = ["projectCode", "contentId", "leaseMonthlyPriceMax", "listedOrg",
                 "leaseNotices_summary", "detail_url"]
    df_xlsx = df.drop(columns=[c for c in drop_cols if c in df.columns])
    df_xlsx.to_excel(output, index=False)
    print(f"\n完成: {len(df_xlsx)} 条 → {output}")
    print(f"  类型分布: {df_xlsx['用途类别'].value_counts().to_dict()}")
    print(f"  xlsx 列({len(df_xlsx.columns)}): {list(df_xlsx.columns)}")

    # === 输出 md: 按用途差异化筛选, 表格展示 用途类别 + 简述(leaseObjects_summary 原文) ===
    # 筛选规则: 办公写字楼≥2000㎡; 商业/住宅公寓(居住类) 全部保留
    OFFICE_MIN = 2000

    def _md_keep(row):
        cat = row["用途类别"]
        if cat == "办公写字楼":
            v = row.get("leaseObjectCounts")
            try:
                return pd.notna(v) and float(v) >= OFFICE_MIN
            except (TypeError, ValueError):
                return False
        # 商业 / 住宅公寓(居住类) 全部保留
        return cat in ("商业", "住宅公寓")

    df_md = df[df.apply(_md_keep, axis=1)].copy()
    # 解析每个项目的 leaseObjects_summary,每个标的拆成一行（附 项目 字段供同楼/同项目合并）
    md_rows = []
    for _, r in df_md.iterrows():
        objs = parse_lease_objects_summary(r.get("leaseObjects_summary", ""))
        if not objs:
            objs = [{"地址": "", "面积": "", "底价": "", "用途": ""}]
        for o in objs:
            md_rows.append({
                "用途类别": r["用途类别"],
                "城区": r.get("城区", ""),
                "项目": r["projectName"],
                "地址": o["地址"],
                "面积": o["面积"],
                "底价": o["底价"],
                "用途": o["用途"],
            })
    # 2026-09-04: 同一栋楼（归一基准地址）或同一项目（projectName）的多宗视为一宗合并统计
    n_raw = len(md_rows)
    n_proj = int(df_md["projectName"].nunique())
    md_rows = merge_same_building_rows(md_rows)
    with open(md_output, "w", encoding="utf-8") as f:
        f.write(f"# 租赁项目清单\n\n")
        f.write(f"数据来源: 深圳阳光租赁平台 (listingStartDate >= {args.cutoff[:10]})\n\n")
        f.write(f"筛选规则: 办公写字楼≥{OFFICE_MIN}㎡; 商业/居住类(住宅、公寓等)全部保留\n\n")
        f.write(f"合并规则: 同一栋楼（归一基准地址）或同一项目（projectName）的多宗视为一宗，提取后合并统计\n\n")
        f.write(f"共 {n_proj} 个项目, {len(md_rows)} 宗（合并自 {n_raw} 个标的）\n\n")
        # 2026-08-26: 表格精简（用途类别/城区列移除，城区由地址承载）；单位统一入表头，单元格只写纯数字
        # 2026-09-04: 新增 宗数 列；同楼/同项目多宗合并为一行汇总（地址=基准地址、面积=合计、底价=单值/区间、用途=去重）
        f.write("| 地址 | 宗数 | 面积(㎡) | 底价(元/㎡/月) | 用途 |\n")
        f.write("|---|---|---|---|---|\n")
        for row in md_rows:
            # 单位剥离兼容 汉字「平方米」与符号「㎡」两种形态（实测底价多为 元/平方米/月）
            area_v = re.sub(r"(?:平方米|㎡)\s*$", "", str(row['面积'] or "").strip())
            price_v = re.sub(r"(?:元/(?:平方米|㎡)/月|元/月)\s*$", "", str(row['底价'] or "").strip())
            f.write(f"| {_md_cell(row['地址'])} | {row['宗数']} | {_md_cell(area_v)} | "
                    f"{_md_cell(price_v)} | {_md_cell(row['用途'])} |\n")
    print(f"md(办公≥{OFFICE_MIN}㎡+商业+居住, {n_proj}项目/{len(md_rows)}宗, 合并自{n_raw}标的) → {md_output}")

    # 核对清单
    log_path = output.rsplit(".", 1)[0] + "_核对清单.txt"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"=== 合并命中 {len(df)} 条 ===\n")
        f.write(f"筛选条件: cutoff={args.cutoff} types={types_to_run}\n\n")
        for cat in df["用途类别"].unique():
            sub = df[df["用途类别"] == cat]
            f.write(f"--- {cat} ({len(sub)}条) ---\n")
            for _, r in sub.iterrows():
                f.write(f"{r['listingStartDate']} | {r['城区']} | {r['projectName']} | 出租方={r['leaser']} | 底价={r['minLeaseBasePrice']}\n")
            f.write("\n")
        f.write(f"=== 各类排除项 ===\n")
        for cat, excl in per_type_excluded.items():
            f.write(f"--- {cat} 排除 {len(excl)} 条 ---\n")
            for name, reason in excl:
                f.write(f"[{reason}] {name}\n")
            f.write("\n")
    print(f"核对清单 → {log_path}")


if __name__ == "__main__":
    main()
