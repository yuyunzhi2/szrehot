# -*- coding: utf-8 -*-
"""轨道1：深圳进行中 + 预告中（四类目）→ 法拍数据.xlsx「京东API」sheet。

用法（工作目录 = szrehot 技能目录）:
  python -X utf8 scripts/jd_paipai/fetch_active.py [--xlsx D:\\Desktop\\法拍数据.xlsx]
      [--sheet 京东API] [--dry-run] [--date YYYY-MM-DD]

日常入口是 scripts/run_daily_chain.py（含幂等跳过）；本脚本可单独重跑轨道 1。
--dry-run 只抓取+组块并打印统计，不触碰 xlsx。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CATES, ST_ACTIVE, STATUS_LABEL, build_row, enrich, paginate  # noqa: E402
from write_block import append_block  # noqa: E402

DEFAULT_XLSX = r'D:\Desktop\法拍数据.xlsx'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_jd_cache.json')


def main():
    ap = argparse.ArgumentParser(description='京东法拍深圳进行中+预告中 → 京东API sheet')
    ap.add_argument('--xlsx', default=DEFAULT_XLSX)
    ap.add_argument('--sheet', default='京东API')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--date', default=None, help='块日期 YYYY-MM-DD（默认今天）')
    a = ap.parse_args()

    items = {}
    for cid, _ in CATES:
        for sid, _ in ST_ACTIVE:
            got = paginate(cid, sid)
            for pid, (cname, dd) in got.items():
                items.setdefault(pid, (cname, dd))
    print(f'列表去重总数: {len(items)}')
    cache = enrich(items, CACHE)

    rows = []
    for pid, (cname, dd) in items.items():
        st = STATUS_LABEL.get(dd.get('paimaiStatus'), '进行中')
        rows.append(build_row(cname, st, dd, cache.get(str(pid)) or {}))
    print(f'组块: {len(rows)} 行')

    if a.dry_run:
        from collections import Counter
        print('DRY-RUN:', Counter((r[0], r[1]) for r in rows))
        print('DRY-RUN: 未写入 xlsx')
        return
    date = datetime.date.fromisoformat(a.date) if a.date else None
    append_block(a.xlsx, a.sheet, rows, 'active', date)


if __name__ == '__main__':
    main()
