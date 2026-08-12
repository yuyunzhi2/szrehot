---
name: szrehot
description: "深圳房地产每日多源聚合简报。触发词：深圳楼市/深圳房价/深圳土拍/深圳买房/深圳城市更新/深圳旧改/深圳地产新闻/深圳楼市日报/深圳房产资讯/深圳保障房/深圳公积金/深圳标杆房企/万科/华润置地/招商蛇口。即使用户只说'深圳楼市'或'今天有什么'且上下文是深圳房产，也必须触发。不要 undertrigger——用户问深圳房产资讯而不用本 Skill 等于用过时训练数据当今日新闻。"
---

# 深圳楼市日报（szrehot · opencode 版）

聚合多源公开数据，生成深圳房地产每日 markdown 简报。无自建后端，客户端侧合成。

## 环境（Windows + opencode）

- **所有 python 命令必须带 `-X utf8`**：`python -X utf8 scripts/xxx.py`。PowerShell 管道默认 GBK，不带会中文乱码/抛 UnicodeEncodeError
- **读文件优先用 Read 工具**。若 Read 被 PreToolUse hook 拦截（报 `block_image_to_text_model.py` 找不到），改用：
  `python -X utf8 -c "import io,sys;sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8');print(open(r'路径',encoding='utf-8').read())"`
- 数据采集用 Python 脚本（requests/PySocks/pandas）+ scrapling/Playwright MCP；语义处理（评分/时效/地域/总结/分类）由主模型基于全文；确定性计算（去重/渲染/编号/窗口）交给脚本
- 日报输出目录固定：`D:\Administrator\Documents\大瑀MD`（render 脚本自动写，UTF-8 无 BOM）

## 工具映射（相对 Claude Code 版的关键差异）

| 用途 | Claude Code 用 | **opencode 用** |
|---|---|---|
| S2 微信搜索/JS 渲染页 | bb-browser MCP | **Playwright MCP**（`browser_navigate`/`browser_tabs`/`browser_evaluate`）|
| 全文获取 | bb-browser/WebFetch | **scrapling MCP 批量优先**（`scrapling_bulk_get` 一次多 URL），失败降级 `webfetch` → Playwright |
| 并行核验 | bb-browser 多标签 | Playwright `browser_tabs(action=new)` 开多页 + `browser_evaluate` |

## 四阶段流程（每阶段一个明确产物）

### 阶段 A：采集（产物 `scripts/_s1.json`、`scripts/_s15.json`）

1. **S1 RSS（备用源，仅 S1.5 失败时启用）**：`python -X utf8 scripts/fetch_s1.py --json -o scripts/_s1.json`。14 路通用财经媒体几乎不产深圳本地新闻，实测连续两日产出 0-4 条且与 S1.5 完全重复（2026-08-12 弃用为常规源）——**默认跳过不跑**；仅当 S1.5 完全失败（代理不可用/无结果）时启用兜底。路由清单见 `references/sources.md`
2. **S1.5 Google Alerts（主源，必跑）**：`python -X utf8 scripts/fetch_s15.py --json -o scripts/_s15.json`。走 socks5h 代理 `127.0.0.1:10808`；退出码 2 = 代理不可用 → 跳过 S1.5 并在日报注明（排查见 `references/sources.md`）。**S1.5 通过 Google 索引聚合大量微信公众号文章，S2 缺席时实质覆盖微信内容**
3. **S2 微信（可选）**：Playwright MCP 操作搜狗微信搜索，操作细节与 `var ct` 核验见 `references/weixin-playwright.md`。**不可用时降级链**：搜狗 → 从 `_s15.json` 筛 `mp.weixin.qq.com` URL → 新榜 → 微信搜一搜；全缺则注明「S2 微信渠道未收录」，不阻塞

### 阶段 B：核验 + 过滤（产物 `scripts/_pre_cands.json` → `scripts/_candidates.json` → `scripts/_dedup_result.json`）

