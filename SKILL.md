---
name: szrehot
description: "深圳房地产每日多源聚合简报。触发词：深圳楼市/深圳房价/深圳土拍/深圳买房/深圳城市更新/深圳旧改/深圳地产新闻/深圳楼市日报/深圳房产资讯/深圳保障房/深圳公积金/深圳标杆房企/万科/华润置地/招商蛇口。即使用户只说'深圳楼市'或'今天有什么'且上下文是深圳房产，也必须触发。不要 undertrigger——用户问深圳房产资讯而不用本 Skill 等于用过时训练数据当今日新闻。"
---

# 深圳资产日报（szrehot · opencode 版）

> 聚合多源公开数据，生成深圳房地产每日 markdown 简报。无自建后端，客户端侧合成。

## 执行纪律（2026-10-22 复盘新增，违反直接导致返工/超时）

1. **开工先读一份成功的先例**（上一份日报的链接格式/章节结构/摘要粒度），直接对齐，不重新发明
2. **跨日去重不能只靠 `dedup.py batch-check`**——必须人工打开前日日报核对关键数字（标题全不同的同源改写五轨会全漏）
3. **跨日索引扫描前移到写摘要之前**（否则要回退 `dedup_index.jsonl`+`sentiment_log.jsonl` 并重渲染）
4. **任何子目标最多 2 次尝试**，失败即取"能用的近似"并注明限制后推进（例：搜狗微信签名链接即满足"可点击 mp.weixin.qq.com 链接"要求，**不追永久链接**——`sn` 为空拼不出来）
5. **只用脚本文件，不在 PowerShell 里内联 python**（含 `['"]` 的正则必被 shell 解析破坏）
6. **临时调试脚本用完即删**，不留 `scripts/` 目录

## 何时触发

用户提及上述触发词，或上下文为深圳房产资讯时**必触发**。仅解释/翻译/总结单篇时不触发（`Do not create a skill` 情形）。

## 执行契约

- **owner** `yuyunzhi2` · 月审 · **file-backed fixture** `D:\Desktop\法拍数据.xlsx` + `scripts/_*.json`
- **output contract** `大瑀MD/深圳资产日报_{date}.md` + `outputs/{窗口起点}/` + `sentiment_log.jsonl` + 周一加 `大瑀MD/深圳资产周报_{上周一起}_{上周日}.md`
- **rollback** `render --no-record` 可重渲染；`missing evidence` 无遥测，仅本地文件为凭

## 环境

- 所有 `python` 带 `-X utf8`；采集 `scrapling_bulk_get`→`webfetch`→`Playwright MCP`
- 语义→主模型，确定性→脚本；hook 拦截时用 `python -X utf8 -c "print(open(...).read())"`
- 阿里法拍浏览器固定用 ShadowBot：`"D:\Program Files\ShadowBot\ShadowBot Browser\Application\ShadowBotBrowser.exe" --remote-debugging-port=9222 --user-data-dir=C:\ChromeDebug`（`scripts/start_chrome_debug.bat` 一键启动，**禁 taskkill 用户主浏览器**）；淘宝登录态存 `C:\ChromeDebug` 跨 run 复用，失效时从 `%LOCALAPPDATA%\ShadowBotBrowser\User Data` 拷登录文件（robocopy 排除缓存/锁文件）或弹窗扫码一次

## 五阶段骨架（周一加 E 周报）

> 详表与参数见 `references/pipeline.md`；踩坑对照见 `references/troubleshooting.md`

1. **A 采集** → `scripts/_s1.json`, `scripts/_s15.json` · S1 RSS 默认跳过（14 路财经媒体贡献≈0），S1.5 Google Alerts 必跑（`socks5h://127.0.0.1:10808`），S2 微信可选（搜狗→S1.5筛`mp.weixin.qq.com`→新榜→微信搜一搜）
2. **B 核验+过滤** → `_pre_cands→_candidates→_dedup_result` · URL 解包→`verify_times`路径初筛→窗口硬门槛→`scrapling_bulk_get`→地域→`dedup batch-check`五轨 · **主模型必人工复核去重结果**（同事件改写标题五轨易漏：跨日同源改写/同数据多口径须手工剔除）；`verify_times` 误取正文内日期（如把优惠截止日当发布时间）时用 `webfetch` 复核纠正
3. **C 语义产出** → `scripts/_final.json` · `build_candidates.py` 生成占位模板 → 主模型填 `category/summary/sentiment/weight`
4. **D 渲染+归档** → 日报 md + 跨日索引
   - `render_report.py --final --date --rentals --rentals-summary [--paipai-xlsx --today --mode jingxuan --weekly --monthly --no-record]`
   - 租赁 `fetch_residential_projects.py --cutoff {窗口起点} 00:00:00 --cutoff-end {今天 HH:MM:SS}`（必显式传窗口；同楼/同项目多宗合并见下）
   - 法拍 `fetch_paipai_hot.py`：TOP5（围观降序，**拍卖开始时间>执行日**硬过滤）+ 2000万汇总（见下）· 面积链：ShadowBot（CDP 9222）页面直读→附件查档单 PDF 自动解析（单单元取值/多单元求和）→`scripts/_paipai_area_store.json` 落盘复用（命中即零开页）；直读前分步滚到底触发懒加载（首屏无面积≠附件型）；扫描图PDF转PNG多模态直读（仍无才留「-」），**面积/单价一律脚本重算、禁手填**
