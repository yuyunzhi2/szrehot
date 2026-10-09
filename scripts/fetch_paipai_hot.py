# -*- coding: utf-8 -*-
"""szrehot 法拍热点提取器：从法拍数据.xlsx 双 sheet 提取当日围观热度 TOP5。

数据源（固定路径）：D:\\Desktop\\法拍数据.xlsx
  - sheet「京东」：前6列 = 物业名称 / 法拍详情网址 / 当前起拍价 / 评估价 / 拍卖开始时间 / 围观人数（热度）
    提取日期位于**第2列**；该日期行以下的数据行为当日提取的数据
  - sheet「阿里」：前6列同上；提取日期位于**第1列**；该日期行以下的数据行为当日提取的数据
  - sheet「中拍」：不在本需求范围内，忽略

规则：
  - 每个 sheet 取**最后一个日期行**以下（到表尾）的连续数据行 = 当日提取数据
  - 过滤无物业名称 / 无围观人数的垃圾行（含日期块内偶发的残缺行）
  - 按围观人数降序取 TOP5（默认 5 条，--top 可调）
  - 拍卖开始时间审核（2026-08-19 新增，硬性门槛）：TOP5 的「拍卖开始时间」必须严格晚于
    技能执行当天日期（开始日期 > 执行日）；已开拍（开始日期 ≤ 执行日）或时间无法解析的
    物业剔除、不进入 TOP5 排序（--today 可指定审核基准日，默认执行当天）
  - 折价率 = 当前起拍价 / 评估价（自行计算，不读表格中的现成折价率列）
  - 建筑面积 = 打开法拍详情页提取（2026-08-19 新增）：
      * 京东：调公开 JSON 接口 api.m.jd.com queryProductDescription（无需登录），
        从标的物调查情况表 HTML 中解析「建筑面积」字段
      * 阿里：优先 requests 直连 sf-item.taobao.com 详情页解析「建筑面积」字段；
        被 x5sec 拦截/重定向登录时，改用 Playwright 打开真实浏览器窗口，
        **暂停等待用户手动验证**（--area-wait 秒，默认 60）：用户在弹出的窗口里
        扫码登录淘宝 → 过滑块 → 页面加载出详情后自动继续提取。多个 URL 复用
        同一浏览器会话（登录一次后续 URL 直接可读）
  - 单价 = 当前起拍价(元) / 建筑面积(㎡)，单位元/㎡（面积缺失时不计算，显示「-」）
  - --no-fetch 可关闭详情页抓取（面积/单价列显示「-」）

用法（工作目录 = 技能目录）:
  python -X utf8 scripts/fetch_paipai_hot.py [--xlsx D:\\Desktop\\法拍数据.xlsx] [--top 5] [--output 章节.md] [--no-fetch] [--area-wait 60]
  不传 --output 时章节 md 输出到 stdout（供 render_report.py 集成）
"""
import argparse
import datetime
import io
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:  # noqa: BLE001 — 无 requests 时降级为不抓面积
    requests = None

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DEFAULT_XLSX = r'D:\Desktop\法拍数据.xlsx'
# (sheet名, 日期所在列) — 列号从 1 起
SHEETS = [('京东', 2), ('阿里', 1)]


def _is_date_cell(v) -> bool:
    """识别日期单元格：datetime/date 或 '2026年08月03日'/'2026-08-03' 字符串。"""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return True
    if isinstance(v, str):
        s = v.strip()
        if s.startswith('202') and ('年' in s or '-' in s) and ('月' in s or '-' in s):
            return True
    return False


def _date_str(v) -> str:
    """日期单元格 → 显示字符串。"""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime('%Y年%m月%d日')
    return str(v).strip()


