# -*- coding: utf-8 -*-
"""京东法拍深圳已结束(9.29起)抓取：getSearchData 默认排序分页 + endTime硬过滤 + 详情补全 → xlsx。"""
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
CUTOFF = datetime.datetime(2026, 9, 29)
PS = 40
MAX_PAGES = 40


def api(url, payload, timeout=30):
    body = 'body=' + requests.utils.quote(json.dumps(payload))
    r = requests.post(url, headers=H, data=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_page(cat_id, page):
    spo = {"lbsEffect": "2", "keyword": "", "reqSource": 0, "multiStatus": "105",
           "multiThirdCateIds": cat_id, "multiProvinceIds": "19", "multiCityIds": "1607"}
    p = {"page": page, "pageSize": PS, "pageParam": "pc_search_judicature", "tabParam": "all",
         "searchParamsObj": spo, "selectedAllParamsNames": [],
         "isFirstEnter": (page == 1), "mergeSearchCondition": {}}
    return api(LIST_URL, p)


def ms2date(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def main():
    items = {}
    for cid, cname in CATES:
        page, total_item = 1, None
        while page <= MAX_PAGES:
            try:
                j = search_page(cid, page)
            except Exception as e:
                print(f'  LIST {cname} p{page} ERR {e}', file=sys.stderr)
                break
            d = (j.get('data') or {})
            if j.get('code') != 0:
                print(f'  LIST {cname} p{page} code={j.get("code")}', file=sys.stderr)
                break
            if total_item is None:
                total_item = (d.get('searchCounts') or {}).get('totalItem')
            rows = d.get('resultData') or []
            if not rows:
                break
            for it in rows:
                dd = (it.get('data') or {})
                pid = dd.get('paimaiId') or dd.get('uniqueId')
                if pid and pid not in items:
                    items[pid] = (cname, dd)
            print(f'  LIST {cname} p{page}: +{len(rows)} (累计{len(items)}, totalItem={total_item})', file=sys.stderr)
            if len(rows) < PS:
                break
            page += 1
            time.sleep(0.3)
        print(f'LIST {cname}: totalItem={total_item}, 去重累计={len(items)}')
        time.sleep(0.5)
    print(f'列表去重总数: {len(items)}')
    json.dump({str(k): {'cate': c, 'data': dd} for k, (c, dd) in items.items()},
              open('outputs/_jd_sz_ended_raw.json', 'w', encoding='utf-8'), ensure_ascii=False)

    # 详情补全(含endTime兜底)
    dets = {}
    try:
        dets = json.load(open('outputs/_jd_sz_ended_det.json', encoding='utf-8'))
        print(f'断点续跑: 已有详情 {len(dets)}')
    except Exception:
        pass
    n = 0
    for pid in list(items):
        if str(pid) in dets:
            continue
        try:
            j = api(DET_URL, {"paimaiId": str(pid)})
            dets[str(pid)] = (j.get('data') or {}) if j.get('code') == 0 else {}
            n += 1
            if n % 30 == 0:
                print(f'  DET {n}... (共{len(dets)}/{len(items)})', file=sys.stderr)
                json.dump(dets, open('outputs/_jd_sz_ended_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
            time.sleep(0.2)
        except Exception as e:
            print(f'  DET {pid} ERR {e}', file=sys.stderr)
            time.sleep(1)
    json.dump(dets, open('outputs/_jd_sz_ended_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'详情完成: {len(dets)}/{len(items)}')

    # 9.29硬过滤(endTime;列表缺失时用详情endTime兜底;双缺失剔除)
    kept, dropped = {}, 0
    for pid, (cname, dd) in items.items():
        det = dets.get(str(pid)) or {}
        et = dd.get('endTime') or det.get('endTime')
        if not et:
            dropped += 1
            continue
        if datetime.datetime.fromtimestamp(et / 1000) >= CUTOFF:
            kept[pid] = (cname, dd, det)
        else:
            dropped += 1
    print(f'窗口过滤: 保留 {len(kept)}, 剔除(窗口外/无时间) {dropped}')

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '深圳法拍'
    ws.append(['类目', '竞价状态', '物业名称', '所在区县', '起拍价(万元)', '当前价(万元)', '评估价(万元)',
               '折价率(%)', '建筑面积(㎡)', '保证金(万元)', '出价次数', '围观数', '拍卖开始时间',
               '拍卖结束时间', '竞价轮次', '详情链接'])
    for pid, (cname, dd, det) in kept.items():
        sp, cp, ap = dd.get('startPrice'), dd.get('currentPrice'), dd.get('assessmentPrice')
        disc = round(sp / ap * 100, 1) if sp and ap else None
        ha = dd.get('houseAttributes') or {}
        county = ((det.get('productAddressResult') or {}).get('county')
                  or (det.get('productAddressResult') or {}).get('town') or '')
        ens = det.get('ensurePrice')
        et = dd.get('endTime') or det.get('endTime')
        ws.append([
            cname, '已结束', dd.get('title', ''), county,
            round(sp / 10000, 2) if sp else None, round(cp / 10000, 2) if cp else None,
            round(ap / 10000, 2) if ap else None, disc,
            ha.get('houseArea'), round(ens / 10000, 2) if ens else None,
            dd.get('bidCount'), dd.get('accessNum'),
            ms2date(det.get('startTime')) if det.get('startTime') else '',
            ms2date(et), det.get('auctionPrefix', ''), f'https://paimai.jd.com/{pid}'])
    for col in ws.columns:
        ml = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(ml + 2, 12), 60)
    out = f'outputs/京东法拍深圳四类_已结束0929起_{datetime.date.today().isoformat()}.xlsx'
    wb.save(out)
    print(f'written: {out} rows={len(kept)}')


if __name__ == '__main__':
    main()