4. **三源合并 + 关键词筛选**：S1/S1.5 从脚本 JSON 读取，S2 由主模型整理，统一字段 `title/url/source/publishedAt`。先做关键词筛选（剔除楼盘详情页软文/中介软文/纯股价噪音，仅留市场相关），写入 `scripts/_pre_cands.json`（可含重复多稿，去重在后）
5. **真实发布时间核验（必须先于全文抓取，防索引时间陷阱）**：`python -X utf8 scripts/verify_times.py --input scripts/_pre_cands.json --window-start {窗口起点} --output scripts/_time_verified.json`。**S1.5 的 publishedAt 是 Google 索引时间，非文章真实发布时间**（实测差 1-8 天），不得用其做时效初筛。脚本自动完成：① URL 路径日期秒级初筛（财新 `/2026-08-03/`、新浪 `/2026-08-01/`、同花顺 `/20260802/`），窗口外直接丢弃不开页；② 仅对需开页者批量抓取，提取 meta/正文/微信 `var ct` 时间戳（Unix 秒 → 北京时间）；③ 输出 `_time_verified.json` 结果表（`window_result: 窗口内|窗口外|时间未知` + `verified_time`）。**顺序强制：先 URL 初筛再抓全文**——2026-08-11 踩坑：先全量抓 27 URL 再核验，窗口外条目白抓。脚本核验不了的（SSL 失败/动态渲染页）降级 `webfetch`/Playwright，本次 lg.gov.cn 用 webfetch 解决。主模型基于结果表复核差异项（如正文提取到非发布时间）
6. **时效审核（硬性门槛，不可软化）**：仅保留发布时间在**窗口起点 0:00 ~ 执行当前时间**（北京时间）内条目。窗口起点：执行日为周一 → 上周五 0:00（跨周末）；其余执行日 → 前一天 0:00。逐条核实，窗口外直接丢弃，**不得通过评分降权变相保留**。无发布时间且全文失败 → 丢弃；全文成功 → 保留标注「时间未知」排末尾
7. **批量全文获取（在时效初筛后进行）**：对窗口内候选，scrapling `scrapling_bulk_get` 一次传全部 URL；失败条目降级 `webfetch`。仍失败标 `fullText: null`，仅保留摘要并标注「⚠️ 摘要来源：未获取全文」
8. **地域审核（硬性门槛）**：主体不在深圳的新闻丢弃。原文未提及深圳时不得虚构关联
9. **跨源去重**：`python -X utf8 scripts/dedup.py batch-check --input scripts/_candidates.json --date {今天} --output scripts/_dedup_result.json`。剔除 `is_duplicate=true` 的条目。**DUP 语义（易误解）**：`is_duplicate=true` = 该条与已存在条目重复，**应剔除**，不是"已收录所以保留"。批内重复只留先出现的一条；跨日重复（`index_*`）默认剔除，除非本次是重新生成当日日报且该条重新核验后仍在窗口内。**五轨机制**见 `references/classification.md`：
    - 轨1 URL 指纹 / 轨2 标题 2-gram / 轨3 实体组合（公司+区+事件+类型，含条件4：共同公司+共同类型判重）/ 轨4 结构化标题 / **轨5 实体指纹（2026-08-12 新增）**
    - **轨5 实体指纹**：解决"同一事件不同媒体标题"（深大购楼 4 稿、香蜜湖宅地 3 稿标题差异极大，轨2/3/4 全漏，过去靠主模型人工合并）。机制：① 金额/数量指纹归一化（`5.3亿→(亿,5)`、`5.336亿→(亿,5)`，带小数金额=强信号直接判重，整数金额需实体上下文佐证）；② 标题 6-gram Jaccard ≥0.35（专有名词级重合，如"红山中学民治校区"）。**主模型仍应人工复核去重结果**——指纹轨是辅助，极端改写标题（如每经"深圳大学打算买一整栋楼做学生宿舍" vs 新浪"深大再出手…5.3亿…"）靠轨3条件4（深大≡深大 + 宿舍类型）
    去重结果即阶段C样板生成器（步骤10）的输入，无需再存 _checked.json

### 阶段 C：语义产出（产物 `scripts/_final.json`）