def parse_start_time(v, default_year: int | None = None,
                     after: datetime.date | None = None) -> datetime.date | None:
    """解析拍卖开始时间 → 日期（仅比较到日期精度）。

    支持 openpyxl 读出的 datetime/date 对象，以及字符串
    '2026年08月20日 10:00' / '2026-08-20 10:00' / '2026/8/20' 等形态。

    无年份格式（'09月05日 10:00'，京东/阿里法拍列表页默认省略当年年份）：
    用 default_year（提取日期所在年份）补全；解析出的日期早于 after（提取日期）
    时自动视为次年（12 月提取看到次年 1 月开拍标的的场景）。
    解析失败（时间未知/乱码）返回 None。
    """
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.date() if isinstance(v, datetime.datetime) else v
    if not isinstance(v, str):
        return None
    s = v.strip()
    m = re.search(r'(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?', s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:  # noqa: BLE001 — 非法日期（如 2月30日）
            return None
    # 无年份格式：'09月05日 10:00' → 继承 default_year（提取日期所在年份）
    if default_year is not None:
        m2 = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', s)
        if m2:
            try:
                d = datetime.date(default_year, int(m2.group(1)), int(m2.group(2)))
            except ValueError:  # noqa: BLE001 — 非法日期（如 2月30日）
                return None
            if after is not None and d < after:
                d = d.replace(year=d.year + 1)  # 早于提取日 → 跨年标的（次年开拍）
            return d
    return None


def parse_price_to_wan(v):
    """解析价格到万元数值。支持 '4960万' / '¥6200万' / '136万' / '2.930146359亿' / 数字(单位万)。

    返回 float 万元，解析失败或 None 返回 None。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('¥', '').replace('￥', '').replace(',', '')
    if not s:
        return None
    try:
        if s.endswith('亿'):
            return float(s[:-1]) * 10000
        if s.endswith('万'):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return None


def fmt_price_wan(x) -> str:
    """万元数值 → 展示字符串（纯数字，单位「万元」已入表头；≥1亿换算为万元，去尾零）。"""
    if x is None:
        return '-'
    s = f'{x:.2f}'.rstrip('0').rstrip('.')
    return f'{s}' if s else '0'


def fmt_discount(rate) -> str:
    """折价率（0.8 → '80'，纯数字，单位「%」已入表头）。"""
    if rate is None:
        return '-'
    return f'{rate * 100:.1f}'.rstrip('0').rstrip('.')


# ---------- 建筑面积提取（2026-08-19 新增） ----------

_JD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'Referer': 'https://paimai.jd.com/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
_TB_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
# 兼容「建筑面积 78.54㎡」「建筑面积为78.54平方米」「建筑面积约：98.2㎡」等连接写法
# Pre-extracted areas from bb-browser (Alibaba x5sec blocks automated extraction)
_TB_AREA_CACHE = {
    '1079720849040': 123.89,
    '1077885467988': 36.73,
    '1078738322646': 73.05,
    '1080709676857': 110.19,
    '1079825461920': 119.06,
    # 2026-09-04 阿里 TOP5（bb-browser/CDP 提取）
    '1078755027219': 88.33,
    '1079671822462': 233.17,
    '1080679997017': 55.28,
    '1080637681724': 22.06,
    '1079668510324': 87.43,
    # 2026-09-09 阿里（ShadowBot浏览器登录态 + 查档单附件PDF解析，见 scripts/parse_paipai_pdf_areas.py）
    '1083203804632': 111.82,  # 蔚蓝海岸二期25栋6C，查档单扫描图见证
    '1081181114988': 143.06,  # 吉祥里豪庭3栋5D，查档单文本
    '1082220257853': 18499.28,  # 铭德大厦A座401~1801十五套面积求和
    '1083148196035': 1373.45,  # 竹子林教育科技大厦塔楼04层，查档单文本
}


# ---------- 面积持久 store（2026-09-09 新增，治复跑反复开页） ----------
# 已确认面积落盘复用：启动时并入 _TB_AREA_CACHE；每次新提取成功写透。
# 命中缓存的条目不再打开详情页（京东 API 与阿里页面均跳过），复跑即缺件补取。
_PAIPAI_AREA_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_paipai_area_store.json')


def _load_area_store() -> None:
    try:
        data = json.load(open(_PAIPAI_AREA_STORE, encoding='utf-8'))
    except Exception:  # noqa: BLE001 — 首跑无文件
        return
    for k, v in (data or {}).items():
        try:
            _TB_AREA_CACHE.setdefault(str(k), float(v.get('area') if isinstance(v, dict) else v))
        except Exception:  # noqa: BLE001 — 脏数据跳过
            continue


def _store_area(item_id: str, area: float, provenance: str) -> None:
    try:
        data = json.load(open(_PAIPAI_AREA_STORE, encoding='utf-8'))
    except Exception:  # noqa: BLE001 — 首跑无文件
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[str(item_id)] = {
        'area': float(area),
        'provenance': provenance,
        'date': datetime.date.today().isoformat(),
    }
    try:
        _TB_AREA_CACHE[str(item_id)] = float(area)
    except Exception:  # noqa: BLE001
        pass
    json.dump(data, open(_PAIPAI_AREA_STORE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def _tb_item_id(url: str) -> str | None:
    m = re.search(r'/sf_item/(\d+)', url or '')
    return m.group(1) if m else None


_load_area_store()

# 面积前缀：建筑面积/测绘面积优先，兼顾裸「面积 X 平方米」写法
# （2026-10-01 实测：塘尾新村整栋私宅调查表/公告四处写「面积 1512.29平方米」，
# 从无建筑面积/测绘面积前缀，老正则全灭）。占地/使用/计租/投影/公摊/套内/分摊
# 等前缀用定长后顾排除（占地面积是土地面积，绝不可取）。
_AREA_RE = re.compile(r'(?<!占地)(?<!使用)(?<!计租)(?<!投影)(?<!公摊)(?<!套内)(?<!分摊)(?:建筑面积|测绘面积|面积)[^0-9，。,]{0,6}?([0-9]+(?:\.[0-9]+)?)\s*(?:平方米|㎡)')
# 多栋合计写法：产权证载明建筑面积 2栋A㎡、3栋B㎡.共C㎡（取合计 C）
_AREA_RE_TOTAL = re.compile(r'共\s*([0-9]+(?:\.[0-9]+)?)\s*(?:平方米|㎡)')


def _extract_area_text(text: str) -> float | None:
    """从清洗后文本提取建筑面积。优先标准「建筑面积 X 平方米/㎡」；
    多栋合计写法（产权证载明建筑面积 2栋A㎡、3栋B㎡.共C㎡）取合计 C。"""
    for m in _AREA_RE.finditer(text):
        # 口径 guard：「套内建筑面积」中的裸「面积」分支会绕过定长后顾，
        # 统一检查匹配点前 4 字是否含排除前缀（含套内/公摊/使用/占地等）
        if any(k in text[max(0, m.start() - 4):m.start()] for k in ('套内', '公摊', '使用', '占地', '计租', '投影', '分摊')):
            continue
        return float(m.group(1))
    if '建筑面积' not in text:
        return None
    m = _AREA_RE_TOTAL.search(text)
    return float(m.group(1)) if m else None


def extract_area_jd(url: str, timeout: int = 15):
    """京东法拍详情页 → 建筑面积（㎡）。

    先调公开接口 api.m.jd.com queryProductDescription 解析「建筑面积 X 平方米/㎡」；
    接口对该拍品返回空/无建筑面积时（实测部分拍品 API 空返回但页面有数据），
    回退用浏览器（优先 CDP 连接）打开详情页提取。均失败返回 None。
    """
    if requests is None:
        return None
    m = re.search(r'/(\d+)(?:\.htm)?(?:\?.*)?$', url.strip())
    if not m:
        return None
    paimai_id = m.group(1)
    if paimai_id in _TB_AREA_CACHE:  # store 命中直接返回，不再调接口/开页
        return _TB_AREA_CACHE[paimai_id]
    body = 'body=' + requests.utils.quote('{"paimaiId":"%s","source":5}' % paimai_id)
    try:
        r = requests.post(
            'https://api.m.jd.com/api?appid=paimai&functionId=queryProductDescription&loginType=3',
            headers=_JD_HEADERS, data=body, timeout=timeout)
        r.raise_for_status()
        html = (r.json() or {}).get('data', '') or ''
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        area = _extract_area_text(text)
        if area:
            _store_area(paimai_id, area, 'jd-api')
            return area
    except Exception:  # noqa: BLE001 — 接口失败走页面回退
        pass
    return extract_area_jd_page(url, timeout)


def extract_area_jd_page(url: str, timeout: int = 20):
    """京东回退通道：用浏览器（优先 CDP 连接用户浏览器）打开详情页提取面积。

    京东详情页无滑块反爬（实测 CDP 直接可读），API 空返回时页面仍有完整数据。
    """
    page = _pw_get_page(_JD_HEADERS['User-Agent'])
    if page is None:
        return None
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        time.sleep(3)  # 页面动态渲染
        body = page.evaluate('() => document.body ? document.body.innerText : ""')
        area = _extract_area_text(re.sub(r'\s+', ' ', body))
        if area:
            m2 = re.search(r'/(\d+)(?:\.htm)?(?:\?.*)?$', url.strip())
            _store_area(m2.group(1) if m2 else url, area, 'jd-page')
            print(f'[paipai] ✅ 京东详情页提取到建筑面积 {area}㎡', file=sys.stderr)
        return area
    except Exception:  # noqa: BLE001 — 页面打开失败降级为 None
        return None


def extract_area_tb(url: str, timeout: int = 15):
    """阿里法拍详情页 → 建筑面积（㎡）。

    先 requests 直连解析；若被 x5sec 拦截（登录跳转/滑块页，特征：页面含
    x5referer/x5secdata/登录/验证码 且无「建筑面积」关键词），交给
    extract_area_tb_playwright 用真实浏览器打开并暂停等用户手动验证。
    """
    if requests is None:
        return None
    # Check pre-extracted cache first
    _m = re.search(r"/sf_item/(\d+)", url)
    if _m and _m.group(1) in _TB_AREA_CACHE:
        return _TB_AREA_CACHE[_m.group(1)]
    try:
        r = requests.get(url, headers=_TB_HEADERS, timeout=timeout)
        r.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text)
        m = _AREA_RE.search(text)
        if m:
            area = float(m.group(1))
            if _m:
                _store_area(_m.group(1), area, 'tb-html')
            return area
        # 反爬拦截特征（x5sec 滑块页 / 登录跳转页）：含特征关键词且无面积 → 转浏览器人工验证
        blocked = any(k in text for k in ('x5referer', 'x5secdata', '验证码', '滑块'))
        if blocked or 'login' in r.url.lower() or 'havanaone' in r.url.lower():
            return extract_area_tb_playwright(url, _TB_HEADERS['User-Agent'])
        return None
    except Exception:  # noqa: BLE001 — 反爬/网络/解析失败时转浏览器人工验证
        return extract_area_tb_playwright(url, _TB_HEADERS['User-Agent'])


# Playwright 浏览器实例缓存（模块级，跨 URL 复用会话；登录一次后续 URL 直接可读）
_PW = {'browser': None, 'context': None, 'page': None, 'wait_seconds': 60,
       'wait_attempted': False,  # wait_attempted：本次运行是否已做过一次完整人工等待
       'login_waited': False}  # login_waited：本次运行是否已为登录/滑块做过人工等待

# 真实 Chrome 持久化 profile 目录（登录态/验证状态跨运行保留，登录一次后续免验证）
_PW_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.pw_profile')

# 反自动化标记（隐藏 webdriver 等指纹，避免 x5sec 判定为自动化风险而弹滑块）
_PW_STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


def _pw_get_page(user_agent: str):
    """获取（或创建）浏览器页面。返回 None 表示浏览器不可用。

    连接策略（2026-08-19 重构）：
      1. 优先 CDP 连接用户**主浏览器**（127.0.0.1:9222，经 start_chrome_debug.bat
         以调试端口启动）——复用主浏览器的淘宝登录态与真实指纹，x5sec 不会弹滑块，
         直接读取详情页面积（实测：无登录态的新窗口即使隐藏 webdriver 仍会被软拒绝）
      2. 回退：真实 Chrome（channel='chrome'）+ 隐藏 webdriver + 持久化 profile
         （.pw_profile 目录，登录一次后续运行复用）
      3. 无真实 Chrome 时回退内置 Chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # noqa: BLE001 — 未装 playwright 时无法人工验证
        return None
    if _PW['page'] is not None:
        return _PW['page']
    try:
        _PW['pw'] = sync_playwright().start()

        # 1) 优先连接主浏览器（CDP，带登录态）
        try:
            browser = _PW['pw'].chromium.connect_over_cdp('http://127.0.0.1:9222')
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            _PW['browser'], _PW['context'], _PW['page'] = browser, ctx, page
            print('[paipai] 🌐 已连接主浏览器（CDP），复用登录态读取法拍详情页', file=sys.stderr)
            return page
        except Exception:  # noqa: BLE001 — 主浏览器未以调试端口启动时走回退
            pass

        # 2) 回退：真实 Chrome + stealth + 持久化 profile
        launch_kwargs = dict(headless=False, locale='zh-CN',
                             args=['--disable-blink-features=AutomationControlled'])
        try:
            ctx = _PW['pw'].chromium.launch_persistent_context(
                _PW_PROFILE, channel='chrome', **launch_kwargs)
        except Exception:  # noqa: BLE001 — 无真实 Chrome 时回退内置 Chromium
            ctx = _PW['pw'].chromium.launch_persistent_context(
                _PW_PROFILE, **launch_kwargs)
        ctx.add_init_script(_PW_STEALTH)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _PW['browser'], _PW['context'], _PW['page'] = None, ctx, page
        return page
    except Exception as e:  # noqa: BLE001 — 浏览器启动失败降级
        print(f'[paipai] ⚠️ 浏览器不可用，无法人工验证阿里详情页：{e}', file=sys.stderr)
        return None


def _pw_read_area(page) -> float | None:
    """从当前页面 innerText 解析「建筑面积」。返回面积或 None。

    2026-09-25 实测：阿里详情页「标的物介绍」为滚动懒加载，首屏快照无面积
    （信义荔山御园 1086664377114：首屏 2380 字无面积，滚到底 9866 字才出现
    「建筑面积 89.48平方米」）。读取前先分步滚到底触发加载再读快照。
    """
    try:
        for _ in range(8):
            page.evaluate('() => window.scrollBy(0, 1500)')
            time.sleep(0.4)
        body = page.evaluate('() => document.body ? document.body.innerText : ""')
    except Exception:  # noqa: BLE001 — 页面跳转中
        return None
    return _extract_area_text(re.sub(r'\s+', ' ', body))


def _pw_page_state(page) -> str:
    """判断当前页面状态：login（登录页）/ slider（滑块验证）/ detail（详情）/ other。"""
    try:
        url = page.url or ''
    except Exception:  # noqa: BLE001
        url = ''
    try:
        body = page.evaluate('() => document.body ? document.body.innerText : ""')
    except Exception:  # noqa: BLE001
        body = ''
    low_url = url.lower()
    if 'login' in low_url or 'havanaone' in low_url or '扫码登录' in body or '密码登录' in body:
        return 'login'
    if '验证码' in body or '滑块' in body or 'punish' in low_url:
        return 'slider'
    if '建筑面积' in body:
        return 'detail'
    return 'other'


# ---------- 附件PDF兜底（2026-09-09 新增） ----------
# 部分阿里标的（如查档单在附件、整层多单元）的面积不在页面正文，
# 而在「相关附件下载」PDF（查档单/不动产信息单）中。正文无面积时自动下载解析。
_ATTACH_KEYWORDS = ('查档单', '不动产信息', '不动产登记', '房产证', '产权证', '评估报告', '测绘')
_ATTACH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs', 'paipai_attach')


def _parse_attach_pdf(path: str) -> float | None:
    """解析查档单类 PDF：单单元取「单独所有 X 平方米」；多单元求和（如整层拍卖）。"""
    try:
        import pdfplumber
    except ImportError:  # noqa: BLE001 — 无 pdfplumber 时降级留空
        return None
    try:
        with pdfplumber.open(path) as pdf:
            full = '\n'.join([(pg.extract_text() or '') for pg in pdf.pages])
    except Exception:  # noqa: BLE001 — 扫描图无文本层/损坏时降级留空
        return None
    vals = re.findall(r'单独所有\s*([0-9]+(?:\.[0-9]+)?)\s*平方米', full)
    if len(vals) == 1:
        return float(vals[0])
    if len(vals) > 1:
        return round(sum(float(v) for v in vals), 2)
    m = _AREA_RE.search(re.sub(r'\s+', ' ', full))
    if m:
        return float(m.group(1))
    # 测绘报告口径（2026-10-01 实测：塘尾新村整栋私宅测绘报告）：面积统计表
    # 同一面积值重复出现（「塘尾新村 1512.29 1512.29」），首个重复值即面积合计
    m = re.search(r'(\d+\.\d+)\s+\1', full)
    return float(m.group(1)) if m else None


def _pw_try_attach_pdf(page, url: str) -> float | None:
    """附件PDF兜底：下载详情页「相关附件」查档单并解析面积。返回面积或 None。"""
    item = _tb_item_id(url) or 'unknown'
    try:
        links = page.locator("a[href*='download_attach']").all()
    except Exception:  # noqa: BLE001 — 页面已跳转
        return None
    target, tname = None, ''
    for l in links:
        try:
            t = (l.inner_text() or '').strip()
        except Exception:  # noqa: BLE001 — 元素已失效
            continue
        if any(k in t for k in _ATTACH_KEYWORDS):
            target, tname = l, t
            break
    if target is None:
        return None
    try:
        os.makedirs(_ATTACH_DIR, exist_ok=True)
        dest = os.path.join(_ATTACH_DIR, f'{item}.pdf')
        with page.expect_download(timeout=20000) as dl_info:
            target.click()
        dl_info.value.save_as(dest)
    except Exception as e:  # noqa: BLE001 — 下载失败降级留空
        print(f'[paipai] ⚠️ 附件PDF下载失败：{e}', file=sys.stderr)
        return None
    area = _parse_attach_pdf(dest)
    if area:
        _store_area(item, area, f'pdf-attach:{tname[:40]}')
        print(f'[paipai] ✅ 附件PDF提取到建筑面积 {area}㎡（{tname[:30]}）', file=sys.stderr)
    else:
        print('[paipai] ⏰ 附件PDF未解析到面积（如扫描图无文本层），该条面积留空。', file=sys.stderr)
    return area


def _pw_manual_wait(page, url: str, wait_s: int, state: str) -> float | None:
    """登录/滑块页人工等待：打印指引并轮询面积至多 wait_s 秒。返回面积或 None。"""
    if state == 'login':
        print(f'[paipai] 🔓 阿里需登录淘宝，已打开浏览器窗口等待最多 {wait_s} 秒。', file=sys.stderr)
        print(f'[paipai]    请在弹出窗口中扫码登录淘宝（手机淘宝 App 扫一扫）；登录后自动跳回详情页。', file=sys.stderr)
        print(f'[paipai]    登录态会保存，下次运行无需重复登录。详情页：{url}', file=sys.stderr)
    elif state == 'slider':
        print(f'[paipai] 🔒 阿里详情页触发滑块验证，已打开浏览器窗口等待最多 {wait_s} 秒。', file=sys.stderr)
        print(f'[paipai]    请在弹出窗口中拖动滑块完成验证；验证通过后自动继续。', file=sys.stderr)
        print(f'[paipai]    详情页：{url}', file=sys.stderr)
    else:
        print(f'[paipai] ⏳ 阿里详情页加载中，已打开浏览器窗口等待最多 {wait_s} 秒。', file=sys.stderr)
        print(f'[paipai]    详情页：{url}', file=sys.stderr)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(2)
        area = _pw_read_area(page)
        if area:
            print(f'[paipai] ✅ 人工验证完成，提取到建筑面积 {area}㎡', file=sys.stderr)
            _store_area(_tb_item_id(url) or url, area, 'tb-browser')
            return area
    print(f'[paipai] ⏰ {wait_s} 秒内未完成人工验证，该条面积留空。', file=sys.stderr)
    return None


def extract_area_tb_playwright(url: str, user_agent: str, timeout: int = 30):
    """用真实浏览器打开阿里详情页，暂停等待用户手动验证（登录/滑块），再提取面积。

    弹出真实 Chrome 窗口后按页面状态打印指引：
      - 登录页 → 提示扫码登录淘宝（真实 Chrome 指纹 + 隐藏 webdriver 后一般直接走登录页，
        不再触发滑块；登录态写入持久化 profile，后续运行免登录）
      - 滑块页 → 提示拖动滑块
    轮询检测「建筑面积」出现（验证完成后自动继续），超时返回 None。
    浏览器会话跨 URL 复用，后续 URL 无需重复验证。

    每次运行只做**一次**完整人工等待：用户已在第一次弹窗时完成验证 → 后续 URL
    直接读；第一次超时（用户不在场）→ 后续 URL 只快速检查一次，不再重复等待。
    """
    page = _pw_get_page(user_agent)
    if page is None:
        return None
    wait_s = _PW['wait_seconds']
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
    except Exception:  # noqa: BLE001 — 超时/跳转异常继续，页面可能已加载
        pass
    # 本次运行已做过一次完整人工等待：短轮询快速检查。
    # 2026-08-24 教训：此前此处仅 sleep(2) 做单次快照读取——阿里详情页标的
    # 调查表为异步渲染，冷启动时前几条在 2 秒窗口内读不到「建筑面积」字段，
    # 导致当日 TOP5 前三条面积全部留空（同榜后两条热身后成功）。
    # 改为两轮短轮询：每轮最多 8 秒（每 2 秒读一次），首轮失败重新 goto 后再试一轮。
    if _PW['wait_attempted']:
        for attempt in range(2):
            deadline = time.time() + 8
            while time.time() < deadline:
                time.sleep(2)
                area = _pw_read_area(page)
                if area:
                    print(f'[paipai] ✅ 提取到建筑面积 {area}㎡（已登录会话）', file=sys.stderr)
                    _store_area(_tb_item_id(url) or url, area, 'tb-browser')
                    return area
            if attempt == 0:
                try:
                    page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                except Exception:  # noqa: BLE001 — 重导航异常继续读当前页
                    pass
        print('[paipai] ⏰ 快速轮询仍未提取到建筑面积。', file=sys.stderr)
        # 登录 redirect 常发生在 domcontentloaded 之后：复核一次真实状态，
        # 若已跳登录/滑块且本轮尚未做过人工等待，走一次人工等待而非直接兜底
        # （2026-09-30 实测：x5sec 风控下连击会被误判为附件型）。
        state2 = _pw_page_state(page)
        if state2 in ('login', 'slider') and not _PW.get('login_waited'):
            _PW['login_waited'] = True
            area = _pw_manual_wait(page, url, wait_s, state2)
            if area:
                return area
        print('[paipai] 转附件PDF兜底。', file=sys.stderr)
        return _pw_try_attach_pdf(page, url)
    # 首次拦截：先看页面状态——已加载但无面积文本（附件型标的）直接走附件PDF兜底，
    # 不做 60 秒人工等待；仅 login/slider 才完整等待用户手动验证（2026-09-09 优化）。
    _PW['wait_attempted'] = True
    state = _pw_page_state(page)
    if state not in ('login', 'slider'):
        deadline = time.time() + 10
        while time.time() < deadline:
            time.sleep(2)
            area = _pw_read_area(page)
            if area:
                print(f'[paipai] ✅ 提取到建筑面积 {area}㎡（页面直读）', file=sys.stderr)
                _store_area(_tb_item_id(url) or url, area, 'tb-browser')
                return area
        # 10 秒轮询仍无面积：复核状态（登录 redirect 可能后发先至），
        # 若已跳登录/滑块则转人工等待，否则走附件PDF兜底。
        state = _pw_page_state(page)
        if state not in ('login', 'slider'):
            return _pw_try_attach_pdf(page, url)
    _PW['login_waited'] = True
    area = _pw_manual_wait(page, url, wait_s, state if state in ('login', 'slider') else 'other')
    if area:
        return area
    return None


def extract_area(url: str) -> float | None:
    """按 URL 域名分发到对应平台的面积提取器。

    当次运行内失败免重抓（2026-09-30 新增）：同一 URL 在本次运行中已走完完整链路
    仍未提取到面积时直接返回 None，不重复开页。输出语义不变（仍为 '-'），仅省掉
    TOP5 与 2000 万块之间对同一 URL 的重复抓取。
    """
    u = (url or '').strip().lower()
    if u in _AREA_MISS:
        return None
    if 'jd.com' in u:
        area = extract_area_jd(url)
    elif 'taobao.com' in u or 'tmall.com' in u:
        area = extract_area_tb(url)
    else:
        return None
    if area is None:
        _AREA_MISS.add(u)
    return area


# 当次运行内已走完链路仍无面积的 URL（免重复开页；进程结束即失效，不落盘）
_AREA_MISS: set = set()


def _load_miss_memo() -> None:
    """载入当次执行日已验证无面积的 URL 备忘（outputs/_area_miss_*.json，run-scoped）。

    备忘仅收录本执行日内已走完完整链路（页面直读→附件PDF）仍无面积的 URL，
    输出语义与重抓完全一致（均为 '-'），只为省掉 render 内 TOP5/2000万块的重复开页。
    面积一律来自脚本链路或面积库，无手填。
    """
    import glob as _glob
    try:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'outputs')
        for path in sorted(_glob.glob(os.path.join(base, '_area_miss_*.json'))):
            try:
                data = json.load(open(path, encoding='utf-8'))
                urls = data if isinstance(data, list) else data.get('urls', [])
                for u in urls:
                    if u:
                        _AREA_MISS.add(str(u).strip().lower())
            except Exception:  # noqa: BLE001 — 单个备忘损坏不阻塞
                continue
    except Exception:  # noqa: BLE001
        pass


