# -*- coding: utf-8 -*-
"""京东法拍深圳全量抓取：getSearchData(列表) + getProductBasicInfo(详情补全) → xlsx。"""
import datetime, io, json, sys, time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0',
     'Accept': 'application/json, text/plain, */*',
     'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
     'Referer': 'https://pmsearch.jd.com/', 'Accept-Language': 'zh-CN,zh;q=0.9'}
LIST_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=getSearchData&loginType=3'
DET_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=getProductBasicInfo&loginType=3'
CATES = [('15', '住宅用房'), ('14', '商业用房'), ('11', '工业用房'), ('6', '其他用房')]
STATUS = [('101', '进行中'), ('102', '预告中')]
PS = 40


def api(url, payload, timeout=30):
    body = 'body=' + requests.utils.quote(json.dumps(payload))
    r = requests.post(url, headers=H, data=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_page(cat_id, st_id, page):
    spo = {"lbsEffect": "2", "keyword": "", "reqSource": 0, "multiStatus": st_id,
           "multiThirdCateIds": cat_id, "multiProvinceIds": "19", "multiCityIds": "1607"}
    p = {"page": page, "pageSize": PS, "pageParam": "pc_search_judicature", "tabParam": "all",
         "searchParamsObj": spo, "selectedAllParamsNames": [],
         "isFirstEnter": (page == 1), "mergeSearchCondition": {}}
    return api(LIST_URL, p)


def detail(pid):
    return api(DET_URL, {"paimaiId": str(pid)})


def ms2date(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def main():
    items = {}  # paimaiId -> (cate_name, st_name, data)
    for cid, cname in CATES:
        for sid, sname in STATUS:
            page, total_item, got = 1, None, 0
            while True:
                try:
                    j = search_page(cid, sid, page)
                except Exception as e:
                    print(f'  LIST {cname}/{sname} p{page} ERR {e}', file=sys.stderr)
                    break
                d = (j.get('data') or {})
                if j.get('code') != 0:
                    print(f'  LIST {cname}/{sname} p{page} code={j.get("code")}', file=sys.stderr)
                    break
                if total_item is None:
                    total_item = ((d.get('searchCounts') or {}).get('totalItem')
                                  or (d.get('searchCounts') or {}).get('total') or 0)
                rows = d.get('resultData') or []
                if not rows:
                    break
                for it in rows:
                    dd = (it.get('data') or {})
                    pid = dd.get('paimaiId') or dd.get('uniqueId')
                    if not pid:
                        continue
                    if pid not in items:
                        items[pid] = (cname, sname, dd)
                        got += 1
                print(f'  LIST {cname}/{sname} p{page}: +{len(rows)} (累计{len(items)}, totalItem={total_item})',
                      file=sys.stderr)
                if len(rows) < PS or (total_item and page * PS >= total_item):
                    break
                page += 1
                time.sleep(0.3)
            print(f'LIST {cname}/{sname}: totalItem={total_item}, 去重累计={len(items)}')
            time.sleep(0.5)
    print(f'列表去重总数: {len(items)}')
    json.dump({str(k): {'cate': c, 'st': s, 'data': dd} for k, (c, s, dd) in items.items()},
              open('outputs/_jd_sz_raw.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print('原始列表已存 outputs/_jd_sz_raw.json')

    # 详情补全
    dets = {}
    try:
        dets = json.load(open('outputs/_jd_sz_det.json', encoding='utf-8'))
        print(f'断点续跑: 已有详情 {len(dets)}')
    except Exception:
        pass
    n = 0
    for pid in list(items):
        if str(pid) in dets:
            continue
        try:
            j = detail(pid)
            dd = (j.get('data') or {}) if j.get('code') == 0 else {}
            dets[str(pid)] = dd
            n += 1
            if n % 30 == 0:
                print(f'  DET {n}... (共{len(dets)}/{len(items)})', file=sys.stderr)
                json.dump(dets, open('outputs/_jd_sz_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
            time.sleep(0.2)
        except Exception as e:
            print(f'  DET {pid} ERR {e}', file=sys.stderr)
            time.sleep(1)
    json.dump(dets, open('outputs/_jd_sz_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'详情完成: {len(dets)}/{len(items)}')

    # 写 xlsx
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '深圳法拍'
    head = ['类目', '竞价状态', '物业名称', '所在区县', '起拍价(万元)', '当前价(万元)', '评估价(万元)',
            '折价率(%)', '建筑面积(㎡)', '保证金(万元)', '出价次数', '围观数', '拍卖开始时间',
            '拍卖结束时间', '竞价轮次', '详情链接']
    ws.append(head)
    for pid, (cname, sname, dd) in items.items():
        det = dets.get(str(pid)) or {}
        sp = dd.get('startPrice')
        cp = dd.get('currentPrice')
        ap = dd.get('assessmentPrice')
        disc = round(sp / ap * 100, 1) if sp and ap else None
        ha = dd.get('houseAttributes') or {}
        area = ha.get('houseArea')
        county = ((det.get('productAddressResult') or {}).get('county')
                  or (det.get('productAddressResult') or {}).get('town') or '')
        ens = det.get('ensurePrice')
        ws.append([
            cname, '预告中' if (dd.get('paimaiStatus') == 0 or sname == '预告中') and dd.get('paimaiStatus') != 1 else '进行中',
            dd.get('title', ''), county,
            round(sp / 10000, 2) if sp else None, round(cp / 10000, 2) if cp else None,
            round(ap / 10000, 2) if ap else None, disc,
            area, round(ens / 10000, 2) if ens else None,
            dd.get('bidCount'), dd.get('accessNum'),
            ms2date(det.get('startTime')) if det.get('startTime') else '',
            ms2date(dd.get('endTime')) if dd.get('endTime') else '',
            det.get('auctionPrefix', ''), f'https://paimai.jd.com/{pid}'])
    for col in ws.columns:
        ml = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(ml + 2, 12), 60)
    out = f'outputs/京东法拍深圳四类_进行中预告中_{datetime.date.today().isoformat()}.xlsx'
    wb.save(out)
    print(f'written: {out} rows={len(items)}')


if __name__ == '__main__':
    main()
