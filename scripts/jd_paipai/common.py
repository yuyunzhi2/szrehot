# -*- coding: utf-8 -*-
"""jd-paipai 共享层（szrehot 每日链步骤 1）：API client + 常量 + 行构建。实测沉淀，见 references/jd-paipai.md。"""
import datetime
import io
import json
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

LIST_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=getSearchData&loginType=3'
DET_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=getProductBasicInfo&loginType=3'
DESC_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=queryProductDescription&loginType=3'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0',
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
    'Referer': 'https://pmsearch.jd.com/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 连接复用：同一 host 的 TCP+TLS 连接留池复用（keep-alive），省掉每个请求
# 的握手开销。多线程场景下每线程各持一个 Session（见 get_session）。
import threading as _threading

_session_local = _threading.local()


def get_session():
    s = getattr(_session_local, 's', None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _session_local.s = s
    return s

# 资产类目 multiThirdCateIds（深圳固定只取这四类）
CATES = [('15', '住宅用房'), ('14', '商业用房'), ('11', '工业用房'), ('6', '其他用房')]
# 竞价状态 multiStatus
ST_ACTIVE = [('101', '进行中'), ('102', '预告中')]
ST_CLOSED = '105'  # 已结束（行内状态统一写“已结束”）
CITY = {"multiProvinceIds": "19", "multiCityIds": "1607"}  # 广东/深圳

# 16 列块格式（references/columns.md）
COLS = ['类目', '竞价状态', '物业名称', '所在区县', '起拍价(万元)', '当前价(万元)',
        '评估价(万元)', '折价率(%)', '建筑面积(㎡)', '保证金(万元)', '出价次数',
        '围观数', '拍卖开始时间', '拍卖结束时间', '竞价轮次', '详情链接']

STATUS_LABEL = {1: '进行中', 0: '预告中'}


def api(url, payload, timeout=30):
    body = 'body=' + requests.utils.quote(json.dumps(payload))
    r = get_session().post(url, data=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_page(cat_id, status_id, page, page_size=40, extra=None, first=False):
    spo = {"lbsEffect": "2", "keyword": "", "reqSource": 0,
           "multiStatus": status_id, "multiThirdCateIds": cat_id}
    spo.update(CITY)
    if extra:
        spo.update(extra)
    p = {"page": page, "pageSize": page_size, "pageParam": "pc_search_judicature",
         "tabParam": "all", "searchParamsObj": spo, "selectedAllParamsNames": [],
         "isFirstEnter": first, "mergeSearchCondition": {}}
    return api(LIST_URL, p)


def fetch_detail(pid):
    j = api(DET_URL, {"paimaiId": str(pid)})
    return (j.get('data') or {}) if j.get('code') == 0 else {}


def ms2str(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def wan(yuan):
    try:
        return round(float(yuan) / 10000, 2) if yuan is not None else None
    except Exception:
        return None


def build_row(cname, st_label, dd, det):
    """列表 dd + 详情 det → 16 列行。价格接口单位为元，统一换算万元。"""
    pid = dd.get('paimaiId') or dd.get('uniqueId')
    sp, cp, ap = dd.get('startPrice'), dd.get('currentPrice'), dd.get('assessmentPrice')
    disc = round(sp / ap * 100, 1) if sp and ap else None
    ha = dd.get('houseAttributes') or {}
    addr = det.get('productAddressResult') or {}
    return [cname, st_label, dd.get('title', ''), addr.get('county') or addr.get('town') or '',
            wan(sp), wan(cp), wan(ap), disc, ha.get('houseArea'),
            wan(det.get('ensurePrice')), dd.get('bidCount'), dd.get('accessNum'),
            ms2str(det.get('startTime')) if det.get('startTime') else '',
            ms2str(dd.get('endTime') or det.get('endTime')),
            det.get('auctionPrefix', ''), f'https://paimai.jd.com/{pid}']


def paginate(cat_id, status_id, extra=None, sleep=0.3):
    """翻到空页为止，返回去重后的 {pid: (cname, dd)}。计数器不可信，以空页为准。"""
    cname = dict(CATES)[cat_id]
    items, page = {}, 1
    while True:
        j = search_page(cat_id, status_id, page, extra=extra, first=(page == 1))
        d = j.get('data') or {}
        if j.get('code') != 0:
            print(f'  LIST {cname}/{status_id} p{page} code={j.get("code")}', file=sys.stderr)
            break
        rows = d.get('resultData') or []
        if not rows:
            break
        for it in rows:
            dd = it.get('data') or {}
            pid = dd.get('paimaiId') or dd.get('uniqueId')
            if pid and pid not in items:
                items[pid] = (cname, dd)
        print(f'  LIST {cname} p{page}: +{len(rows)} (去重累计{len(items)})', file=sys.stderr)
        if len(rows) < 40:
            break
        page += 1
        time.sleep(sleep)
    return items


def enrich(items, cache_path, sleep=0.15):
    """详情补全，带断点续跑缓存。返回 {pid: det}。"""
    try:
        cache = json.load(open(cache_path, encoding='utf-8'))
    except Exception:
        cache = {}
    n = 0
    for pid in list(items):
        if str(pid) in cache:
            continue
        try:
            cache[str(pid)] = fetch_detail(pid)
            n += 1
            if n % 50 == 0:
                print(f'  DET +{n} (共{len(cache)})', file=sys.stderr)
                json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False)
            time.sleep(sleep)
        except Exception as e:
            print(f'  DET {pid} ERR {e}', file=sys.stderr)
            time.sleep(1)
    json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'详情: 新增{n}, 共{len(cache)}', file=sys.stderr)
    return cache
