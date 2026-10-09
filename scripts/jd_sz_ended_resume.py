# -*- coding: utf-8 -*-
"""断点续跑：补全详情(每轮上限) → 全部完成则过滤写xlsx。"""
import datetime, io, json, sys, time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0',
     'Accept': 'application/json, text/plain, */*',
     'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
     'Referer': 'https://pmsearch.jd.com/', 'Accept-Language': 'zh-CN,zh;q=0.9'}
DET_URL = 'https://api.m.jd.com/api?appid=paimai&functionId=getProductBasicInfo&loginType=3'
CUTOFF = datetime.datetime(2026, 9, 29)
MAX_NEW = int(sys.argv[1]) if len(sys.argv) > 1 else 500


def ms2date(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


items = json.load(open('outputs/_jd_sz_ended_raw.json', encoding='utf-8'))
try:
    dets = json.load(open('outputs/_jd_sz_ended_det.json', encoding='utf-8'))
except Exception:
    dets = {}
todo = [p for p in items if p not in dets]
print(f'raw={len(items)} det={len(dets)} todo={len(todo)} cap={MAX_NEW}')
n = 0
for pid in todo:
    if n >= MAX_NEW:
        break
    try:
        body = 'body=' + requests.utils.quote(json.dumps({"paimaiId": str(pid)}))
        r = requests.post(DET_URL, headers=H, data=body, timeout=30)
        j = r.json()
        dets[pid] = (j.get('data') or {}) if j.get('code') == 0 else {}
        n += 1
        if n % 50 == 0:
            print(f'  DET +{n} (共{len(dets)}/{len(items)})', file=sys.stderr)
            json.dump(dets, open('outputs/_jd_sz_ended_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
        time.sleep(0.15)
    except Exception as e:
        print(f'  DET {pid} ERR {e}', file=sys.stderr)
        time.sleep(1)
json.dump(dets, open('outputs/_jd_sz_ended_det.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(f'本轮+{n}, det共{len(dets)}/{len(items)}')
if len(dets) < len(items):
    print('未完成,需再跑一轮')
    sys.exit(0)

kept, dropped = {}, 0
for pid, rec in items.items():
    dd, det = rec['data'], dets.get(pid) or {}
    et = dd.get('endTime') or det.get('endTime')
    if not et:
        dropped += 1
        continue
    if datetime.datetime.fromtimestamp(et / 1000) >= CUTOFF:
        kept[pid] = (rec['cate'], dd, det)
    else:
        dropped += 1
print(f'窗口过滤: 保留 {len(kept)}, 剔除 {dropped}')
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
    ws.append([cname, '已结束', dd.get('title', ''), county,
               round(sp / 10000, 2) if sp else None, round(cp / 10000, 2) if cp else None,
               round(ap / 10000, 2) if ap else None, disc, ha.get('houseArea'),
               round(ens / 10000, 2) if ens else None, dd.get('bidCount'), dd.get('accessNum'),
               ms2date(det.get('startTime')) if det.get('startTime') else '',
               ms2date(et), det.get('auctionPrefix', ''), f'https://paimai.jd.com/{pid}'])
for col in ws.columns:
    ml = max((len(str(c.value)) if c.value is not None else 0) for c in col)
    ws.column_dimensions[col[0].column_letter].width = min(max(ml + 2, 12), 60)
out = f'outputs/京东法拍深圳四类_已结束0929起_{datetime.date.today().isoformat()}.xlsx'
wb.save(out)
print(f'written: {out} rows={len(kept)}')