10. **样板生成 → 语义分类 + 权重 + 摘要**：
    - 先跑样板生成器（固化去重+时效过滤逻辑，产出含占位字段的 `_final.json` 模板）：`python -X utf8 scripts/build_candidates.py --verified scripts/_time_verified.json --dedup scripts/_dedup_result.json --output scripts/_final.json`。脚本自动：剔除窗口外条目 + 剔除 `is_duplicate=true` 条目 + 用核验后的真实时间替换 S1.5 索引时间。**注意执行顺序：batch-check（步骤9）必须在 render batch-record（步骤12）之前**——否则索引已有当日记录，20 条候选会全部被误标跨日 DUP（2026-08-12 测试踩坑）
    - 然后主模型基于全文语义对每条填写：`category`（policy/market/urban_renewal/company/research/insights，映射表见 `references/classification.md`）、`summary`（50-100 字）、`sentiment`（情绪标签三选一）、`weight`（影响权重 1-5）。存为 `scripts/_final.json`
    - **情绪标签**（三选一，基于全文语义明确判断，不得偷懒全标中性）：
      - 🔴 **看涨**（+1）：政策松绑/利好、成交量价上升、土地高溢价、企业盈利扩张、保障房供给增加（购房选择面扩大）
      - 🟢 **看跌**（−1）：政策收紧/利空、成交量价下跌、土地流拍/低溢价、企业亏损暴雷、供应过剩信号
      - ⚪ **中性**（0）：纯信息发布无方向性、政策征求意见未定、市场分化涨跌互现、规划调整无明确利好利空
      - **⚠️ JSON 枚举值**：`_final.json` 的 `sentiment` 字段必须写 **`bullish`/`bearish`/`neutral`**（全小写英文）。`render_report.py` 已支持容错别名（bull/bear/中文/图标等自动归一，未知值警告回退中性），但**不要依赖容错**——写错枚举是 2026-08-12 实测返工案例（全中性指数 50.0 → 修复后 82.8）
      - **⚠️ 配套/规划利好降权**：单项目/配套类利好（学校完工、地铁/铁路推进、产业园区奠基、企业总部落地）默认权重 **≤2**，标 🔴看涨必须能在摘要中写出明确的价格/成交传导路径（如「利好北站片区居住配套 → 在售项目去化」）；写不出传导路径的配套信息标 ⚪中性。防止看涨通胀（2026-08-12 实测 7 涨/1 跌却指数 82.8 的教训）
    - **影响权重**（1-5 级，决定该条对指数与精选模式的杠杆）：
      - ★★★★★（5）：市级及以上重大政策/数据（限购松绑、公积金新政、全市月度成交数据）
      - ★★★★（4）：区级政策或区域级数据（区级保障房供应、核心区土拍、重点旧改规划调整）
      - ★★★（3）：行业趋势/全国政策深圳关联（全国公积金条例修订、多城楼市趋势分析）
      - ★★（2）：单项目/局部动态（单个楼盘清盘、非核心区租金数据）
      - ★（1）：边缘关联/信息性（网友问答、一般租赁招租）
11. **大宗租赁**（日报末尾必产章节，独立于新闻管道）：`python -X utf8 scripts/fetch_residential_projects.py --cutoff "{窗口起点} 00:00:00" --cutoff-end "{今天} {当前HH:MM:SS}"`（工作目录=技能目录）。**必须显式传窗口且与步骤 6 完全一致**：`--cutoff` = 窗口起点 00:00:00（周一=上周五），`--cutoff-end` = 执行当下时刻（**非 23:59:59**，否则上午执行会纳入下午才出现的项目）。脚本失败/无数据 → 章节注明「今日阳光租赁平台无新增大宗招租项目」，不得编造。脚本输出到 \outputs/{窗口起点日期}/\ 目录（xlsx + md + 核对清单），记下 md 路径给阶段 D

### 阶段 D：渲染 + 归档（产物：日报 md + 跨日索引）

12. **一键渲染**：
    `python -X utf8 scripts/render_report.py --final scripts/_final.json --date {今天} --rentals "{租md路径}" --rentals-summary "{3-5句租赁摘要：总数/三类分布/区域分布/2-3个亮点项目}"`
    脚本自动完成：6 章节分组 + 贯穿全文编号 + **情绪指数聚合**（公式 `50 + 50×Σ(情绪值×权重)/Σ权重`，0=极度看跌，50=中性，100=极度看涨）+ 头部「当日市场情绪指数」章节 + 条目尾部 `🔴看涨 ★★★★` 格式 + 租赁表格从 md 提取 + UTF-8 无 BOM 写入 `D:\Administrator\Documents\大瑀MD\深圳楼市日报_{date}.md` + 调 `dedup.py batch-record` 写入跨日索引 + 调 `sentiment_log.py record` 写入当日情绪日志（同日覆盖）
    - 精选模式加 `--mode jingxuan`（文件名 `深圳楼市精选_`，仅含 **情绪≠中性 且 权重≥3** 的条目，指数基于精选子集）
    - **周一运行加 `--weekly`、每月1日运行加 `--monthly`**：自动追加周/月度情绪汇总到日报末尾（数据源 `D:\Administrator\Documents\大瑀MD\sentiment_log.jsonl`，按新闻数加权平均，无数据日期自动跳过）