_load_miss_memo()


def fmt_area(area: float | None) -> str:
    """建筑面积 → 展示字符串（纯数字，单位「㎡」已入表头；None → '-'）。"""
    if area is None:
        return '-'
    return f'{area:.2f}'.rstrip('0').rstrip('.')


def fmt_unit_price(start_wan: float | None, area: float | None) -> str:
    """单价 = 当前起拍价(元) / 建筑面积(㎡)，纯数字（单位「元/㎡」已入表头，四舍五入到元）。"""
    if start_wan is None or area is None or area <= 0:
        return '-'
    return f'{round(start_wan * 10000 / area):,}'


# 物业名称精简（2026-08-26 新增）：只保留位置信息
_NAME_TAIL_RE = re.compile(r'【[^】]*(?:不动产权证号|产权证)[^】]*】')          # 尾部权证号说明
_NAME_PREFIX_RE = re.compile(r'^(?:关于)?(?:(?:司法)?拍卖|变卖|竞价)+[::]?\s*')  # 动词前缀
_NAME_REGION_RE = re.compile(r'^(?:广东省)?(?:深圳市?)?\s*')                     # 行政区划前缀


def _clean_name(name) -> str:
    """物业名称精简：去尾部「【不动产权证号：…】」、去「关于/拍卖/变卖」动词前缀、
    去「广东省/深圳市」行政区划前缀。清洗后过短（<4字）则回退原名防误伤。"""
    s = str(name or '').strip()
    t = _NAME_TAIL_RE.sub('', s)
    t = _NAME_PREFIX_RE.sub('', t).strip()
    t = _NAME_REGION_RE.sub('', t).strip(' ，,、；;')
    return t if len(t) >= 4 else s


