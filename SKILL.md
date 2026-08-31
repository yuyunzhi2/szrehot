---
name: szrehot
description: "深圳房地产每日多源聚合简报。触发词：深圳楼市/深圳房价/深圳土拍/深圳买房/深圳城市更新/深圳旧改/深圳地产新闻/深圳楼市日报/深圳房产资讯/深圳保障房/深圳公积金/深圳标杆房企/万科/华润置地/招商蛇口。即使用户只说'深圳楼市'或'今天有什么'且上下文是深圳房产，也必须触发。不要 undertrigger——用户问深圳房产资讯而不用本 Skill 等于用过时训练数据当今日新闻。"
---

# 深圳资产日报（szrehot · opencode 版）

> 聚合多源公开数据，生成深圳房地产每日 markdown 简报。无自建后端，客户端侧合成。

## 何时触发

用户提及上述触发词，或上下文为深圳房产资讯时**必触发**。仅解释/翻译/总结单篇时不触发（`Do not create a skill` 情形）。

## 执行契约

- **owner** `yuyunzhi2` · 月审 · **file-backed fixture** `D:\Desktop\法拍数据.xlsx` + `scripts/_*.json`
- **output contract** `大瑀MD/深圳资产日报_{date}.md` + `outputs/{窗口起点}/` + `sentiment_log.jsonl`
- **rollback** `render --no-record` 可重渲染；`missing evidence` 无遥测，仅本地文件为凭

## 环境

- 所有 `python` 带 `-X utf8`；采集 `scrapling_bulk_get`→`webfetch`→`Playwright MCP`
- 语义→主模型，确定性→脚本；hook 拦截时用 `python -X utf8 -c "print(open(...).read())"`

## 四阶段骨架（每阶段一产物）

> 详表与参数见 `references/pipeline.md`；踩坑对照见 `references/troubleshooting.md`

1. **A 采集** → `scripts/_s1.json`, `scripts/_s15.json` · S1 RSS 默认跳过（14 路财经媒体贡献≈0），S1.5 Google Alerts 必跑（`socks5h://127.0.0.1:10808`），S2 微信可选（搜狗→S1.5筛`mp.weixin.qq.com`→新榜→微信搜一搜）
2. **B 核验+过滤** → `_pre_cands→_candidates→_dedup_result` · URL 解包→`verify_times`路径初筛→窗口硬门槛→`scrapling_bulk_get`→地域→`dedup batch-check`五轨
3. **C 语义产出** → `scripts/_final.json` · `build_candidates.py` 生成占位模板 → 主模型填 `category/summary/sentiment/weight`
4. **D 渲染+归档** → 日报 md + 跨日索引
   - `render_report.py --final --date --rentals --rentals-summary [--paipai-xlsx --today --mode jingxuan --weekly --monthly --no-record]`
   - 租赁 `fetch_residential_projects.py --cutoff {窗口起点} 00:00:00 --cutoff-end {今天 HH:MM:SS}`（必显式传窗口）
   - 法拍 `fetch_paipai_hot.py`：TOP5（围观降序，**拍卖开始时间>执行日**硬过滤）+ 亿元汇总（见下）

### 法拍亿元（2026-08-31）

- 阈值 `10000万`=1亿，统计当日块全部可解析行；归一截断首个栋/座/房/室及后房号（如 `岗厦皇庭大厦A座15房→岗厦皇庭大厦`），单宗或同址合计≥阈值入选

## 工具映射

- 微信/JS `Playwright MCP`，全文 `scrapling_bulk_get`→`webfetch`→Playwright

## 输出

- 编号贯穿全文，头部 `情绪指数=50+50×Σ(val×w)/Σw`，尾部 `M月D日|🔴看涨★★★★`
- 表格上方 `<p align="center">左右滑动查看表格</p>`，单位入表头；详规 `references/output-spec.md`

## 红线

- 窗口外丢弃不降权；去重覆盖全源；顺序 时效/地域→去重→情绪；`var ct`后判时效；重跑=重走管道

## References

- `references/pipeline.md` 15步流水线（含亿元分组）
- `references/troubleshooting.md` 踩坑速查
- `references/output-spec.md` 表格与法拍经验
- `references/classification.md` 六类+五轨
- `references/sources.md` · `references/weixin-playwright.md`
- `scripts/fetch_paipai_hot.py` · `scripts/sentiment_log.py`
- `evals/trigger_eval.py` 触发评测 · `reports/output_quality_scorecard.md` 质检

## 不要做

- 编造/脑补深圳关联；跳过全文仅用标题摘要
- 跳过时效/地域/去重；把 `is_duplicate=true` 当保留
- 用脚本默认 cutoff；手改租赁面积/底价
- 手写/改写租赁与法拍表格（含折价率/单价重算）；法拍 TOP5 混入已开拍；向 stdout 写非交付日志（`file=sys.stderr`）
- 暴露基础设施（feed/API/工具名）在日报正文；漏 URL/不可点击链接
- 情绪全标中性或手写情绪指数；把租赁计入指数；伪造 `sentiment_log.jsonl`