13. **对话输出**：把渲染出的 md 内容**原样**粘贴到对话回复中（不缩写不重写），告知保存路径。分类空时注明「今日暂无更新」。发布时间统一「M月D日」格式，不用相对词

### 阶段四：周度/月度情绪汇总（条件触发）

14. **周度汇总**：周一运行时，在步骤 12 的命令加 `--weekly`。render 自动调 `sentiment_log.py weekly` 取上周一~上周日的每日指数，按新闻数加权平均（`Σ(日index×日newsCount) / Σ(日newsCount)`），汇总表追加在日报「大宗租赁关注」之后
15. **月度汇总**：每月1日运行时，在步骤 12 的命令加 `--monthly`。同上取上月1日~上月末，公式相同。同一天同时满足周一和1日时两个开关都加
    - 汇总表也可独立生成：`python -X utf8 scripts/sentiment_log.py weekly|monthly [--json]`
    - **日志数据只来自真实 render 流程**（`sentiment_log.py record`），不得手动填充 sentiment_log.jsonl

## 本次执行踩坑速查（2026-08-12 更新，直接照抄规避）

| 坑 | 规避 |
|---|---|
| S1 RSS 全旧闻、贡献 0 | 辅助源，0 条直接跳过，别花时间修源 |
| S2 微信源不可用 | opencode 用 Playwright MCP；仍不可用 → 降级 S1.5 微信 URL → 注明缺席，不阻塞流程 |
| PowerShell 中文乱码 | 一切 python 命令带 `-X utf8` |
| Read 被 hook 拦截（`$HOME` 未展开）| 用 `python -X utf8 -c "print(open(...).read())"` 读文件 |
| Google Alerts publishedAt 是索引时间 | 必须 URL 路径日期或全文核验真实时间；融创 07-29 实例：索引时间在窗口内但真实发布时间在窗口外 |
| 搜狗时间词精确到月 | 只用「YYYY年」级；「2026年8月」被字面过滤致结果 1 条旧文 |
| 微信文章搜索不支持时间排序 | `sort=1` 对 type=2 无效，按相关性浏览前 2 页 |
| 微信中间链接（`src=11`/`weixin.sogou.com/link`）时间不可信 | 必须 `browser_evaluate` 核验 `var ct` 时间戳 |
| dedup DUP 标记者 | DUP = 应剔除，不是已收录 |
| 重新生成当日日报 | 旧日报条目只作候选，必须重走 B 阶段核验，不得直接沿用 |
| 租赁脚本默认 cutoff | 必须显式传 `--cutoff {窗口起点} 00:00:00 --cutoff-end {今天当前HH:MM:SS}`，否则拉回历史项目污染章节 |
| 手动编号/组装日报 | 全部交给 `render_report.py`，主模型只提供语义字段 |
| 情绪分类偷懒全标中性 | 每条必须基于全文语义明确判断看涨/看跌/中性，纯信息发布才标中性 |
| 情绪指数手算 | 全部交给 `render_report.py` 聚合（公式 50+50×Σ(val×w)/Σw），主模型只提供 sentiment+weight |
| 租赁项目误计入情绪指数 | 租赁章独立于新闻管道，不参与情绪聚合 |
| sentiment 枚举写错（bull/bear vs bullish/bearish） | 2026-08-12 实测：`_final.json` 写 `bull` 导致渲染全部静默回退中性（指数 50.0），修复后 82.8。必须写 `bullish/bearish/neutral`；render 已做容错别名映射+未知值警告，但别依赖容错 |
| 先全量抓全文再核验时间（窗口外白抓） | 2026-08-12 实测：先 scrapling 抓 27 URL 再核验，窗口外条目白抓。顺序强制：先 `verify_times.py` URL 初筛（秒级丢弃窗口外）→ 再对窗口内批量抓全文 |
| 跨媒体同事件 dedup 四轨全漏 | 2026-08-12 实测：深大购楼 4 稿/香蜜湖宅地 3 稿/红山中学 2 稿标题差异极大，轨2/3/4 识别不到。新增轨5 实体指纹（金额归一化+6-gram）；主模型仍人工复核 |
| batch-check 在 render 之后跑全 DUP | 2026-08-12 测试踩坑：render 的 batch-record 已把当日条目写入索引，之后对同日候选 batch-check 全标跨日 DUP。顺序：batch-check（阶段B）→ render batch-record（阶段D），不可颠倒 |
| szhome 页面 copyright 在正文时间前 | 2026-08-12 修复：页面骨架 JS 的 copyright/版权在时间之前，全局截断会掐掉真实时间。verify_times.py 改为匹配位置局部噪音检查 |
| 配套/规划利好全标看涨致指数虚高 | 2026-08-12 实测：7 涨/1 跌却指数 82.8（极度看涨）。配套利好（学校/铁路/园区奠基）默认权重≤2，标 bullish 需写出价格/成交传导路径 |
| 租赁脚本输出堆积技能根目录 | 2026-08-12 修复：输出改到 `outputs/{窗口起点日期}/`，按日隔离，gitignore 已排除 |
## 流程执行红线

