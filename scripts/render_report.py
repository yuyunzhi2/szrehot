# -*- coding: utf-8 -*-
"""szrehot 日报渲染器：从 _final.json + 租赁 md 渲染完整日报 md（UTF-8 无 BOM）。

用法（工作目录 = 技能目录）:
  python -X utf8 scripts/render_report.py --final scripts/_final.json --date 2026-08-11
      [--rentals "scripts/深圳阳光租赁_三类_2026-08-10起.md"] [--rentals-summary "摘要段文字"]
      [--mode daily|jingxuan] [--weekly] [--monthly]
      [--out D:\\Administrator\\Documents\\大瑀MD\\深圳资产日报_2026-08-11.md]
      [--window-start 2026-08-10] [--no-record]
      [--paipai-xlsx D:\\Desktop\\法拍数据.xlsx]

--final 每条字段: title / url / source / publishedAt(可空) / category /
  summary(50-100字摘要) / sentiment(bullish|bearish|neutral) / weight(1-5)

来源清洗: Google Alerts 通道名（如「Google Alert·深圳 城市更新」）不写入成果文档，
  渲染时自动提取标题尾部媒体名（「... - 咚咚地产头条」）作为来源；提取不到则省略来源标记。

脚本自动完成:
  - 6 章节分组 + 贯穿全文编号
  - 窗口起点计算（周一=上周五）
  - 情绪指数聚合（公式 50 + 50×Σ(val×w)/Σw）+ 头部「当日市场情绪指数」章节
  - 条目尾部「🔴看涨 ★★★★」格式
  - 租赁表格从 md 提取
  - UTF-8 无 BOM 写入 D:\\Administrator\\Documents\\大瑀MD
  - 调 dedup.py batch-record 写入跨日索引（--no-record 跳过）
  - 调 sentiment_log.py record 记录当日情绪指数（--no-record 同时跳过）
  - --weekly/--monthly 时追加周/月度情绪汇总到日报末尾

精选模式（--mode jingxuan）: 仅保留 情绪≠中性 且 权重≥3 的条目，
指数基于精选子集计算（日报内部自洽）。
"""
import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CATEGORY_ORDER = [
    ('policy', '政策法规'), ('market', '市场行情'), ('urban_renewal', '城市更新'),
    ('company', '标杆公司'), ('research', '研究报告'), ('insights', '观点洞察'),
]
CATEGORY_MAP = dict(CATEGORY_ORDER)

SENTIMENT_VAL = {'bullish': +1, 'bearish': -1, 'neutral': 0}
SENTIMENT_ICON = {'bullish': '🔴看涨', 'bearish': '🟢看跌', 'neutral': '⚪中性'}

# 情绪枚举容错别名映射（主模型手写 JSON 时可能用缩写/中文/图标）
# bull/bear 是 2026-08-12 实测踩坑：写错导致全部静默回退 neutral
SENTIMENT_ALIAS = {
    'bullish': 'bullish', 'bull': 'bullish', '看涨': 'bullish', '🔴': 'bullish', '🔴看涨': 'bullish', '+1': 'bullish',
    'bearish': 'bearish', 'bear': 'bearish', '看跌': 'bearish', '🟢': 'bearish', '🟢看跌': 'bearish', '-1': 'bearish',
    'neutral': 'neutral', '中性': 'neutral', '⚪': 'neutral', '⚪中性': 'neutral', '0': 'neutral',
}


def normalize_sentiment(raw) -> str:
    """规范化情绪值到 bullish/bearish/neutral；未知值警告并回退 neutral。"""
    if raw is None:
        return 'neutral'
    s = str(raw).strip().lower()
    if s in SENTIMENT_ALIAS:
        return SENTIMENT_ALIAS[s]
    # 容错：精确匹配失败后做包含匹配（如 "bullish " / "看涨(强)"）
    for k, v in SENTIMENT_ALIAS.items():
        if k and s and k in s:
            return v
    print(f'[render] ⚠️ 未知 sentiment 值 {raw!r}，回退 neutral（合法值: bullish/bearish/neutral 或其别名）')
    return 'neutral'

# 区间边界与 sentiment_log.py 的 LABEL_MAP 保持一致
INDEX_BANDS = [
    (0, 20, '极度看跌', '市场情绪显著偏空'),
    (20, 35, '偏空', '市场情绪偏弱'),
    (35, 45, '弱空', '市场情绪略偏谨慎'),
    (45, 55, '中性', '市场情绪多空交织'),
    (55, 65, '弱多', '市场情绪略偏积极'),
    (65, 80, '偏多', '市场情绪偏暖'),
    (80, 101, '极度看涨', '市场情绪显著偏多'),
]


