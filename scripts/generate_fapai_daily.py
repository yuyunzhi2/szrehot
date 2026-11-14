# -*- coding: utf-8 -*-
"""深圳法拍资产日报生成器

从 法拍数据.xlsx 的 京东 / 阿里 / 京东成交 / 阿里成交 四个工作表，
按**业务日当日日期块**提取数据，输出 markdown 日报；京东两表另与步骤 1 的
`京东API` / `京东成交API` 当日块取**并集**（同名去二拍/变卖前缀 + 价格差 ≤0.05 万
判同，数值/热度取 API、名称保处置标记）。详见 references/fapai-daily.md。

日期标记兼容三种形态：
  1) 中文文本 "2026年10月27日"（允许首尾空白）
  2) ISO 文本 "2026-10-27" / "2026/10/27" / "2026.10.27"
  3) Excel 日期序列值（如 46322）或 datetime/date 对象
标记行按「SHEETS 声明列 + 前 3 列」探测（2026-11-14 起）：人工表会把日期粘到
别的列（阿里 2026-11-05 起写进 B 列），只认单列会整块漏掉并静默回退。

呈现规则：
  - 拍卖开始时间只保留到日期（如 "11月23日 10:00:00开始" -> "11月23日"）
  - 项目名称精简为「区 + 小区（同级单位）+ 楼栋房号」：删省市/街道/路/门牌等
    地址信息与 房产/房屋/不动产/房地产 后缀；保留 (破)(刑)(第N次)/二拍/变卖。
    规则见 references/fapai-daily.md「项目名称清洗」。
  - 含"二拍"的名称把"二拍"提到首位，全部二拍项独立成组置顶，组内按
    "二拍"后的城区名称首字拼音降序；其余项按城区降序；无城区的行排
    组内最后并保持原序。
  - 入选规则（2026-11-03 起）：
      关注（新增关注top10）：有当前价即入选；有围观按围观降序，围观无法解析
        按当前价降序补足至 10 条（评估价缺失时折价率显示「—」）
      成交（成交top10）：有成交价即入选；折价率 > 100% 的一律入选，其余按
        折价率由低到高补足至 10 条，评估价缺失者排最后

用法（工作目录 = szrehot 技能目录）：
  python -X utf8 scripts/generate_fapai_daily.py                    # 今天，缺省路径
  python -X utf8 scripts/generate_fapai_daily.py --date 2026-10-27
  python -X utf8 scripts/generate_fapai_daily.py --xlsx PATH --out-dir DIR
  python -X utf8 scripts/generate_fapai_daily.py --self-test        # 名称清洗回归用例
  python -X utf8 scripts/generate_fapai_daily.py --audit-names TSV  # 全量名称对照+统计

本脚本是 szrehot 每日链的第 2 步（第 1 步 = scripts/jd_paipai/ 京东法拍采集，
第 3 步 = 本技能原有五阶段新闻管道），规则详表见 references/fapai-daily.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:
    from openpyxl import load_workbook
    from openpyxl.utils.datetime import from_excel
except ImportError:
    sys.stderr.write("缺少 openpyxl，请先安装：python -m pip install openpyxl\n")
    raise

try:
    from pypinyin import lazy_pinyin
    _HAS_PINYIN = True
except ImportError:
    _HAS_PINYIN = False

DEFAULT_XLSX = r"D:\Desktop\法拍数据.xlsx"
DEFAULT_OUT_DIR = r"D:\Administrator\Documents\大瑀MD"

# 表配置：(sheet 名, 日期列, 模式, API 并集源 (sheet 名, 日期列) 或 None)
#   watch = 新增关注：按 F 列热度降序取前 10
#   deal_jd = 京东成交：F 列含"出价"字样
#   deal_ali = 阿里成交：F 列出价人数 > 0
# 第四项为并集源：人工表 ∪ 步骤 1（jd_paipai）归档的 API 块，按清洗名+价格去重合并。
SHEETS = [
    ("阿里", 0, "watch", None),
    ("京东", 1, "watch", ("京东API", 0)),
    ("阿里成交", 0, "deal_ali", None),
    ("京东成交", 1, "deal_jd", ("京东成交API", 0)),
]

TITLE_MAP = {
    "阿里": "阿里法拍新增关注top10",
    "京东": "京东法拍新增关注top10",
    "阿里成交": "阿里法拍成交top10",
    "京东成交": "京东法拍成交top10",
}

TOP_N = 10

DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"),
]
PURE_NUM = re.compile(r"^\d+(?:\.\d+)?$")
HEAT_W = re.compile(r"^(\d+(?:\.\d+)?)\s*[wW万]\s*人?围观?$")
HEAT_PLAIN = re.compile(r"^(\d+(?:\.\d+)?)\s*人围观?$")

# 拍卖开始时间：截到日期
START_CJK_FULL = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
START_CJK_MD = re.compile(r"\d{1,2}月\d{1,2}日")
START_ISO = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")

# ---------- 项目名称清洗：只留「区 + 小区（同级单位）+ 楼栋房号」 ----------
# 思路：不做多级地名链解析，改用「从右往左的候选回溯式右截断」。
# 地址与小区名之间常无分隔符，正向解析会互相吃字；反向取「最后一个像样的
# 地址分隔点」天然只留紧邻楼栋的那一段。规则详表见 references/fapai-daily.md。

JUNK_BRACKET_KEYS = ("不动产权证号", "室内物品", "不含室内", "不包含动产", "装修按现状",
                     "带租", "建筑面积", "面积")
DUP_PREFIX = [("变卖变卖", "变卖"), ("拍卖拍卖", "拍卖"), ("二拍二拍", "二拍")]

DISTRICTS = ("罗湖", "福田", "南山", "盐田", "宝安", "龙岗", "龙华", "坪山",
             "光明", "大鹏", "深汕", "前海")

BRACKETS = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
ADDR_KEYS = re.compile(r"路|街|道|号|巷|里|侧|对面|附近|旁|镇|社区|自然村|村民|街道")
BRACKET_PAIRS = {"（": "）", "(": ")", "【": "】", "[": "]", "［": "］"}
BRACKET_CLOSES = set(BRACKET_PAIRS.values())
CJK = re.compile(r"[\u4e00-\u9fa5]")

# 需保留并前置的案件/处置标记（「二拍」「变卖」必须保留：promote_er 依赖「二拍」）
KEEP_TAG = re.compile(r"^(?:[（(【\[]\s*(?:破|刑|破产|刑事|变卖|第[一二三四五六七八九十]+次)"
                      r"[^）)】\]]{0,16}[）)】\]]|二拍|变卖)\s*")
# 「（第二次拍卖）/（第二次）/第2次拍卖」→ 二拍（前置）；「第一次/第1次」默认一拍，直接删除
SECOND_AUCTION = re.compile(r"^第\s*(?:二|2)\s*次\s*(?:拍卖)?\s*[::]?\s*")
SECOND_MARK = re.compile(r"[（(【\[]?\s*第\s*(?:二|2)\s*次\s*(?:拍卖)?\s*[）)】\]]?|二拍")
FIRST_MARK = re.compile(r"[（(【\[]?\s*第\s*(?:一|1)\s*次\s*(?:拍卖)?\s*[）)】\]]?")
# 小区名中的「花园」删除；「花园城」是专名，保留
GARDEN_RE = re.compile(r"花园(?!城)")
STRIP_VERB = re.compile(r"^(?:关于)?(?:[一二三四五]\s*拍(?:拍卖)?|司法拍卖|二拍拍卖|拍卖|竞价)+"
                        r"(?:公告)?[::]?\s*")
OWNER_HEAD = re.compile(r"^(?:被执行人|案外人|申请人)?[^，,。；;]{0,30}?(?:名下|所有|拥有|所属)")
LOCATED = re.compile(r"^(?:位于|坐落于|坐落|地处|地址[位于]?|址于)\s*")
PROVINCE = re.compile(r"^(?:中国)?(?:广东省)?\s*")
CITY = re.compile(r"^(?:深圳市?|东莞市?|惠州市?|广州市?|佛山市?|中山市?|珠海市?|清远市?|河源市?)\s*")
CASENO = re.compile(r"^(?:[（(]\d{4}[）)]\s*)?[^，,。]{0,8}执[^号]{0,12}号(?:案件下|案下|下)?\s*")
STRIP_LEAD = re.compile(r"^(?:关于|整体|序号\s*\d+\s*[-－—_])\s*")
# 开头的营销/处置括注（「（无底价拍卖）」「【…不限购】」），破/刑/第N次已由 KEEP_TAG 保留
MOD_BRACKET = re.compile(r"^[（(【\[][^）)】\]]*(?:拍卖|变卖|特价|不限购|精装|处置|无底价)"
                         r"[^）)】\]]*[）)】\]]\s*")

ROAD_TAIL = "高速公路|高速|公路|大道|道|路|街|巷|里|段"
ADMIN_TAIL = ("街道办|街道|社区|镇|工业区|开发区|保税区|高新区|科技园|工业园|产业园|"
              "物流园|软件园|园区|片区|商圈|新区|厂区")
TAIL_PATS = [
    re.compile(r"(?:" + ROAD_TAIL + r")"),
    re.compile(r"(?:" + ADMIN_TAIL + r")"),
    re.compile(r"(?<!新)村"),                      # 灵芝新村 / 园岭新村 属小区名
    re.compile(r"小区"),
    re.compile(r"(?:路|街|大道|道|公路|高速)[东西南北]"),
    re.compile(r"(?:路|街|大道|道|公路|高速)[东西南北]?(?:侧|面|边|角)"),
    re.compile(r"(?:[东南西北]{2}角|[东南西北]{1,2}(?:侧|面|边)|以[东南西北]|交汇处|交汇|交界处|交界|"
               r"交叉口|路口|附近|对面|旁|之中)"),
    re.compile(r"(?:\d+(?:-\d+)?\s*(?:号|区)|第[一二三四五六七八九十\d]+号)"),
]
BUILDING = re.compile(r"(?:负[一二三四五六七八九十百\d]+\s*层|"
                      r"第?[一二三四五六七八九十百\d]+\s*(?:栋|幢|座|号楼)|"
                      r"[A-Za-z]\s*(?:栋|座|幢)|"
                      r"[一二三四五六七八九十百\d]+\s*(?:单元|室|铺|层)|"
                      r"第[一二三四五六七八九十百\d]+层|#|＃)")
TAIL_STOP = re.compile(r"(?:房产|房屋|不动产|房地产|拆迁|权益|使用权)")
TAIL_TYPE = re.compile(r"(?:商铺|车位|车库|厂房|住宅|公寓|办公|仓库|宿舍)+$")
NOISE_TAIL = re.compile(r"(?:[、，,。；;\s]*(?:共计|合计)\s*(?:\d+|[一二三四五六七八九十百千]+)\s*[套处宗栋]|等\s*(?:\d+|[一二三四五六七八九十百千]+)\s*[套处宗])+$")
LEAD_NOISE = re.compile(r"^(?:的|房产|房屋|不动产|房地产|号|[（(]第[一二三四五六七八九十]+次[）)])+")
LEAD_DIR = re.compile(r"^(?:[东南西北]{2}角|[东南西北]{1,2}(?:侧|面|边)|以[东南西北]|"
                      r"交汇处|交界处|交叉口|路口|旁)")
# 句中「房产」类词（如 …B-315号房产（第二次））与数字后的「号」
PROPERTY_WORD = re.compile(r"(?:的)?(?:房产|房屋|不动产|房地产)(?=[（(\s、，,]|$)")
NUM_HAO = re.compile(r"(?<=[\dA-Za-z])号(?=[（(\s]|$)")
# 房号后紧跟案件括注（…1201房（第二次））：房/室一并删掉
UNIT_TAIL = re.compile(r"(?<=[\dA-Za-z])\s*[房室](?=[（(])")
# 裸露的面积注记（建筑面积282.06㎡），括号形式的由 JUNK_BRACKET_KEYS 处理
AREA_NOTE = re.compile(r"\s*(?:建筑面积|房屋面积|面积)[:：]?\s*[\d.]+\s*(?:㎡|平方米|平米|m2|M2)")
# 括注外的权证号尾巴（…110C、权证号为粤（2020）…）
CERT_MARK = re.compile(r"[、，,;；]?\s*(?:不动产权证号|不动产权证书号|不动产证号|房产证号|产权证号|权证号|证书号)")
MULTI = re.compile(r"[、，,]\s*(?=(?:广东省)?深圳市|"
                   r"(?:罗湖|福田|南山|盐田|宝安|龙岗|龙华|坪山|光明|大鹏|深汕|前海)区)")

# 城区提取
KNOWN_DISTRICTS = list(DISTRICTS)
LOCALITY_GENERIC = re.compile(r"([\u4e00-\u9fa5]{2})(?:新区|街道|镇|区|县)")
GENERIC_BAD = ("小区", "工业区", "商业区", "住宅区", "宿舍区", "生活区", "科技区",
               "开发区", "经济区", "保税区", "加工区", "厂区", "校区", "园区")


# ---------- 解析工具 ----------

def parse_date_cell(v):
    """单元格 -> datetime.date | None（兼容中文/ISO 文本、Excel 序列值、日期对象）"""
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # Excel 序列值：1900 前后至 2100 年约合 1..80000，热度/价格列不在扫描范围
        if 20000 <= float(v) <= 80000:
            try:
                d = from_excel(v)
                return d.date() if isinstance(d, dt.datetime) else d
            except Exception:
                return None
        return None
    if isinstance(v, str):
        s = v.strip().strip("　")
        for pat in DATE_PATTERNS:
            m = pat.search(s)
            if m:
                y, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    return dt.date(y, mo, dy)
                except ValueError:
                    return None
    return None


def is_marker(row, date_col):
    """日期标记行：日期列可解析成日期，且整行不含 URL（数据行必有网址列）"""
    if date_col >= len(row):
        return None
    d = parse_date_cell(row[date_col])
    if d is None:
        return None
    for cell in row:
        if isinstance(cell, str) and cell.strip().lower().startswith(("http://", "https://")):
            return None
    return d


# 日期标记行的列漂移容错：阿里 sheet 自 2026-11-05 起日期写进了 B 列（A 列空），
# 只认单一 date_col 会把当天块漏掉并静默回退到十几天前的块。改为「首选列 + 前 3 列」
# 依次探测；非空格数 ≤2 是因为部分老块把日期在 A、B 各写了一遍。
DATE_COL_PROBE = (0, 1, 2)
MARKER_MAX_CELLS = 2


def _non_empty_cells(row):
    return [c for c in row if c is not None and str(c).strip() not in ("", "-", "—")]


def _has_url(row):
    return any(isinstance(c, str) and c.strip().lower().startswith(("http://", "https://"))
               for c in row)


def find_markers(rows, preferred=None):
    """全部日期标记行 -> [(row_idx_0based, date)]；preferred 为首选日期列"""
    cols = ([preferred] if preferred is not None else []) + \
           [c for c in DATE_COL_PROBE if c != preferred]
    markers = []
    for i, r in enumerate(rows):
        if not r or _has_url(r) or len(_non_empty_cells(r)) > MARKER_MAX_CELLS:
            continue
        for c in cols:
            if c >= len(r):
                continue
            d = parse_date_cell(r[c])
            if d is not None:
                markers.append((i, d))
                break
    return markers


def is_data_row(row):
    """数据行：项目名称（A 列）非空"""
    v = row[0] if row else None
    return isinstance(v, str) and v.strip() != ""


def parse_price_wan(v):
    """价格 -> 万元 float。兼容 284.49万 / ¥355.62万 / 2.5亿 / 裸数字(按万) / 1234元"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "").replace("，", "").replace("￥", "").replace("¥", "").replace(" ", "")
    if not s:
        return None
    mult = None
    if s.endswith("亿元"):
        s, mult = s[:-2], 10000.0
    elif s.endswith("亿"):
        s, mult = s[:-1], 10000.0
    elif s.endswith("万元"):
        s, mult = s[:-2], 1.0
    elif s.endswith("万"):
        s, mult = s[:-1], 1.0
    elif s.endswith("元"):
        s, mult = s[:-1], 0.0001
    try:
        num = float(s)
    except ValueError:
        return None
    return num * (mult if mult is not None else 1.0)


