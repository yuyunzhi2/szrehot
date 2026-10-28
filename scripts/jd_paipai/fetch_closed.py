# -*- coding: utf-8 -*-
"""轨道2：深圳已结束（四类目，结束时间窗口内）→ 法拍数据.xlsx「京东成交API」sheet。

用法（工作目录 = szrehot 技能目录）:
  python -X utf8 scripts/jd_paipai/fetch_closed.py [--xlsx D:\\Desktop\\法拍数据.xlsx]
      [--sheet 京东成交API] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run] [--date YYYY-MM-DD]

日常入口是 scripts/run_daily_chain.py（含幂等跳过 + 窗口随业务日期滚动）；本脚本可单独重跑轨道 2。
--since 默认今天-10天，--until 默认今天；服务端按结束时间精确过滤。
--dry-run 只抓取+组块并打印统计，不触碰 xlsx。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CATES, ST_CLOSED, build_row, enrich, paginate  # noqa: E402
from write_block import append_block  # noqa: E402

DEFAULT_XLSX = r'D:\Desktop\法拍数据.xlsx'
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_jd_cache.json')


def main():
    ap = argparse.ArgumentParser(description='京东法拍深圳已结束 → 京东成交API sheet')
    ap.add_argument('--xlsx', default=DEFAULT_XLSX)
    ap.add_argument('--sheet', default='京东成交API')
    ap.add_argument('--since', default=None, help='结束时间起点 YYYY-MM-DD（默认今天-10天）')
    ap.add_argument('--until', default=None, help='结束时间终点 YYYY-MM-DD（默认今天）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--date', default=None, help='块日期 YYYY-MM-DD（默认今天）')
    a = ap.parse_args()

    today = datetime.date.today()
    since = a.since or (today - datetime.timedelta(days=10)).isoformat()
    until = a.until or today.isoformat()
    cutoff = datetime.datetime.fromisoformat(since)
    extra = {"endTimeRangeStart": since, "endTimeRangeEnd": until}

    items = {}
    for cid, _ in CATES:
        got = paginate(cid, ST_CLOSED, extra=extra)
        for pid, (cname, dd) in got.items():
            items.setdefault(pid, (cname, dd))
    print(f'列表去重总数: {len(items)}（窗口 {since}~{until}）')
    cache = enrich(items, CACHE)

    rows, dropped = [], 0
    for pid, (cname, dd) in items.items():
        det = cache.get(str(pid)) or {}
        et = dd.get('endTime') or det.get('endTime')
        if not et or datetime.datetime.fromtimestamp(et / 1000) < cutoff:
            dropped += 1
            continue
        rows.append(build_row(cname, '已结束', dd, det))
    print(f'组块: {len(rows)} 行，窗口外/无时间剔除 {dropped}')

    if a.dry_run:
        from collections import Counter
        print('DRY-RUN:', Counter(r[0] for r in rows))
        print('DRY-RUN: 未写入 xlsx')
        return
    date = datetime.date.fromisoformat(a.date) if a.date else None
    append_block(a.xlsx, a.sheet, rows, 'closed', date)


if __name__ == '__main__':
    main()
