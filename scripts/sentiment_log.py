#!/usr/bin/env python3
"""szrehot 情绪指数日志 — 记录每日指数 + 周度/月度汇总

用法:
    python sentiment_log.py record --date 2026-08-04 --index 75.4 --label 偏多 \
        --bullish-count 10 --bullish-weight 34 \
        --neutral-count 7 --neutral-weight 21 \
        --bearish-count 1 --bearish-weight 4 \
        --weighted-sum 30 --total-weight 59 --news-count 18

    python sentiment_log.py weekly                 # 上周汇总
    python sentiment_log.py weekly --date 2026-08-04  # 指定日期所在周的上周
    python sentiment_log.py monthly                # 上月汇总
    python sentiment_log.py monthly --date 2026-08-04  # 指定日期所在月的上月
    python sentiment_log.py show [--days N]         # 查看最近N天记录
    python sentiment_log.py prune [--days 90]       # 清理90天前的记录
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_DIR = Path(r'D:\Administrator\Documents\大瑀MD')
LOG_PATH = OUTPUT_DIR / 'sentiment_log.jsonl'

# ── 7 档区间语义 ──
LABEL_MAP = [
    (20, '极度看跌'), (35, '偏空'), (45, '弱空'),
    (55, '中性'), (65, '弱多'), (80, '偏多'), (101, '极度看涨'),
]

def index_to_label(index: float) -> str:
    for threshold, label in LABEL_MAP:
        if index < threshold:
            return label
    return '极度看涨'


# ── 记录 ──

def record_entry(date: str, index: float, label: str,
                 bullish_count: int, bullish_weight: int,
                 neutral_count: int, neutral_weight: int,
                 bearish_count: int, bearish_weight: int,
                 weighted_sum: float, total_weight: int, news_count: int):
    """追加一条日指数记录。若同日已存在则覆盖。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        'date': date,
        'index': round(index, 1),
        'label': label or index_to_label(index),
        'bullish': {'count': bullish_count, 'weight': bullish_weight},
        'neutral': {'count': neutral_count, 'weight': neutral_weight},
        'bearish': {'count': bearish_count, 'weight': bearish_weight},
        'weightedSum': round(weighted_sum, 1),
        'totalWeight': total_weight,
        'newsCount': news_count,
    }

    # 检查同日是否已存在（仅读最后几行判断，避免全量读取）
    existing = load_all()
    same_day = [r for r in existing if r['date'] == date]

    if same_day:
        # 同日覆盖：需全量重写
        records = [r for r in existing if r['date'] != date]
        records.append(entry)
        records.sort(key=lambda r: r['date'])
        save_all(records)
    else:
        # 新日追加：O(1) 追加一行
        line = json.dumps(entry, ensure_ascii=False)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    print(f'Recorded: {date} index={entry["index"]} ({entry["label"]}) news={news_count}')


# ── 读取 ──

def load_all() -> list:
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text(encoding='utf-8').strip().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def save_all(records: list):
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    LOG_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def get_date_range(records: list, start: str, end: str) -> list:
    """获取 start <= date <= end 范围内的记录。"""
    return [r for r in records if start <= r['date'] <= end]


# ── 汇总算法 ──

def aggregate(records: list) -> dict:
    """加权平均：权重=当日新闻数(newsCount)。

    公式：汇总指数 = sum(日index * 日newsCount) / sum(日newsCount)
    """
    if not records:
        return {'index': 50.0, 'label': '中性', 'days': 0,
                'tradingDays': 0, 'totalNews': 0}

    total_news = sum(r['newsCount'] for r in records)
    if total_news == 0:
        return {'index': 50.0, 'label': '中性', 'days': len(records),
                'tradingDays': 0, 'totalNews': 0}

    weighted_index = sum(r['index'] * r['newsCount'] for r in records) / total_news
    trading_days = [r for r in records if r['newsCount'] > 0]

    return {
        'index': round(weighted_index, 1),
        'label': index_to_label(weighted_index),
        'days': len(records),
        'tradingDays': len(trading_days),
        'totalNews': total_news,
    }


def weekly_summary(date_str: str = None) -> dict:
    """上周一~上周日的汇总。"""
    if date_str:
        d = datetime.strptime(date_str, '%Y-%m-%d')
    else:
        d = datetime.now()

    # 本周一
    weekday = d.weekday()  # 0=Mon
    this_monday = d - timedelta(days=weekday)
    # 上周一~上周日
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    records = load_all()
    week_records = get_date_range(records, last_monday.strftime('%Y-%m-%d'),
                                          last_sunday.strftime('%Y-%m-%d'))

    result = aggregate(week_records)
    result['period'] = 'weekly'
    result['startDate'] = last_monday.strftime('%Y-%m-%d')
    result['endDate'] = last_sunday.strftime('%Y-%m-%d')
    result['dailyRecords'] = week_records
    return result


def monthly_summary(date_str: str = None) -> dict:
    """上月1日~上月末日的汇总。"""
    if date_str:
        d = datetime.strptime(date_str, '%Y-%m-%d')
    else:
        d = datetime.now()

    # 上月
    first_of_this_month = d.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)

    records = load_all()
    month_records = get_date_range(records, first_of_prev_month.strftime('%Y-%m-%d'),
                                          last_of_prev_month.strftime('%Y-%m-%d'))

    result = aggregate(month_records)
    result['period'] = 'monthly'
    result['startDate'] = first_of_prev_month.strftime('%Y-%m-%d')
    result['endDate'] = last_of_prev_month.strftime('%Y-%m-%d')
    result['dailyRecords'] = month_records
    return result