# ---------- 2000万物业归一与汇总（2026-08-31 新增，2026-09-02 起阈值由1亿调整为2000万） ----------
YI_THRESHOLD_WAN = 2000  # 2000万 = 2000万元（2026-09-02 起由1亿调整）
_YI_BASE_RE = re.compile(
    r"(\d+\s*[栋座幢号楼单元层房室铺号]|"  # 15栋 / 3号楼 / 2单元 / 15层 / 1501房/室
    r"[A-Z]\s*[栋座]|"                      # A栋 / B座
    r"[A-Z]\d+\s*[房室]?|"                  # A15房 / B1501室
    r"之\s*\d+|"                            # 之18
    r"\d+[A-Z]?\s*房|\d+[A-Z]?\s*室)"                   # 1501房 / 15A房
)


def _base_address(name: str) -> str:
    """归一基准地址：用于同址多宗合计判定。"""
    s = _clean_name(name)
    s = re.sub(r"的房产\s*$", "", s).strip()
    s = re.sub(r"房产\s*$", "", s).strip()
    m = _YI_BASE_RE.search(s)
    if m:
        base = s[:m.start()].strip(" \uFF0C,\u3001\uFF1B;")
        if base and base[-1].isalpha() and base[-1].isupper():
            if re.search(r"^[A-Z]\d+\s*[房室]?", s[m.start():]):
                base = base[:-1].strip()
        if len(base) >= 4:
            return base
    m2 = re.search(r"\s+\d+\s*$", s)
    if m2:
        base = s[:m2.start()].strip()
        if len(base) >= 4:
            return base
    return s


