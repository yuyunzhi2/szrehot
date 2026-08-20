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
    """万元数值 → 展示字符串（≥1亿显示亿，否则万，去尾零）。"""
    if x is None:
        return '-'
    if abs(x) >= 10000:
        s = f'{x / 10000:.2f}'.rstrip('0').rstrip('.')
        return f'{s}亿'
    s = f'{x:.2f}'.rstrip('0').rstrip('.')
    return f'{s}万'


def fmt_discount(rate) -> str:
    """折价率（0.8 → '80%'）。"""
    if rate is None:
        return '-'
    s = f'{rate * 100:.1f}'.rstrip('0').rstrip('.')
    return f'{s}%'


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
_AREA_RE = re.compile(r'建筑面积\s*([0-9]+(?:\.[0-9]+)?)\s*(?:平方米|㎡)')
# 多栋合计写法：产权证载明建筑面积 2栋A㎡、3栋B㎡.共C㎡（取合计 C）
_AREA_RE_TOTAL = re.compile(r'共\s*([0-9]+(?:\.[0-9]+)?)\s*(?:平方米|㎡)')


def _extract_area_text(text: str) -> float | None:
    """从清洗后文本提取建筑面积。优先标准「建筑面积 X 平方米/㎡」；
    多栋合计写法（产权证载明建筑面积 2栋A㎡、3栋B㎡.共C㎡）取合计 C。"""
    m = _AREA_RE.search(text)
    if m:
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
    try:
        r = requests.get(url, headers=_TB_HEADERS, timeout=timeout)
        r.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', r.text)
        text = re.sub(r'\s+', ' ', text)
        m = _AREA_RE.search(text)
        if m:
            return float(m.group(1))
        # 反爬拦截特征（x5sec 滑块页 / 登录跳转页）：含特征关键词且无面积 → 转浏览器人工验证
        blocked = any(k in text for k in ('x5referer', 'x5secdata', '验证码', '滑块'))
        if blocked or 'login' in r.url.lower() or 'havanaone' in r.url.lower():
            return extract_area_tb_playwright(url, _TB_HEADERS['User-Agent'])
        return None
    except Exception:  # noqa: BLE001 — 反爬/网络/解析失败时转浏览器人工验证
        return extract_area_tb_playwright(url, _TB_HEADERS['User-Agent'])


# Playwright 浏览器实例缓存（模块级，跨 URL 复用会话；登录一次后续 URL 直接可读）
_PW = {'browser': None, 'context': None, 'page': None, 'wait_seconds': 60,
       'wait_attempted': False}  # wait_attempted：本次运行是否已做过一次完整人工等待

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
    """从当前页面 innerText 解析「建筑面积」。返回面积或 None。"""
    try:
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
    # 本次运行已做过一次完整人工等待：只快速检查一次（若用户之前登录成功则直接命中）
    if _PW['wait_attempted']:
        time.sleep(2)
        area = _pw_read_area(page)
        if area:
            print(f'[paipai] ✅ 提取到建筑面积 {area}㎡（已登录会话）', file=sys.stderr)
        return area
    # 首次拦截：完整等待用户手动验证
    _PW['wait_attempted'] = True
    state = _pw_page_state(page)
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
            return area
    print(f'[paipai] ⏰ {wait_s} 秒内未完成人工验证，该条面积留空。', file=sys.stderr)
    return None


def extract_area(url: str) -> float | None:
    """按 URL 域名分发到对应平台的面积提取器。"""
    u = (url or '').strip().lower()
    if 'jd.com' in u:
        return extract_area_jd(url)
    if 'taobao.com' in u or 'tmall.com' in u:
        return extract_area_tb(url)
    return None


def fmt_area(area: float | None) -> str:
    """建筑面积 → 展示字符串（86.79㎡；None → '-'）。"""
    if area is None:
        return '-'
    return f'{area:.2f}'.rstrip('0').rstrip('.') + '㎡'


def fmt_unit_price(start_wan: float | None, area: float | None) -> str:
    """单价 = 当前起拍价(元) / 建筑面积(㎡)，单位元/㎡（四舍五入到元）。"""
    if start_wan is None or area is None or area <= 0:
        return '-'
    price = start_wan * 10000 / area
    return f'{round(price):,}元/㎡'


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
        lines.append('| 物业名称 | 当前起拍价 | 评估价 | 折价率 | 建筑面积 | 单价 | 拍卖开始时间 |')
        lines.append('|---|---|---|---|---|---|---|')
        for name, url, start_p, eval_p, start_time, _v, _sd in top_items:
            name_cell = f'[{name}]({url})' if url else name
            discount = None
            if start_p is not None and eval_p:
                discount = start_p / eval_p
            area = extract_area(url) if fetch_area and url else None
            lines.append(f'| {name_cell} | {fmt_price_wan(start_p)} | {fmt_price_wan(eval_p)} | '
                         f'{fmt_discount(discount)} | {fmt_area(area)} | {fmt_unit_price(start_p, area)} | {start_time} |')
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