# ── 格式化输出 ──

def format_weekly(summary: dict) -> str:
    """格式化周度汇总为 Markdown。"""
    start = datetime.strptime(summary['startDate'], '%Y-%m-%d')
    end = datetime.strptime(summary['endDate'], '%Y-%m-%d')
    weekdays_cn = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

    lines = [
        f'## 上周市场情绪汇总（{start.month}.{start.day}-{end.month}.{end.day}）',
        '',
        f'**周度情绪指数：{summary["index"]}（{summary["label"]}）**',
        '',
        f'| 日期 | 指数 | 标签 | 新闻数 |',
        f'|------|------|------|--------|',
    ]

    # 构建日期→记录映射
    rec_map = {r['date']: r for r in summary['dailyRecords']}
    current = start
    while current <= end:
        ds = current.strftime('%Y-%m-%d')
        wd = weekdays_cn[current.weekday()]
        if ds in rec_map:
            r = rec_map[ds]
            lines.append(f'| {current.month}.{current.day} {wd} | {r["index"]} | {r["label"]} | {r["newsCount"]} |')
        else:
            lines.append(f'| {current.month}.{current.day} {wd} | — | 无数据 | 0 |')
        current += timedelta(days=1)

    lines.append('')
    lines.append(f'> 交易日{summary["tradingDays"]}天，总新闻{summary["totalNews"]}条，加权平均指数{summary["index"]}（{summary["label"]}）。')
    return '\n'.join(lines)


def format_monthly(summary: dict) -> str:
    """格式化月度汇总为 Markdown。"""
    start = datetime.strptime(summary['startDate'], '%Y-%m-%d')
    end = datetime.strptime(summary['endDate'], '%Y-%m-%d')

    lines = [
        f'## {start.year}年{start.month}月市场情绪汇总',
        '',
        f'**月度情绪指数：{summary["index"]}（{summary["label"]}）**',
        '',
        f'| 日期 | 指数 | 标签 | 新闻数 |',
        f'|------|------|------|--------|',
    ]

    for r in summary['dailyRecords']:
        d = datetime.strptime(r['date'], '%Y-%m-%d')
        lines.append(f'| {d.month}.{d.day} | {r["index"]} | {r["label"]} | {r["newsCount"]} |')

    lines.append('')
    lines.append(f'> 交易日{summary["tradingDays"]}天，总新闻{summary["totalNews"]}条，加权平均指数{summary["index"]}（{summary["label"]}）。')
    return '\n'.join(lines)


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description='szrehot 情绪指数日志')
    sub = parser.add_subparsers(dest='cmd')

    # record
    p_rec = sub.add_parser('record', help='记录日指数')
    p_rec.add_argument('--date', required=True)
    p_rec.add_argument('--index', type=float, required=True)
    p_rec.add_argument('--label', default='')
    p_rec.add_argument('--bullish-count', type=int, required=True)
    p_rec.add_argument('--bullish-weight', type=int, required=True)
    p_rec.add_argument('--neutral-count', type=int, required=True)
    p_rec.add_argument('--neutral-weight', type=int, required=True)
    p_rec.add_argument('--bearish-count', type=int, required=True)
    p_rec.add_argument('--bearish-weight', type=int, required=True)
    p_rec.add_argument('--weighted-sum', type=float, required=True)
    p_rec.add_argument('--total-weight', type=int, required=True)
    p_rec.add_argument('--news-count', type=int, required=True)

    # weekly
    p_weekly = sub.add_parser('weekly', help='上周情绪汇总')
    p_weekly.add_argument('--date', default=None, help='参考日期(默认今天)')
    p_weekly.add_argument('--json', action='store_true', help='输出JSON')

    # monthly
    p_monthly = sub.add_parser('monthly', help='上月情绪汇总')
    p_monthly.add_argument('--date', default=None, help='参考日期(默认今天)')
    p_monthly.add_argument('--json', action='store_true', help='输出JSON')

    # show
    p_show = sub.add_parser('show', help='查看最近记录')
    p_show.add_argument('--days', type=int, default=7)

    # prune
    p_prune = sub.add_parser('prune', help='清理旧记录')
    p_prune.add_argument('--days', type=int, default=90)

    args = parser.parse_args()

    if args.cmd == 'record':
        record_entry(
            args.date, args.index, args.label,
            args.bullish_count, args.bullish_weight,
            args.neutral_count, args.neutral_weight,
            args.bearish_count, args.bearish_weight,
            args.weighted_sum, args.total_weight, args.news_count,
        )
    elif args.cmd == 'weekly':
        summary = weekly_summary(args.date)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(format_weekly(summary))
    elif args.cmd == 'monthly':
        summary = monthly_summary(args.date)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(format_monthly(summary))
    elif args.cmd == 'show':
        records = load_all()
        recent = records[-args.days:] if len(records) > args.days else records
        for r in recent:
            print(f'{r["date"]}  {r["index"]:5.1f}  {r["label"]:6s}  news={r["newsCount"]}')
    elif args.cmd == 'prune':
        records = load_all()
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        before = len(records)
        records = [r for r in records if r['date'] >= cutoff]
        save_all(records)
        print(f'Pruned {before - len(records)} records before {cutoff}')
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