def _group_yi_items(items: list) -> list:
    groups: dict[str, list] = {}
    for name, url, start_p, eval_p, _st, _v, start_date in items:
        if eval_p is None:
            continue
        base = _base_address(name)
        groups.setdefault(base, []).append((name, url, start_p, eval_p, start_date))
    result = []
    for base, members in groups.items():
        total = sum(m[3] for m in members)
        if total >= YI_THRESHOLD_WAN:
            max_eval = max(m[3] for m in members)
            members_sorted = sorted(members, key=lambda x: x[3], reverse=True)
            result.append({
                "base": base,
                "count": len(members),
                "total_eval": total,
                "max_eval": max_eval,
                "members": members_sorted,
            })
    result.sort(key=lambda g: g["total_eval"], reverse=True)
    return result


def _render_yi_block(groups: list, sheet_name: str, fetch_area: bool = True) -> list[str]:
    """评估价超2000万物业汇总块。每个同址分组以单行汇总呈现，含聚合计算：
    起拍价合计/评估价合计/整体折价率（起拍合计÷评估合计）/建筑面积合计/整体单价（起拍合计÷面积合计）；
    拍卖开始时间：组内全部同日期 → 单值；跨日期 → 日期区间（最早-最晚）。
    面积缺失（未提取）时合计列显示 '-'；起拍价缺失的成员不计入起拍合计。
    """
    if not groups:
        return []
    lines: list[str] = []
    lines.append(f"#### {sheet_name}·评估价超2000万物业（单宗≥2000万 / 同址合计≥2000万）")
    lines.append("")
    lines.append(f"> 阈值 2000万元（{YI_THRESHOLD_WAN}万元）；归一规则：截断首个栋/座/房/室标识及其后房号，同一基准地址多宗合计≥阈值即入选；合计≥2000万的多宗分组以汇总行呈现，并汇总起拍/评估合计、整体折价率与面积单价；组内拍卖开始日期不同则以日期区间展示。共 {len(groups)} 组。")
    lines.append("")
    lines.append("| 基准地址 | 宗数 | 起拍价合计(万元) | 评估价合计(万元) | 折价率(%) | 建筑面积合计(㎡) | 单价(元/㎡) | 拍卖开始时间 | 涉及物业明细 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for g in groups:
        details = []
        start_total = 0.0
        eval_total = 0.0
        area_total = 0.0
        n_area = 0
        dates = []
        for member in g["members"]:
            name, url, start_p, eval_p, start_date = member
            clean = _clean_name(name)
            base = g["base"]
            suffix = clean[len(base):].strip(" \uFF0C,\u3001") if clean.startswith(base) else clean
            suffix = re.sub(r"的房产\s*$", "", suffix).strip()
            suffix = re.sub(r"房产\s*$", "", suffix).strip()
            if not suffix:
                suffix = clean
            disp = f"[{suffix}]({url})" if url else suffix
            eval_s = fmt_price_wan(eval_p)
            start_s = fmt_price_wan(start_p) if start_p is not None else "-"
            details.append(f"{disp} {eval_s}万(起拍{start_s}万)")
            if eval_p is not None:
                eval_total += eval_p
            if start_p is not None:
                start_total += start_p
            if fetch_area and url:
                area = extract_area(url)
                if area:
                    area_total += area
                    n_area += 1
            if start_date is not None:
                dates.append(start_date)
        detail_cell = "<br>".join(details)
        if dates:
            lo, hi = min(dates), max(dates)
            lo_s = f"{lo.month:02d}月{lo.day:02d}日"
            hi_s = f"{hi.month:02d}月{hi.day:02d}日"
            time_cell = lo_s if lo == hi else f"{lo_s}-{hi_s}"
        else:
            time_cell = "-"
        discount = fmt_discount(start_total / eval_total) if eval_total else "-"
        area_cell = fmt_area(area_total) if n_area else "-"
        unit = fmt_unit_price(start_total, area_total) if n_area else "-"
        lines.append(
            f"| {g['base']} | {g['count']} | {fmt_price_wan(start_total)} | {fmt_price_wan(eval_total)} | "
            f"{discount} | {area_cell} | {unit} | {time_cell} | {detail_cell} |"
        )
    lines.append("")
    return lines

def _fmt_start_time(start_time: str | None) -> str:
    """拍卖开始时间仅保留日期部分（'09月28日 10:00' → '09月28日'），小时不入表。"""
    if not start_time:
        return start_time or '-'
    return re.sub(r'\s*\d{1,2}:\d{2}(?::\d{2})?$', '', str(start_time).strip()) or str(start_time)


def _parse_block(ws, date_col: int):
    """返回 (提取日期字符串, 数据行列表[(名称, url, 起拍价wan, 评估价wan, 开始时间, 围观人数, 开始日期)]).

    取最后一个日期行以下直到表尾的连续数据行；跳过全空行与残缺行。
    开始日期 = parse_start_time(开始时间)，时间无法解析时为 None。
    """
    last_date_row = None
    last_date = None
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=date_col).value
        if _is_date_cell(v):
            last_date_row = r
            last_date = _date_str(v)

    if last_date_row is None:
        return None, []

    # 提取日期（数据块标题行）→ 用于无年份拍卖开始时间补全年份 / 跨年判断
    extract_date = parse_start_time(last_date)
    default_year = extract_date.year if extract_date else None

    items = []
    for r in range(last_date_row + 1, ws.max_row + 1):
        name = ws.cell(row=r, column=1).value
        url = ws.cell(row=r, column=2).value
        start_price = ws.cell(row=r, column=3).value
        eval_price = ws.cell(row=r, column=4).value
        start_time = ws.cell(row=r, column=5).value
        views = ws.cell(row=r, column=6).value
        if not name or views is None:
            continue  # 空行 / 残缺行（如京东 3562 行只有网址列）
        try:
            views_i = int(views)
        except (TypeError, ValueError):
            continue
        items.append((str(name).strip(), str(url).strip() if url else '',
                      parse_price_to_wan(start_price), parse_price_to_wan(eval_price),
                      str(start_time).strip() if start_time else '', views_i,
                      parse_start_time(start_time, default_year=default_year, after=extract_date)))
    return last_date, items


