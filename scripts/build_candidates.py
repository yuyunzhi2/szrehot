# -*- coding: utf-8 -*-
"""szrehot 候选→最终 JSON 样板生成器 — 阶段C入口脚手架

固化 2026-08-12 手动构造 _final.json 的样板逻辑：
  1. 从 _time_verified.json（verify_times.py 产物）读窗口内候选
  2. 合并去重结果（_dedup_result.json，剔除 is_duplicate=true）
  3. 生成 _final.json 模板：每条含 category/summary/sentiment/weight 占位字段
  4. 主模型基于全文逐条填写语义字段（情绪/权重/摘要），填完即为阶段C产物

用法（工作目录 = 技能目录）:
  python -X utf8 scripts/build_candidates.py \
      --verified scripts/_time_verified.json \
      --dedup scripts/_dedup_result.json \
      --output scripts/_final.json \
      [--drop-window-out]   # 默认剔除窗口外；不加则保留并标记

输出每条字段:
  title/url/source/publishedAt(核验后的真实时间)/category(空待填)/
  summary(空待填)/sentiment(空待填)/weight(空待填)/verified_time/window_result

主模型填写规范（见 SKILL.md 阶段C）:
  category: policy|market|urban_renewal|company|research|insights
  sentiment: bullish|bearish|neutral（全小写英文，勿用 bull/bear）
  weight: 1-5；配套/规划利好默认≤2
"""
import argparse
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def main():
    ap = argparse.ArgumentParser(description='候选→最终 JSON 样板生成器')
    ap.add_argument('--verified', required=True, help='verify_times.py 输出（_time_verified.json）')
    ap.add_argument('--dedup', required=True, help='dedup.py batch-check 输出（_dedup_result.json）')
    ap.add_argument('--output', required=True, help='输出 _final.json 路径')
    ap.add_argument('--drop-window-out', action='store_true', default=True,
                    help='剔除窗口外条目（默认开）')
    a = ap.parse_args()

    with open(a.verified, encoding='utf-8') as f:
        verified = json.load(f)
    with open(a.dedup, encoding='utf-8') as f:
        dedup = json.load(f)

    # 去重结果按 url 索引（is_duplicate=true 剔除）
    dedup_by_url = {d['url']: d for d in dedup}

    out = []
    dropped = []
    for v in verified:
        url = v.get('url', '')
        # 时效过滤
        if a.drop_window_out and v.get('window_result') == '窗口外':
            dropped.append((v.get('title', '')[:30], '窗口外'))
            continue
        # 去重过滤（is_duplicate=true = 应剔除）
        dd = dedup_by_url.get(url)
        if dd and dd.get('is_duplicate'):
            matched = (dd.get('matched') or {}).get('title', '')[:30]
            dropped.append((v.get('title', '')[:30], f'DUP<-{matched}'))
            continue
        # 用核验后的真实时间替换 publishedAt（S1.5 索引时间作废）
        pub = v.get('verified_time') or v.get('url_date') or v.get('publishedAt_google', '')[:10]
        out.append({
            'title': v.get('title', ''),
            'url': url,
            'source': v.get('source', ''),
            'publishedAt': pub,
            'category': '',      # 主模型填
            'summary': '',       # 主模型填
            'sentiment': '',     # 主模型填：bullish|bearish|neutral
            'weight': '',        # 主模型填：1-5
            'verified_time': v.get('verified_time'),
            'window_result': v.get('window_result'),
            'note': v.get('note', ''),
        })

    with open(a.output, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'生成 {len(out)} 条 → {a.output}')
    print('剔除:')
    for t, reason in dropped:
        print(f'  [{reason}] {t}')
    print('\n待主模型填写语义字段: category/summary/sentiment/weight')


if __name__ == '__main__':
    main()