5. **E 周报（仅周一，日报 D 完成后必执行）** → 周报 md · 输入上周一~周日 6 份日报 → 正文跨日去重（同事件/多口径/政策链合并）→ 租赁跨日去重合并表 + 法拍2000万全量合并 + 超千万排序（TOP5 明细不展开）→ 落盘 + 对话原样粘贴；详规 `references/weekly.md`

### 法拍2000万汇总（2026-08-31 起，2026-09-02 起阈值由1亿调整为2000万）

- 阈值 `2000万`=2000万元，统计当日块全部可解析行；归一截断首个栋/座/房/室及后房号（如 `岗厦皇庭大厦A座15房→岗厦皇庭大厦`），单宗或同址合计≥阈值入选；**若为同址多宗合计≥2000万，输出必须以汇总行形式呈现（基准地址 | 宗数 | 起拍价合计 | 评估价合计 | 折价率 | 建筑面积合计 | 单价 | 拍卖开始时间 | 涉及物业明细），不得拆分为单宗罗列**
- 汇总行含**聚合计算**：起拍价合计（Σ各宗起拍价）、评估价合计（Σ各宗评估价）、整体折价率（起拍合计÷评估合计）、建筑面积合计（Σ已提取面积；缺失显示`-`）、整体单价（起拍合计÷面积合计，缺失显示`-`）；起拍价缺失的成员不计入起拍合计
- **拍卖开始时间**：组内全部同日期 → 单值（`10月08日`）；跨日期 → 日期区间（最早-最晚，如 `10月08日-10月12日`）
- **面积字段兼容「测绘面积」**（2026-09-02 实测）：部分整栋/私宅标的详情页用「测绘面积 X ㎡」而非「建筑面积 X ㎡」（另有「占地面积」为土地面积不应取），`_AREA_RE` 已兼容 `(?:建筑面积|测绘面积)`

### 租赁同楼/同项目多宗合并（2026-09-04 新增）

- 深圳阳光租赁平台大宗物业提取时，同一栋楼（归一基准地址相同）或同一项目（projectName 相同）的多宗标的**视为一宗**，md 表格以汇总行呈现，**不得拆分为单宗罗列**；归一规则参考法拍2000万：去「广东省/深圳市」前缀后截断首个栋/座/幢/楼/层/房/室标识及其后房号（如 `广东省深圳市宝安区石街大厦8楼` → `宝安区石街大厦`），过短(<4字)回退原地址；**不截断「号」街道门牌**（不同门牌=不同楼栋，不得误并）；再按 (基准地址 | 项目名) 连通归并
- 表格列 `地址 | 宗数 | 面积(㎡) | 底价(元/㎡/月) | 用途`；合并聚合：面积=Σ各宗面积（全缺失`-`）、底价=全部同价→单值 / 有差异→区间（低-高）/ 含无法解析→去重原文（`<br>`连接）、用途=去重合并；单宗行宗数=1 原样呈现
- 逐字重复的标的先按（项目+地址+面积+底价+用途）去重——同项目被多用途类别重复命中（如青湖山庄青泉苑同时命中办公/商业）或跨项目重复公告，不得重复计入宗数
- 头部计数行「共 N 个项目, M 宗（合并自 K 个标的）」；xlsx 数据文件仍保留全部明细行（项目/标的粒度），md 为合并后宗粒度

## 工具映射

- 微信/JS `Playwright MCP`，全文 `scrapling_bulk_get`→`webfetch`→Playwright
- 阿里法拍面积 `ShadowBot`（CDP 127.0.0.1:9222，登录态复用；页面无面积→附件 PDF→store），京东走公开接口

## 输出

- 编号贯穿全文，头部 `情绪指数=50+50×Σ(val×w)/Σw`，尾部 `M月D日|🔴看涨★★★★`
- 表格上方 `<p align="center">左右滑动查看表格</p>`，单位入表头；详规 `references/output-spec.md`

## 红线

- 窗口外丢弃不降权；去重覆盖全源；顺序 时效/地域→去重→情绪；`var ct`后判时效；重跑=重走管道

## References

- `references/pipeline.md` 15步流水线（含2000万分组）
- `references/weekly.md` 周一E周报流程（跨日去重+合并表+五节结构）
- `references/troubleshooting.md` 踩坑速查
- `references/output-spec.md` 表格与法拍经验
- `references/classification.md` 六类+五轨
- `references/sources.md` · `references/weixin-playwright.md`
- `scripts/fetch_paipai_hot.py` · `scripts/parse_paipai_pdf_areas.py` · `scripts/_paipai_area_store.json` · `scripts/sentiment_log.py`
- `evals/trigger_eval.py` 触发评测 · `reports/output_quality_scorecard.md` 质检

## 不要做

- 编造/脑补深圳关联；跳过全文仅用标题摘要
- 跳过时效/地域/去重；把 `is_duplicate=true` 当保留；把五轨漏标的同事件改写（跨日同源/同数据多口径）当新增
- 用脚本默认 cutoff；手改租赁面积/底价
- 手写/改写租赁与法拍表格（含折价率/单价重算）；法拍 TOP5 混入已开拍；向 stdout 写非交付日志（`file=sys.stderr`）
- 暴露基础设施（feed/API/工具名）在日报正文；漏 URL/不可点击链接
- 情绪全标中性或手写情绪指数；把租赁计入指数；伪造 `sentiment_log.jsonl`
- 周报展开每日法拍TOP5全量（约60行）或重算情绪/改写日报表格数值；脑补旧日报缺失列