def parse_heat(v):
    """F 列热度 -> float | None。忽略 单价（元/平）等无关数据"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "").replace("，", "").replace(" ", "")
    if not s:
        return None
    if PURE_NUM.match(s):
        return float(s)
    m = HEAT_W.match(s)  # 3.63w人围观 / 3.63万围观
    if m:
        return float(m.group(1)) * 10000.0
    m = HEAT_PLAIN.match(s)  # 1900人围观
    if m:
        return float(m.group(1))
    return None  # 元/平、商业 等噪声


def fmt_wan(v):
    if v is None:
        return "—"
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def fmt_ratio(cur, ev):
    if cur is None or ev is None or ev == 0:
        return "—"
    return f"{cur / ev * 100:.1f}%"


def fmt_start(v):
    """拍卖开始时间只保留到日期，统一 MM-DD：'11月23日 10:00:00开始' -> '11-23'"""
    if v is None:
        return "—"
    s = str(v).strip()
    if not s:
        return "—"
    m = re.search(r"(?:\d{4}\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"\d{4}[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s  # 进行中/预告中/已结束 等无日期文本


def _balanced(s):
    """括号是否配对"""
    stack = []
    for ch in s:
        if ch in BRACKET_PAIRS:
            stack.append(ch)
        elif ch in BRACKET_CLOSES:
            if not stack or BRACKET_PAIRS[stack.pop()] != ch:
                return False
    return not stack


def _fix_brackets(s):
    """括号配平：不配平的括注整段丢弃，避免输出半截括号（如 …半岛城邦花园(三期2号楼26B）"""
    if _balanced(s):
        return s
    res, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch in BRACKET_PAIRS:
            j = s.find(BRACKET_PAIRS[ch], i + 1)
            if j >= 0 and _balanced(s[i:j + 1]):
                res.append(s[i:j + 1])
                i = j + 1
                continue
            k = i + 1  # 落单开括号：丢到下一个括号字符
            while k < n and s[k] not in BRACKET_PAIRS and s[k] not in BRACKET_CLOSES:
                k += 1
            i = k
            continue
        if ch in BRACKET_CLOSES:  # 落单闭括号：直接丢
            i += 1
            continue
        res.append(ch)
        i += 1
    return "".join(res)


def _bracket_groups(s):
    """配平括号组 [(start, end, inner)]，按 start 升序"""
    stack, groups = [], []
    for i, ch in enumerate(s):
        if ch in BRACKET_PAIRS:
            stack.append((i, ch))
        elif ch in BRACKET_CLOSES:
            for k in range(len(stack) - 1, -1, -1):
                if BRACKET_PAIRS[stack[k][1]] == ch:
                    groups.append((stack[k][0], i + 1, s[stack[k][0] + 1:i]))
                    del stack[k:]
                    break
    return groups


def _drop_bracket_groups(s, pred):
    """删除满足 pred(内容) 的配平括号组（内层优先，循环到稳定）"""
    prev = None
    while s != prev:
        prev = s
        groups = sorted(_bracket_groups(s), key=lambda g: g[1] - g[0])   # 内层（跨度短）优先
        hit = next(((st, en) for st, en, inner in groups if pred(inner)), None)
        if hit is None:
            break
        s = s[:hit[0]] + s[hit[1]:]
    return s


def _drop_junk_brackets(s):
    """删注记型括号（不动产权证号 / 不含室内物品 / 建筑面积 / 带租 …），其余括注保留"""
    return _drop_bracket_groups(s, lambda c: any(k in c for k in JUNK_BRACKET_KEYS))


def _strip_prefix(s):
    """剥前缀；返回 (保留的案件标记串, 剩余主体)"""
    tags = []
    prev = None
    while s != prev:
        prev = s
        m = KEEP_TAG.match(s)
        if m:
            tag = m.group(0).strip()
            if re.search(r"第\s*(?:二|2)\s*次", tag):
                tag = "二拍"          # （第二次拍卖）/（第二次）→ 二拍
            tags.append(tag)
            s = s[m.end():]
            continue
        m = SECOND_AUCTION.match(s)
        if m:
            tags.append("二拍")        # 裸「第二次拍卖」→ 二拍
            s = s[m.end():]
            continue
        for pat in (STRIP_LEAD, MOD_BRACKET, STRIP_VERB, CASENO, OWNER_HEAD, LOCATED, PROVINCE, CITY):
            s = pat.sub("", s)
        s = s.strip(" 、，,。；;：:-—")
    return "".join(tags).replace("二拍二拍", "二拍"), s


def _find_district(s):
    for d in DISTRICTS:
        if s.startswith(d + "新区"):
            return d + "新区"
        if s.startswith(d + "区"):
            return d + "区"
        if s.startswith(d):
            return d
    return None


def _mask_brackets(s):
    chars = list(s)
    for st, en, _ in _bracket_groups(s):
        for i in range(st, en):
            chars[i] = "\x00"
    return "".join(chars)


def _candidates(head):
    """楼栋锚之前的候选分隔点结束位置，从右到左"""
    masked = _mask_brackets(head)
    ends = {0}
    for pat in TAIL_PATS:
        for m in pat.finditer(masked):
            ends.add(m.end())
    out = []
    for e in sorted(ends, reverse=True):
        while e < len(head) and head[e] in "、，,。；;：: 　":
            e += 1
        if e < len(head) and head[e] in "侧边面角":
            continue                    # 方位词被切开（路西|侧），跳过这个点
        out.append(e)
    return sorted(set(out), reverse=True)


def _strip_tail_noise(s):
    """删尾部房产类词/处置词，以及数字后的「号/房/室」（可连续，如 307号 / 403房 / 房产一拍）"""
    prev = None
    while s != prev:
        prev = s
        s = s.strip(" 、，,。；;：:")
        for w in ("房地产", "不动产", "房屋", "房产"):
            if s.endswith(w):
                s = s[:-len(w)].rstrip("的")
                break
        s = re.sub(r"(?:商务|酒店式|服务式)公寓$", "", s)
        s = re.sub(r"(?:[一二三四五]\s*拍(?:拍卖)?|司法拍卖|拍卖|变卖|公告)$", "", s)
        s = re.sub(r"(?<=[\dA-Za-z])\s*[号房室]+$", "", s)
    return s


def _strip_addr_brackets(s):
    """删地址型括注（内容含 路/街/道/号/巷/里/镇/社区/村），保留「（车位号N）」这类编号括注"""
    return _drop_bracket_groups(
        s, lambda c: not re.match(r"^\s*车位号", c) and bool(ADDR_KEYS.search(c)))


GENERIC_ONLY = ("商铺", "车位", "车库", "仓库", "厂房", "住宅", "公寓", "物业", "使用权",
                "产权", "份额", "办公", "宿舍", "土地", "房产", "房屋", "汽车位",
                # 只有小区后缀、没有专名的残片（…里|家园 → 只剩「家园」）
                "家园", "花园", "花苑", "新村", "小区", "大厦", "广场", "名苑", "雅苑",
                "山庄", "公馆", "华府", "豪园", "天地", "国际", "时代", "中心", "大楼")
ROOM_START = re.compile(r"^\d+(?:-\d+)?\s*号")
# 纯数量/房号残片：一处 / 四号 / 整栋 / 4套商铺 / 956个车位
FRAGMENT_ONLY = re.compile(
    r"^(?:[一两二三四五六七八九十百千\d]+\s*(?:套|处|宗|栋|幢|座|间|号|层|室|房|铺|个|份)|[整全][栋幢套])"
    r"(?:商铺|车位|车库|房产|房屋|不动产|住宅|公寓|物业|使用权|产权|份额|办公|宿舍|厂房|"
    r"仓库|土地|汽车位|收益权)*$")
# 以小类建筑词/分区词开头的残段（厂房、A单元、塔楼、北区、一期…）不是小区名；
# 命中时触发候选回溯，把左侧真正的「XX花园/XX大厦/XX广场」一起带上。
# 带小区名前缀的（XX大厦裙楼）不会命中，因为锚定在残段首字。
TYPE_HEAD = re.compile(
    r"^(?:[A-Za-z]?\s*(?:单元|室|铺|层)|[A-Za-z]?\s*塔楼|"
    r"(?:[东南西北中]?(?:研发楼|商业办公楼|商业楼|办公楼|综合楼|住宅楼|商住楼|宿舍楼|教学楼)|"
    r"厂房|裙楼|写字楼|车间|食堂|保安亭|地下室|半地下)|"
    r"民住宅|私宅|住宅|商铺|地块|车位|车库|仓库|游泳馆|"
    r"[东南西北中]区|第?[一二三四五六七八九十百\d]+期)")
ROAD_END = re.compile(r"(?:路|街|大道|道|公路|高速)$")


def _plausible(rest):
    """切完是否还剩一个像样的「小区名」（≥2 个汉字，且不是房号/数量/类型词残片）"""
    t = _strip_addr_brackets(rest)
    t = PROPERTY_WORD.sub("", t)
    t = LEAD_NOISE.sub("", t)
    t = _strip_tail_noise(t)
    t = LEAD_DIR.sub("", t)
    t = NUM_HAO.sub("", t)
    t = _drop_bracket_groups(t, lambda c: True)     # 去掉剩余括注后再判断主体
    t = t.strip(" 、，,。；;：:.·")
    if len(CJK.findall(t)) < 2:
        return False
    if ROOM_START.match(t) or FRAGMENT_ONLY.match(t) or TYPE_HEAD.match(t):
        return False
    if ROAD_END.search(t):         # 只剩路名（华景道 / XX路）不算小区名
        return False
    if t in GENERIC_ONLY:          # 只剩房产类型词
        return False
    return True


def _clean_one(s):
    tags, s = _strip_prefix(s)
    s = CERT_MARK.split(s, 1)[0]          # 括注外的权证号尾巴整段丢弃
    district = _find_district(s)
    if district is None:
        # 区名不在开头（如「被执行人…名下的位于…光明区…」前缀过长没剥干净）：
        # 取最靠左的「深圳市X区」锚点，丢掉其前的权利人/地址描述。
        m = re.search(r"(?:深圳市?)?((?:" + "|".join(DISTRICTS) + r")(?:新区|区))", s)
        if m:
            district = m.group(1)
            s = district + s[m.end():]
    body = s[len(district):] if district else s
    body = body.replace("社区", "")           # 社区出现在小区名内部时也是噪声

    m = BUILDING.search(_mask_brackets(body))   # 括号内（现地址/别名）的楼栋不作为锚
    head, tail = (body[:m.start()], body[m.start():]) if m else (body, "")
    if tail:
        tail = PROPERTY_WORD.sub("", tail)
        if not re.search(r"[、，,]\s*\d", tail):    # 多宗连写（28栋商铺117、…）整段保留
            tm = TAIL_STOP.search(tail)
            if tm:
                tail = tail[:tm.start()]
        tail = TAIL_TYPE.sub("", _strip_tail_noise(tail))

    cut = 0
    for e in _candidates(head):
        if _plausible(head[e:]):
            cut = e
            break

    core = head[cut:]
    joiner = ""
    hao_tail = ""
    ms = re.search(r"[、，,]\s*$", core)      # 保留 14、15座 这类楼栋分隔
    if ms and tail:
        joiner = ms.group(0)
    if tail and re.search(r"\d\s*号$", core) and re.match(r"[\dA-Za-z第]", tail):
        hao_tail = "号"                       # 门牌号后紧跟楼栋（160号2栋）时别把「号」吃掉
    elif tail and re.search(r"\d\s*号$", core) and re.match(r"[一二三四五六七八九十]", tail):
        hao_tail = "号"                       # 24号四层私宅 / 2号七层住宅楼
    core = LEAD_DIR.sub("", core)
    core = _strip_addr_brackets(core)
    core = AREA_NOTE.sub("", core)
    core = PROPERTY_WORD.sub("", core)
    core = LEAD_NOISE.sub("", core)
    core = _strip_tail_noise(core)
    core = NUM_HAO.sub("", core)
    core = core.strip(" 、，,。；;：:.")      # 不含 ）)】]，避免吃掉配对括号
    core = _fix_brackets(core)
    if not _plausible(core):
        return ""
    out = tags + (district or "") + core + joiner + hao_tail + tail
    out = AREA_NOTE.sub("", out)
    out = _strip_addr_brackets(out)
    out = PROPERTY_WORD.sub("", out)
    out = _strip_tail_noise(out)
    out = NUM_HAO.sub("", out)
    out = UNIT_TAIL.sub("", out)
    out = NOISE_TAIL.sub("", out)
    out = _fix_brackets(out)
    return out.strip(" 、，,。；;：:.")


def clean_name_detail(name):
    """返回 (精简名, 是否因切空/过短而回退原名)

    标记归一（2026-11-05）：
      - 「（第二次）/（第二次拍卖）/第2次」→ 删除原标记，名称最前面加「二拍」（已含二拍则不重复）；
      - 「（第一次）/（第一次拍卖）/第1次」→ 默认一拍，直接删除；
      - 小区名中的「花园」删除（「花园城」专名保留）；
      - 「变卖」保留并前置。
    """
    raw = str(name or "").strip()
    for a, b in DUP_PREFIX:
        raw = raw.replace(a, b)
    is_second = bool(SECOND_MARK.search(raw))
    base = FIRST_MARK.sub("", SECOND_MARK.sub("", raw))
    outs = [o for o in (_clean_one(_drop_junk_brackets(p)) for p in MULTI.split(base)) if o]
    out = GARDEN_RE.sub("", "、".join(outs))
    if len(out) >= 4:
        if "变卖" in raw and "变卖" not in out:
            out = "变卖" + out
        if is_second:
            out = "二拍" + out            # 二拍永远在最前
        return out, False
    fallback = GARDEN_RE.sub("", base).strip(" 、，,。；;：:")
    if fallback:
        if is_second:
            fallback = "二拍" + fallback
        return fallback, True
    return raw, True


def clean_name(name):
    """项目名称精简：只保留「区 + 小区（同级单位）+ 楼栋房号」。

    规则见 references/fapai-daily.md「项目名称清洗」。切不动/切空一律回退原名。
    """
    return clean_name_detail(name)[0]


def promote_er(name):
    """含"二拍"的名称把"二拍"两字提到首位（已在首位则不变）"""
    if "二拍" not in name or name.startswith("二拍"):
        return name
    i = name.find("二拍")
    s = "二拍" + name[:i] + name[i + 2:]
    for empty in ("（）", "()", "【】", "[]"):
        s = s.replace(empty, "")
    return s


def extract_locality(name):
    """取城区名称：'二拍'名称看'二拍'之后；优先已知城区，其次通用 XX区/镇/街道"""
    seg = name.split("二拍", 1)[1] if "二拍" in name else name
    best, best_pos = None, None
    for d in KNOWN_DISTRICTS:
        p = seg.find(d)
        if p >= 0 and (best_pos is None or p < best_pos):
            best, best_pos = d, p
    if best:
        return best
    for m in LOCALITY_GENERIC.finditer(seg):
        tok = m.group(0)
        if any(tok.endswith(b) or tok == b for b in GENERIC_BAD):
            continue
        return tok
    return None


def locality_pinyin(locality):
    """城区名称首字拼音（如 '罗湖区' -> 'luo'）；无 pypinyin 时退化为字符本身"""
    if not locality:
        return ""
    ch = locality[0]
    if _HAS_PINYIN:
        try:
            py = lazy_pinyin(ch)[0]
            return py.lower() if isinstance(py, str) else ""
        except Exception:
            return ""
    return ch


def sort_by_locality(items):
    """二拍项独立成组置顶，组内按城区首字拼音降序；其余按城区降序；无城区排组内最后"""
    if not _HAS_PINYIN:
        sys.stderr.write("提示：未安装 pypinyin（python -m pip install pypinyin），"
                         "城区排序退化为字符码降序\n")

        def order(group):
            known = sorted([i for i in group if i["loc"]],
                           key=lambda x: x["loc"][0], reverse=True)
            return known + [i for i in group if not i["loc"]]
    else:
        def order(group):
            known = sorted([i for i in group if i["py"]],
                           key=lambda x: x["py"], reverse=True)
            return known + [i for i in group if not i["py"]]
    erpai = [i for i in items if i.get("is_er")]
    normal = [i for i in items if not i.get("is_er")]
    return order(erpai) + order(normal)


def md_cell(s):
    if s is None:
        return "—"
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip() or "—"


# ---------- 数据提取 ----------

def extract_block(rows, date_col, target):
    """定位 target 日期块；当天无有效块则回退到最新的有效块。
    date_col 为首选日期列，列漂移时按 find_markers 自动探测。
    返回 (data_rows, used_date, fallback: bool, block_size: int)"""
    markers = find_markers(rows, date_col)
    if not markers:
        return [], None, False, 0

    blocks = []  # (date, row_idx_of_marker, data_rows)
    for k, (mi, d) in enumerate(markers):
        end = markers[k + 1][0] if k + 1 < len(markers) else len(rows)
        body = [r for r in rows[mi + 1:end] if is_data_row(r)]
        blocks.append((d, mi, body))

    for d, _, body in blocks:
        if d == target and body:
            return body, d, False, len(body)
    # 回退：取日期最新且非空的块
    valid = [b for b in blocks if b[2]]
    if not valid:
        return [], None, False, 0
    d, _, body = max(valid, key=lambda b: (b[0], b[1]))
    return body, d, True, len(body)


def _zero_none(v):
    """评估价/当前价 0 视为缺失（API 用 0 表示无评估价，不能显示成「评估价 0」）"""
    if v is None or v <= 0:
        return None
    return v


def _new_item(name, cur, ev, start):
    """清洗后的名称 + 价格 + 开始时间 -> 输出项（含二拍标记、城区与拼音键）"""
    is_er = "二拍" in name
    loc = extract_locality(name)
    return {"name": md_cell(name), "plain": name, "cur": cur, "ev": ev,
            "start": md_cell(start), "loc": loc, "py": locality_pinyin(loc),
            "is_er": is_er}


def make_item(r):
    """人工表 6 列行 -> 输出项（A 名称｜B 网址｜C 当前价｜D 评估价｜E 开始时间｜F 热度）"""
    cur = _zero_none(parse_price_wan(r[2])) if len(r) > 2 else None
    ev = _zero_none(parse_price_wan(r[3])) if len(r) > 3 else None
    start = fmt_start(r[4]) if len(r) > 4 else "—"
    name = promote_er(clean_name(str(r[0])))
    return _new_item(name, cur, ev, start)


def make_item_api(r):
    """API 16 列行 -> 输出项（C 物业名称｜E 起拍｜F 当前价｜G 评估价｜M 开始时间）"""
    cur = _zero_none(parse_price_wan(r[5])) if len(r) > 5 else None
    ev = _zero_none(parse_price_wan(r[6])) if len(r) > 6 else None
    start = fmt_start(r[12]) if len(r) > 12 else "—"
    name = promote_er(clean_name(str(r[2]) if len(r) > 2 else ""))
    return _new_item(name, cur, ev, start)


def has_prices(it):
    """入选门槛：当前价与评估价都有有效数据（不编数）"""
    return it["cur"] is not None and it["ev"] is not None and it["ev"] > 0


def ratio_of(it):
    """折价率 = 当前价/评估价（小数），无法计算返回 None"""
    if not has_prices(it):
        return None
    return it["cur"] / it["ev"]


# ---------- 多源并集（2026-11-14：人工表 ∪ 步骤 1 的 API 归档） ----------
# 人工表有 API 抓不到的标的（如 2026-11-14 实测 4 条阳光新境园在 京东API 全表 0 命中），
# API 表有 100% 可解析的围观数且不漏人工漏抄的行；两者按「清洗名去处置标记 + 价格」
# 去重后合并，数值以 API 为准，名称取带处置标记的一方。

MARK_RE = re.compile(r"^(?:二拍|变卖|拍卖)+")
MERGE_PRICE_TOL = 0.05          # 万元；人工表与 API 表存在 0.01 级取整差
NO_DATE_START = ("—", "进行中", "预告中", "已结束", "")
# 京东成交API 的块是「--since 窗口内且历史未出现」的增量，可能夹带多日前的旧成交
# （2026-11-14 实测夹带 11-05 怡景花园 2093 万）。人工表的口径是「业务日前一天的成交」，
# 周末/断档最多滞后 2 天，故 API 成交行按「距业务日 ≤3 天」过滤。
API_DEAL_MAX_LAG_DAYS = 3


def _same_property(a, b):
    """同名（去处置标记）+ 价格差 ≤0.05 万 → 同一物业（人工表与 API 表存在 149.87/149.88 这类取整差）"""
    if MARK_RE.sub("", a["plain"]) != MARK_RE.sub("", b["plain"]):
        return False
    if a["cur"] is None or b["cur"] is None:
        return True
    return abs(a["cur"] - b["cur"]) <= MERGE_PRICE_TOL


def _retag(it, name):
    it = dict(it)
    it["plain"] = name
    it["name"] = md_cell(name)
    it["is_er"] = "二拍" in name
    it["loc"] = extract_locality(name)
    it["py"] = locality_pinyin(it["loc"])
    return it


def _merge_pair(old, new):
    """old=先到的源（人工），new=后到的源（API）：数值取 new，名称取带处置标记的一方"""
    name = old["plain"]
    if MARK_RE.search(new["plain"]) and not MARK_RE.search(name):
        name = new["plain"]
    elif old["loc"] is None and new["loc"] is not None:
        name = new["plain"]
    it = _retag(old, name)
    if new["cur"] is not None:
        it["cur"] = new["cur"]
    if new["ev"] is not None:
        it["ev"] = new["ev"]
    if new["start"] not in NO_DATE_START and it["start"] in NO_DATE_START:
        it["start"] = new["start"]
    if new.get("heat") is not None:
        it["heat"] = new["heat"]
    return it


def merge_items(*groups):
    """多源合并：保留首次出现顺序，后到源的数值覆盖先到源"""
    merged = []
    for items in groups:
        for it in items:
            hit = next((i for i, m in enumerate(merged) if _same_property(m, it)), None)
            if hit is None:
                merged.append(dict(it))
            else:
                merged[hit] = _merge_pair(merged[hit], it)
    return merged


def _end_date(v):
    """API 结束时间 '2026-11-13 11:26' -> date | None"""
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(v or ""))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _deal_in_window(r, target):
    """API 成交行时效：结束时间距业务日 ≤ API_DEAL_MAX_LAG_DAYS 天（且不晚于业务日）"""
    d = _end_date(r[13] if len(r) > 13 else None)
    if d is None or target is None:
        return True                      # 时间缺失不擅自丢弃，交由入选规则决定
    lag = (target - d).days
    return 0 <= lag <= API_DEAL_MAX_LAG_DAYS


def collect_watch(body, api=False):
    """新增关注候选：有当前价即入选（不编数）；热度取人工 F 列或 API 围观数"""
    items = []
    for r in body:
        if api:
            heat = r[11] if len(r) > 11 else None
            if not isinstance(heat, (int, float)) or isinstance(heat, bool):
                heat = None
            it = make_item_api(r)
        else:
            heat = parse_heat(r[5]) if len(r) > 5 else None
            it = make_item(r)
        if it["cur"] is None:
            continue
        it["heat"] = heat
        items.append(it)
    return items


def collect_deal(body, mode, api=False, target=None):
    """成交候选：人工表按 F 列（京东含「出价」/阿里出价人数>0），API 按出价次数>0 + 时效窗口"""
    items = []
    for r in body:
        if api:
            bids = r[10] if len(r) > 10 else None
            if not (isinstance(bids, (int, float)) and not isinstance(bids, bool) and bids > 0):
                continue                     # 出价 0 = 流拍，不算成交
            if not _deal_in_window(r, target):
                continue
            it = make_item_api(r)
        else:
            f = r[5] if len(r) > 5 else None
            if mode == "deal_jd":
                if not (isinstance(f, str) and "出价" in f):
                    continue                 # 忽略 'X人围观' 等（流拍）
            else:  # deal_ali
                n = parse_heat(f)
                if n is None or n == 0:
                    continue
            it = make_item(r)
        if it["cur"] is None:
            continue
        items.append(it)
    return items


def select_watch(items):
    """TOP 名额分两层：有围观按围观降序，围观不可解析按当前价降序补足（2026-11-03 容错）"""
    with_heat = sorted([x for x in items if x.get("heat") is not None],
                       key=lambda x: x["heat"], reverse=True)
    without_heat = [x for x in items if x.get("heat") is None]
    without_heat.sort(key=lambda x: x["cur"] if x["cur"] is not None else 0, reverse=True)
    return (with_heat + without_heat)[:TOP_N]


def select_deal(items):
    """折价率 > 100% 必选；其余按折价率由低到高；评估价缺失排最后补足（2026-11-03 放宽）"""
    for it in items:
        it["ratio"] = ratio_of(it)
    hot = [it for it in items if it["ratio"] is not None and it["ratio"] > 1.0]
    rest_with_ratio = sorted([it for it in items if it["ratio"] is not None and it["ratio"] <= 1.0],
                             key=lambda x: x["ratio"])
    rest_no_ratio = [it for it in items if it["ratio"] is None]
    return (hot + rest_with_ratio + rest_no_ratio)[:TOP_N]


def build_watch_rows(body):
    """单源（人工表）新增关注 -> (rows, 候选数)，保留给 --audit 与回归用"""
    items = collect_watch(body)
    return sort_by_locality(select_watch(items)), len(items)


def build_deal_rows(body, mode):
    """单源（人工表）成交 -> (rows, 候选数)"""
    items = collect_deal(body, mode)
    return sort_by_locality(select_deal(items)), len(items)


# ---------- 输出 ----------

def render_watch_table(items):
    lines = ["| 项目名称 | 当前价（万） | 评估价（万） | 折价率 | 拍卖开始时间 |",
             "| --- | --- | --- | --- | --- |"]
    for it in items:
        lines.append(f"| {it['name']} | {fmt_wan(it['cur'])} | {fmt_wan(it['ev'])} "
                     f"| {fmt_ratio(it['cur'], it['ev'])} | {it['start']} |")
    return "\n".join(lines)


def render_deal_table(items):
    lines = ["| 项目名称 | 成交价（万） | 评估价（万） | 折价率 |",
             "| --- | --- | --- | --- |"]
    for it in items:
        lines.append(f"| {it['name']} | {fmt_wan(it['cur'])} | {fmt_wan(it['ev'])} "
                     f"| {fmt_ratio(it['cur'], it['ev'])} |")
    return "\n".join(lines)


SLIDE_HINT = '<p align="center">左右滑动查看表格</p>'


# ---------- 名称清洗回归用例与全量审查 ----------

CASES = [
    # 三条验收基准（逐字一致；2026-11-05 起小区名中的「花园」删除）
    ("南山区内环路以北、前海路以西鼎太风华社区七期14、15座14-308房产",
     "南山区鼎太风华七期14、15座14-308"),
    ("陈礼雄名下宝安区新安办广深路西侧碧海花园B栋4座307号房产拆迁后可获得的拆迁补偿安置权益",
     "宝安区碧海B栋4座307"),
    ("宝安区龙华街道梅观高速公路东侧春华四季园14栋三单元10B",
     "宝安区春华四季园14栋三单元10B"),
    # 标记保留
    ("（破）深圳坚达机械有限公司名下位于深圳市南山区深南路科技工业园15栋402房产",
     "（破）南山区科技工业园15栋402"),
    ("（刑）南山区东角头望海路半岛城邦花园(三期)2号楼26B",
     "（刑）南山区半岛城邦(三期)2号楼26B"),
    ("二拍深圳市福田区福田街道滨河大道南京基滨河时代广场北区（二期）3806号",
     "二拍福田区京基滨河时代广场北区（二期）3806"),
    # 房号 vs 门牌（「（第二次）」后置也归一为前置「二拍」）
    ("深圳市坪山区坪山街道盈富家园AB区B-315号房产（第二次）",
     "二拍坪山区盈富家园AB区B-315"),
    # 地址括注
    ("深圳市盐田北山大道（盐横路33号）星港名苑2-6B号房产",
     "盐田星港名苑2-6B"),
    # 村/新村
    ("关于拍卖深圳市宝安区新安街道办兴华路南侧灵芝新村四十五栋403房",
     "宝安区灵芝新村四十五栋403"),
    # 数字地块
    ("深圳市龙岗区中心城11区龙城国际花园6号楼1303房地产",
     "龙岗区龙城国际6号楼1303"),
    # 物业后缀删除
    ("深圳市罗湖区文锦北路翡翠公寓1505房产", "罗湖区翡翠公寓1505"),
    # 泛化楼型不得吃掉左侧小区名（研发楼/A单元/塔楼/北区/一期）
    ("广东省深圳市南山区北环大道方大广场（一期）3、4号研发楼3号楼1708房产",
     "南山区方大广场（一期）3、4号研发楼3号楼1708"),
    ("深圳市宝安区西乡街道领航城领翔华府A区2号A单元2703房产",
     "宝安区领航城领翔华府A区2号A单元2703"),
    ("深圳市福田区华侨城东路以西白石洲路以北碧海云天小区一期7号楼C-11D、C-12D",
     "福田区碧海云天小区一期7号楼C-11D、C-12D"),
    ("深圳市龙华区清祥路1号宝能科技园北区9栋房产", "龙华区宝能科技园北区9栋"),
    # 「里」结尾的小区名不被当成道路切掉
    ("关于拍卖深圳市光明区公明街道马山头社区82号幸福花园里B栋第三层301号房产",
     "光明区幸福里B栋第三层301"),
    # 括注外的权证号尾巴整段丢弃
    ("盐田区沙头角沙深路西南段瀚海翠庭 110C、权证号为粤（2020）深圳市不动产权第0147429号",
     "盐田区瀚海翠庭 110C"),
    # 房号后紧跟案件括注（房/室 一并删掉）；已含「二拍」时删除重复标记
    ("二拍坪山区坪山街道招商花园1栋B座1201房（第二次）",
     "二拍坪山区招商1栋B座1201"),
    # 「（第一次）」默认一拍，直接删除
    ("深圳市坪山区坪山街道豪方东园3栋14D房产（第一次）",
     "坪山区豪方东园3栋14D"),
    # 「花园城」属专名，不删「花园」
    ("深圳市南山区蛇口花园城三期20栋102、103房（第4次）",
     "南山区蛇口花园城三期20栋102、103（第4次）"),
    # 「（第二次拍卖）」/「（第二次）」统一精简为「二拍」
    ("（第二次拍卖）深圳市龙华区龙华街道人民路与八一路交汇处壹成中心花园4座1101房产",
     "二拍龙华区壹成中心4座1101"),
    ("第二次拍卖深圳市坪山区坪山街道天峦湖花园6栋1单元2603房产",
     "二拍坪山区天峦湖6栋1单元2603"),
    # 权利人前缀过长时的区名兜底（区名不在开头）
    ("被执行人罗坤登记在贵州德坤大数据(集团)有限公司名下的位于广东省深圳市光明区"
     "公明将石社区白松路与公常公路交汇处益田假日府邸A座1605号商务公寓",
     "光明区益田假日府邸A座1605"),
]


def self_test():
    """--self-test：跑回归用例，任一不过即返回 1"""
    bad = 0
    for raw, want in CASES:
        got = promote_er(clean_name(raw))
        if got == want:
            print(f"  ok   {got}")
        else:
            bad += 1
            print(f"  FAIL {raw}\n       want: {want}\n       got : {got}")
    if bad:
        print(f"名称清洗用例失败 {bad}/{len(CASES)}")
        return 1
    print(f"名称清洗用例全部通过（{len(CASES)} 条）")
    return 0


def audit_names(xlsx_path, out_tsv=None):
    """--audit-names：全量导出 原名 → 新名 并统计精简率/回退率/残留地址词"""
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    names = []
    for sheet, *_ in SHEETS:
        if sheet not in wb.sheetnames:
            continue
        for r in wb[sheet].iter_rows(values_only=True):
            if is_data_row(r):
                names.append(str(r[0]).strip())
    wb.close()

    addr_left = re.compile(r"街道|镇|村|社区|路|大道|公路")
    changed, fell_back, residual = 0, 0, []
    pairs = []
    for raw in names:
        cleaned, fb = clean_name_detail(raw)
        new = promote_er(cleaned)
        pairs.append((raw, new))
        if new != raw:
            changed += 1
        if fb:
            fell_back += 1
        if addr_left.search(new):
            residual.append((raw, new))

    total = len(names) or 1
    print(f"名称总数 {len(names)}；名称被精简 {changed}（{changed / total:.1%}）；"
          f"回退原名 {fell_back}（{fell_back / total:.1%}）；"
          f"仍含地址词 {len(residual)}（{len(residual) / total:.1%}）")
    cun = sum(1 for _, n in residual if "村" in n)
    print(f"  其中「村」类 {cun} 条（益田村/园岭新村/新秀村 等小区名自带）；"
          f"其他 {len(residual) - cun} 条（括注残片/小区内道路/外地房源）")
    if residual[:10]:
        print("残留地址词示例：")
        for raw, new in residual[:10]:
            print(f"  {new}    <- {raw}")
    if out_tsv:
        path = Path(out_tsv)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["原名\t新名"] + [f"{raw}\t{new}" for raw, new in pairs]
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"全量对照已写出：{path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="生成深圳法拍资产日报")
    ap.add_argument("--date", help="运行日期，默认今天，如 2026-10-27 或 2026年10月27日")
    ap.add_argument("--xlsx", default=DEFAULT_XLSX, help="数据源 xlsx 路径")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="日报输出目录")
    ap.add_argument("--out", help="显式指定输出文件路径（覆盖 out-dir）")
    ap.add_argument("--self-test", action="store_true", help="只跑名称清洗回归用例后退出")
    ap.add_argument("--audit-names", nargs="?", const="",
                    metavar="TSV", help="全量导出 原名→新名 并统计（可选：写出 TSV 路径）")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    xlsx = Path(args.xlsx)
    if not xlsx.exists():
        sys.stderr.write(f"数据源不存在：{xlsx}\n")
        sys.exit(2)

    if args.audit_names is not None:
        sys.exit(audit_names(xlsx, args.audit_names or None))

    target = parse_date_cell(args.date) if args.date else dt.date.today()
    if target is None:
        sys.stderr.write(f"无法识别日期：{args.date}\n")
        sys.exit(2)

    wb = load_workbook(xlsx, read_only=True, data_only=True)
    results = {}   # sheet -> dict
    order = []
    for name, date_col, mode, api_src in SHEETS:
        if name not in wb.sheetnames:
            print(f"[{name}] 工作表不存在，跳过")
            results[name] = {"items": [], "used": None, "fallback": False, "valid": 0, "block": 0, "mode": mode}
            order.append(name)
            continue
        rows = list(wb[name].iter_rows(values_only=True))
        body, used, fallback, block_size = extract_block(rows, date_col, target)

        api_body, api_used, api_block = [], None, 0
        if api_src:
            api_name, api_col = api_src
            if api_name in wb.sheetnames:
                api_rows = list(wb[api_name].iter_rows(values_only=True))
                api_body, api_used, _, api_block = extract_block(api_rows, api_col, target)
                if api_used != target:
                    api_body, api_block = [], 0   # 只并当日块；否则会把他日数据混进当日表
            else:
                print(f"[{name}] 并集源「{api_name}」不存在，仅用人工块")

        if not (used == target) and api_used == target:
            # 人工块是他日数据（当日没人工录入），只并入当日 API 块，避免两天混表
            body, block_size = [], 0
            print(f"[{name}] 提示：人工表当日无块（{used}），仅用 API 块出表",
                  file=sys.stderr)

        if mode == "watch":
            cands = merge_items(collect_watch(body),
                                collect_watch(api_body, api=True))
            items = sort_by_locality(select_watch(cands))
        else:
            cands = merge_items(collect_deal(body, mode),
                                collect_deal(api_body, "deal_jd", api=True, target=target))
            items = sort_by_locality(select_deal(cands))

        dates = [d for d in (used, api_used) if d]
        eff_used = target if target in dates else (max(dates) if dates else None)
        eff_fallback = bool(dates) and eff_used != target
        block = block_size + api_block
        results[name] = {"items": items, "used": eff_used, "fallback": eff_fallback,
                         "valid": len(cands), "block": block, "mode": mode,
                         "api_used": api_used, "api_block": api_block}
        tag = "回退" if eff_fallback else "当日"
        detail = f"块内行数={block_size}" + (f"+{api_block}(API)" if api_src else "")
        print(f"[{name}] {tag} 数据日期={eff_used} {detail} "
              f"候选行={len(cands)} 入选={len(items)}")
        if api_src and api_used != target:
            print(f"[{name}] 提示：API 源「{api_src[0]}」当日无块（{api_used}），"
                  f"仅人工块出表", file=sys.stderr)
        order.append(name)
    wb.close()

    # 组装 markdown（2026-11-05：去掉概览/数据日期/脚注等注释，表前统一放滑动提示）
    # 2026-11-14：回退块必须在正文可见，否则标题日期与数据日期不符而读者无从察觉
    out_lines = [f"# 深圳法拍资产日报（{target.isoformat()}）", ""]
    warnings = []

    for name in order:
        r = results[name]
        out_lines.append(f"## {TITLE_MAP[name]}")
        out_lines.append("")
        if r["fallback"] and r["used"]:
            out_lines.append(f"> ⚠️ 数据日期：{r['used'].isoformat()}"
                             f"（{target.isoformat()} 当日无有效块，已回退）")
            out_lines.append("")
            warnings.append(f"{name} 当日无有效块，回退至 {r['used'].isoformat()}"
                            f"（块内 {r['block']} 行）")
        out_lines.append(SLIDE_HINT)
        out_lines.append("")
        if not r["items"]:
            out_lines.append("_无数据_")
        elif r["mode"] == "watch":
            out_lines.append(render_watch_table(r["items"]))
        else:
            out_lines.append(render_deal_table(r["items"]))
        out_lines.append("")

    if args.out:
        out_path = Path(args.out)
    else:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"深圳法拍资产日报-{target.isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"已生成：{out_path}")
    if warnings:
        for w in warnings:
            sys.stderr.write(f"[回退] {w}\n")
        sys.stderr.write("存在非当日数据块，日报已标注；请核对 xlsx 当日块后重跑\n")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