def render_section(xlsx_path: str, top: int, fetch_area: bool = True,
                   today: datetime.date | None = None) -> str:
    """渲染「今日法拍资产热点」章节 md。xlsx 缺失/无日期行时返回说明文字（不抛错）。

    拍卖开始时间审核（2026-08-19 新增，硬性门槛）：仅保留拍卖开始时间**严格晚于执行当天**
    的物业进入 TOP5——开始日期 ≤ 当天（已开拍/已结束）或时间无法解析者一律剔除，
    在说明行中计数「剔除 X 条」。无符合条件条目时输出说明文字，不降级放宽。

    fetch_area=True 时打开 TOP5 详情页提取建筑面积并计算单价（京东走 API，阿里直连，
    被反爬拦截的面积/单价显示 '-'）；False 时两列均为 '-'（--no-fetch）。
    """
    if today is None:
        today = datetime.date.today()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    except Exception as e:  # noqa: BLE001 — 数据源缺失时降级为说明文字
        return f'## 今日法拍资产热点\n\n> 数据源暂不可用（{xlsx_path} 读取失败：{e}）。今日无法拍资产热点数据。\n'

    lines = ['## 今日法拍资产热点', '']
    any_data = False
    for sheet_name, date_col in SHEETS:
        if sheet_name not in wb.sheetnames:
            lines.append(f'### {sheet_name}法拍\n\n数据源缺少 sheet「{sheet_name}」。\n')
            continue
        ws = wb[sheet_name]
        extract_date, items = _parse_block(ws, date_col)
        if not items:
            lines.append(f'### {sheet_name}法拍\n\n今日无提取数据。\n')
            continue
        any_data = True
        # 拍卖开始时间审核（硬性门槛）：仅保留开始日期严格晚于执行当天（today）的物业
        future_items = [it for it in items if it[6] is not None and it[6] > today]
        excluded = len(items) - len(future_items)
        if not future_items:
            lines.append(f'### {sheet_name}法拍\n\n当日 {len(items)} 条物业的拍卖开始时间均不晚于'
                         f'执行当天（{today}）或时间无法解析，无符合「拍卖开始时间晚于执行日」的标的。\n')
            continue
        future_items.sort(key=lambda it: it[5], reverse=True)
        top_items = future_items[:top]
        lines.append(f'### {sheet_name}法拍（围观 TOP{top}）')
        lines.append('')
        lines.append(f'> 提取日期：{extract_date}；当日共 {len(items)} 条，剔除已开拍/时间无法解析 '
                     f'{excluded} 条，按围观人数取前 {top} 条（拍卖开始时间均晚于执行当天 {today}）。')
        lines.append('')
        lines.append('| 物业名称 | 当前起拍价(万元) | 评估价(万元) | 折价率(%) | 建筑面积(㎡) | 单价(元/㎡) | 拍卖开始时间 |')
        lines.append('|---|---|---|---|---|---|---|')
        for name, url, start_p, eval_p, start_time, _v, _sd in top_items:
            name_cell = f'[{_clean_name(name)}]({url})' if url else _clean_name(name)
            discount = None
            if start_p is not None and eval_p:
                discount = start_p / eval_p
            area = extract_area(url) if fetch_area and url else None
            lines.append(f'| {name_cell} | {fmt_price_wan(start_p)} | {fmt_price_wan(eval_p)} | '
                         f'{fmt_discount(discount)} | {fmt_area(area)} | {fmt_unit_price(start_p, area)} | {_fmt_start_time(start_time)} |')
        lines.append('')
        # 2000万物业汇总（基于当日块全部有效行，不过滤拍卖时间；仅剔除评估价无法解析；合计≥2000万必须以汇总行呈现）
        yi_groups = _group_yi_items(items)
        yi_lines = _render_yi_block(yi_groups, sheet_name, fetch_area=fetch_area)
        if yi_lines:
            lines.extend(yi_lines)
        else:
            eval_valid = sum(1 for _n,_u,_sp,ep,_st,_v,_sd in items if ep is not None)
            if eval_valid:
                lines.append(f'> {sheet_name}当日 {eval_valid} 宗评估价可解析物业中，无单宗或同址合计评估价≥2000万元的标的。')
                lines.append('')
    if not any_data:
        lines.append('今日无法拍资产热点数据。')
        lines.append('')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', default=DEFAULT_XLSX)
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--output')
    ap.add_argument('--no-fetch', action='store_true',
                    help='不打开详情页抓取建筑面积（面积/单价列显示 -）')
    ap.add_argument('--area-wait', type=int, default=60,
                    help='阿里详情页需人工验证（登录/滑块）时，弹出浏览器窗口等待的秒数（默认 60）')
    ap.add_argument('--today',
                    help='审核基准日 YYYY-MM-DD（默认执行当天；测试用可传未来日期）')
    a = ap.parse_args()

    _PW['wait_seconds'] = max(10, a.area_wait)
    today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    md = render_section(a.xlsx, a.top, fetch_area=not a.no_fetch, today=today)
    # 清理 Playwright 浏览器会话（有则关）
    # 清理浏览器会话：CDP 连接的主浏览器只关自己开的标签页，不关闭用户浏览器；
    # 自启的 persistent context 关闭时自动保存 profile（登录态跨运行保留）
    if _PW['page'] is not None:
        try:
            _PW['page'].close()
        except Exception:  # noqa: BLE001
            pass
    if _PW['context'] is not None and _PW['browser'] is None:
        try:
            _PW['context'].close()
        except Exception:  # noqa: BLE001
            pass
    if _PW['browser'] is not None:  # CDP 连接的主浏览器：仅断开，不关闭
        try:
            _PW['browser'].close()
        except Exception:  # noqa: BLE001
            pass
    if a.output:
        os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
        with open(a.output, 'w', encoding='utf-8', newline='\n') as f:
            f.write(md)
        print(f'written: {a.output}')
    else:
        print(md)


if __name__ == '__main__':
    main()