def _to_int(v, default=0):
    """容错转 int（json 中主模型可能写字符串）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp_weight(w: int) -> int:
    return max(1, min(5, w))


def _clean_source(raw, title=''):
    """清洗来源标记：隐藏 Google Alerts / 检索通道名（成果文档不出现 google alert 字样）。

    检索通道形如「深圳 城市更新」「深圳 规划」「深圳 房地产 开发」等，系 S1.5 检索
    源标记而非真实媒体，不直接展示；优先从标题尾部提取真实媒体名
    （形如「... - 搜狐」「... - 咚咚地产头条- 深圳房地产信息网」）；提取不到则隐藏。
    """
    if not raw:
        return ''
    s = str(raw).strip()
    is_channel = bool(re.search(r'google\s*alerts?', s, flags=re.IGNORECASE)) or \
        (s.startswith('深圳') and re.search(r'(城市更新|规划|商业|土地|房地产|开发)', s))
    if is_channel:
        m = re.search(r'[-–—]\s*([^-–—]+?)\s*$', title or '')
        if m:
            return m.group(1).strip()
        return ''
    return s


def index_band(index: float) -> tuple[str, str]:
    """返回 (标签, 日报措辞)。"""
    for lo, hi, label, phrase in INDEX_BANDS:
        if index < hi:
            return label, phrase
    return '极度看涨', '市场情绪显著偏多'


def compute_sentiment(items: list) -> dict:
    """基于每条 sentiment+weight 聚合情绪指数。

    公式: 指数 = 50 + 50 × Σ(情绪值ᵢ × 权重ᵢ) / Σ权重ᵢ
    0=极度看跌, 50=中性, 100=极度看涨。总权重为 0 时指数=50。
    """
    counts = {'bullish': 0, 'bearish': 0, 'neutral': 0}
    weights = {'bullish': 0, 'bearish': 0, 'neutral': 0}
    weighted_sum = 0
    total_weight = 0
    for it in items:
        sent = normalize_sentiment(it.get('sentiment', 'neutral'))
        w = _clamp_weight(_to_int(it.get('weight'), 1))
        counts[sent] += 1
        weights[sent] += w
        weighted_sum += SENTIMENT_VAL[sent] * w
        total_weight += w
    index = 50.0 if total_weight == 0 else 50 + 50 * weighted_sum / total_weight
    index = round(index, 1)
    label, phrase = index_band(index)
    return {
        'index': index, 'label': label, 'phrase': phrase,
        'bullish_count': counts['bullish'], 'bullish_weight': weights['bullish'],
        'neutral_count': counts['neutral'], 'neutral_weight': weights['neutral'],
        'bearish_count': counts['bearish'], 'bearish_weight': weights['bearish'],
        'weighted_sum': weighted_sum, 'total_weight': total_weight,
        'news_count': len(items),
    }


def window_start(d: datetime.date) -> datetime.date:
    """周一→上周五 0:00；其余→前一天 0:00。北京时间(本地时区)。"""
    if d.weekday() == 0:  # Monday
        return d - datetime.timedelta(days=3)
    return d - datetime.timedelta(days=1)


def fmt_md(d: datetime.date) -> str:
    return f'{d.month}月{d.day}日'


def extract_rentals_table(md_path: str) -> tuple[list[str], list[str]]:
    """从租赁 md 提取：摘要段(> 引用块/表格前的段落) + 表格行。失败返回 ([],[])。"""
    try:
        with open(md_path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return [], []
    lines = text.splitlines()
    table_rows = [ln for ln in lines if ln.strip().startswith('|')]
    # 摘要段：表格之前、非表格的连续段落
    summary_paras = []
    for ln in lines:
        if ln.strip().startswith('|'):
            break
        if ln.strip() and not ln.strip().startswith(('#', '>')) and not ln.strip().startswith('---'):
            summary_paras.append(ln.strip())
    return summary_paras, table_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--final', required=True)
    ap.add_argument('--date', required=True)
    ap.add_argument('--rentals')
    ap.add_argument('--rentals-summary')
    ap.add_argument('--mode', default='daily', choices=['daily', 'jingxuan'])
    ap.add_argument('--out')
    ap.add_argument('--window-start')
    ap.add_argument('--weekly', action='store_true', help='追加上周情绪汇总（周一运行）')
    ap.add_argument('--monthly', action='store_true', help='追加上月情绪汇总（每月1日运行）')
    ap.add_argument('--no-record', action='store_true')
    ap.add_argument('--paipai-xlsx', default=r'D:\Desktop\法拍数据.xlsx',
                    help='法拍数据.xlsx 路径（默认 D:\\Desktop\\法拍数据.xlsx，默认必产「今日法拍资产热点」章节；传 --no-paipai 关闭）')
    ap.add_argument('--no-paipai', action='store_true', help='关闭法拍章节（数据源长期不可用时不阻塞）')
    a = ap.parse_args()

    today = datetime.date.fromisoformat(a.date)
    ws = datetime.date.fromisoformat(a['window-start']) if a.window_start else window_start(today)

    with open(a.final, encoding='utf-8') as f:
        items = json.load(f)

    # 精选模式：仅保留 情绪≠中性 且 权重≥3 的条目；指数基于精选子集
    if a.mode == 'jingxuan':
        items = [it for it in items
                 if normalize_sentiment(it.get('sentiment')) in ('bullish', 'bearish')
                 and _clamp_weight(_to_int(it.get('weight'), 1)) >= 3]

    sentiment = compute_sentiment(items)

    # 按章节分组，保持原顺序，编号贯穿全文
    sections = {k: [] for k, _ in CATEGORY_ORDER}
    for it in items:
        cat = it.get('category', 'market')
        sections.setdefault(cat, []).append(it)
    seq = 0
    lines = []
    lines.append(f'**深圳资产日报 · {today.isoformat()}**' if a.mode == 'daily'
                 else f'**深圳资产精选 · {today.isoformat()}**')
    lines.append('')
    lines.append(f'> 时间窗口：{ws.isoformat()} 00:00 ~ {today.isoformat()} 当前'
                 + ('（周一执行，窗口起点为上周五，跨周末）' if today.weekday() == 0 else ''))
    lines.append('')
    # 当日市场情绪指数（必产章节，放在窗口行下方）
    ws_sum = sentiment['weighted_sum']
    ws_str = f'+{ws_sum}' if ws_sum >= 0 else str(ws_sum)
    lines.append('## 当日市场情绪指数')
    lines.append('')
    lines.append(f'**综合情绪指数：{sentiment["index"]}（{sentiment["label"]}）**')
    lines.append('')
    lines.append('| 指标 | 数值 |')
    lines.append('|------|------|')
    lines.append(f'| 看涨条目 | {sentiment["bullish_count"]}条（权重合计{sentiment["bullish_weight"]}） |')
    lines.append(f'| 中性条目 | {sentiment["neutral_count"]}条（权重合计{sentiment["neutral_weight"]}） |')
    lines.append(f'| 看跌条目 | {sentiment["bearish_count"]}条（权重合计{sentiment["bearish_weight"]}） |')
    lines.append(f'| 加权情绪和 | {ws_str} |')
    lines.append(f'| 总权重 | {sentiment["total_weight"]} |')
    lines.append('')
    lines.append(f'> 指数解读：{sentiment["index"]}（{sentiment["label"]}）——{sentiment["phrase"]}。')
    lines.append('')
    for cat, title in CATEGORY_ORDER:
        sec = sections.get(cat) or []
        if not sec:
            lines.append(f'## {title}')
            lines.append('')
            lines.append('今日暂无更新。')
            lines.append('')
            continue
        lines.append(f'## {title}')
        lines.append('')
        for it in sec:
            seq += 1
            pub = it.get('publishedAt') or ''
            pub_s = ''
            if pub:
                try:
                    d = datetime.date.fromisoformat(pub[:10])
                    pub_s = fmt_md(d)
                except ValueError:
                    pub_s = '时间未知'
            else:
                pub_s = '时间未知'
            sent = normalize_sentiment(it.get('sentiment', 'neutral'))
            w = _clamp_weight(_to_int(it.get('weight'), 1))
            _src = _clean_source(it.get("source", ""), it.get("title", ""))
            _title = it["title"]
            # 去掉标题尾部与来源重复的媒体后缀，避免「标题 - 搜狐 — 搜狐」式重复
            if _src:
                _title = re.sub(r'\s*[-–—]\s*' + re.escape(_src) + r'\s*$', '', _title)
            lines.append(f'{seq}. **{_title}**' + (f' — {_src}' if _src else ""))
            lines.append(f'   {it.get("summary", "")}')
            lines.append(f'   {pub_s} | {SENTIMENT_ICON[sent]} {"★" * w}')
            lines.append(f'   {it["url"]}')
            lines.append('')

    # 大宗租赁关注
    lines.append('## 大宗租赁关注')
    lines.append('')
    lines.append(f'> 数据来源：深圳阳光租赁平台（rent.szexgrp.com），listingStartDate {ws.isoformat()} ~ {today.isoformat()} 当前')
    lines.append('')
    summary_paras, table_rows = [], []
    if a.rentals:
        summary_paras, table_rows = extract_rentals_table(a.rentals)
    if a.rentals_summary:
        lines.append(a.rentals_summary.strip())
        lines.append('')
    elif summary_paras:
        lines.append(' '.join(summary_paras))
        lines.append('')
    else:
        lines.append('今日阳光租赁平台无新增大宗招租项目。')
        lines.append('')
    if table_rows:
        lines.extend(table_rows)
        lines.append('')
        if len(table_rows) > 31:
            lines.append(f'> 完整清单见 {os.path.basename(a.rentals)}')
            lines.append('')

    md = '\n'.join(lines).rstrip() + '\n'

    # 今日法拍资产热点（默认必产，调 fetch_paipai_hot.py 获取章节并追加到日报末尾；--no-paipai 关闭）
    if a.paipai_xlsx and not a.no_paipai:
        paipai_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch_paipai_hot.py')
        r = subprocess.run(['python', '-X', 'utf8', paipai_script, 
'--xlsx', a.paipai_xlsx, '--top', '5', '--today', a.date],
                           capture_output=True, text=True, encoding='utf-8')
        if r.returncode == 0 and r.stdout.strip():
            md = md.rstrip() + '\n\n' + r.stdout.strip().rstrip() + '\n'
        else:
            print(f'[paipai] {r.stderr.strip()}')

    # 周/月度情绪汇总追加（--weekly/--monthly 时，调 sentiment_log.py）
    sent_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sentiment_log.py')
    for period, flag in (('weekly', a.weekly), ('monthly', a.monthly)):
        if flag:
            r = subprocess.run(['python', '-X', 'utf8', sent_script, period, '--date', a.date],
                               capture_output=True, text=True, encoding='utf-8')
            if r.returncode == 0 and r.stdout.strip():
                md = md.rstrip() + '\n\n' + r.stdout.strip() + '\n'
            else:
                print(f'[{period}] {r.stderr.strip()}')

    out = a.out or (rf'D:\Administrator\Documents\大瑀MD\深圳资产日报_{today.isoformat()}.md'
                    if a.mode == 'daily'
                    else rf'D:\Administrator\Documents\大瑀MD\深圳资产精选_{today.isoformat()}.md')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(md)
    raw = open(out, 'rb').read()
    print(f'written: {out}')
    print(f'bytes: {len(raw)} | BOM: {raw[:3] == b"\xef\xbb\xbf"} | lines: {raw.count(b"\n") + 1} | items: {len(items)}')

    # 情绪日志 + 跨日去重写入（默认；--no-record 同时跳过两者）
    if not a.no_record:
        cmd = ['python', '-X', 'utf8', sent_script, 'record', '--date', a.date,
               '--index', str(sentiment['index']), '--label', sentiment['label'],
               '--bullish-count', str(sentiment['bullish_count']),
               '--bullish-weight', str(sentiment['bullish_weight']),
               '--neutral-count', str(sentiment['neutral_count']),
               '--neutral-weight', str(sentiment['neutral_weight']),
               '--bearish-count', str(sentiment['bearish_count']),
               '--bearish-weight', str(sentiment['bearish_weight']),
               '--weighted-sum', str(sentiment['weighted_sum']),
               '--total-weight', str(sentiment['total_weight']),
               '--news-count', str(sentiment['news_count'])]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        print(r.stdout.strip() or r.stderr.strip())
        dedup_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dedup.py')
        r = subprocess.run(['python', '-X', 'utf8', dedup_script, 'batch-record',
                            '--input', a.final, '--date', a.date],
                           capture_output=True, text=True, encoding='utf-8')
        print(r.stdout.strip() or r.stderr.strip())


if __name__ == '__main__':
    main()