- **时效审核是硬性门槛**：不得以评分降权、手动拼接、遗漏检查等方式让窗口外条目进入最终日报
- **去重必须覆盖所有来源**：S1+S1.5+S2 合并后统一 batch-check，不存在「某来源单独免检」
- **执行顺序不可调换**：先时效/地域审核（硬性拦截）→ 再去重（软性合并）→ 最后情绪分类（排序输出）
- **微信条目时间核验后才能进时效审核**：搜狗中间链接 publishedAt 不可信，先核验 `var ct` 再判时效
- **重新生成当日日报 = 完整重跑管道**：沿用旧日报条目而不重新核验 = 严重违规
- **手动拼接候选池**：禁止跳过管道手动编写 articles 列表

## 模式

默认日报模式（S1+S1.5+S2）。用户说「精选/快速」走精选模式（`--mode jingxuan`，仅含 **情绪≠中性 且 权重≥3** 的条目，指数基于精选子集），「全部/全量」走全量模式。**模式只影响新闻章节筛选，「大宗租赁关注」章所有模式下都正常产出**。

## 输出格式

日报 markdown，编号贯穿全文（不在每个 ## 内重新计数）。头部必含「当日市场情绪指数」章节（综合指数 + 看涨/中性/看跌条目数与权重合计 + 加权情绪和 + 总权重 + 解读），由 render 脚本自动聚合；每条新闻尾部为 `M月D日 | 🔴看涨 ★★★★`（情绪图标 + 星级权重）。完整格式示例见 render 脚本实际输出，主模型不得手写该章节。

## References

- `references/sources.md`：RSS 路由清单 + Google Alerts Feed + 代理排查（curl schannel 误判历史）
- `references/classification.md`：六类分类规则 + 地域关键词 + 结构化标题去重（轨4）
- `references/weixin-playwright.md`：S2 搜狗微信 Playwright 操作 + `var ct` 核验脚本
- `scripts/sentiment_log.py`：情绪指数日志（record/weekly/monthly/show/prune），数据存 `D:\Administrator\Documents\大瑀MD\sentiment_log.jsonl`

## 不要做

- 不要编造内容 — 永远以数据源返回为准；数据源全挂时告知「当前数据源暂不可用」
- 不要收录非深圳新闻 / 不要脑补深圳关联
- 不要跳过全文获取 — 仅用 RSS 标题+摘要输出是严重质量缺陷
- 不要跳过时效审核 / 地域审核 / 跨源去重
- 不要把 dedup 标 DUP 的条目当「已收录可保留」
- 不要沿用旧日报条目直接输出
- 不要用精确到月的时间词搜搜狗微信
- 不要漏掉「大宗租赁关注」章 — 它是日报末尾必产的第 7 章
- 不要用脚本默认 cutoff 生成租赁章
- 不要手动改写租赁表格面积/底价数值 — 以脚本 md 为准，render 自动提取
- 不要把租赁项目混入新闻去重管道 — 独立运行
- 不要在日报输出中暴露基础设施 — 不写数据源名称/curl 命令/feed URL/API 端点/工具名（对话中可提，日报正文不写）
- 不要漏掉每条的 URL — 用户需要追溯原文
- 不要输出不可点击链接 — 微信文章标注「微信公众号」作为来源
- 不要用旧的双视角评分格式 — 每条新闻用「🔴看涨/🟢看跌/⚪中性 + ★权重」替代从业者评分和购房者评分（render 自动输出）
- 不要在情绪分类时偷懒全部标中性 — 每条必须基于全文语义做出明确判断，除非确实无方向性
- 不要漏掉「当日市场情绪指数」章节 — 日报头部必产内容，render 自动聚合，主模型不得手写或删除
- 不要把租赁项目计入情绪指数 — 大宗租赁关注章独立于新闻管道，不参与情绪聚合
- 不要伪造历史情绪数据 — `sentiment_log.jsonl` 记录必须来自真实 render 流程（`sentiment_log.py record`），不得手动填充
