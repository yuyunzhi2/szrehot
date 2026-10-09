# output_quality_scorecard — szrehot（2026-08-31）

> file-backed fixture: D:\Desktop\法拍数据.xlsx + scripts/_*.json
> output contract: D:\Administrator\Documents\大瑀MD\深圳资产日报_{date}.md
> rollback boundary: batch-record + sentiment_log record 皆为幂等追加，--no-record 可重渲染

## 维度

- 完整性：6 章节 + 头部情绪指数 + 租赁 + 法拍 TOP5 + 2000万子表 + 周/月汇总（条件）均必产，缺章节即扣分
- 准确性：时效窗口硬过滤、地域硬过滤、五轨去重、拍卖开始时间>执行日、2000万阈值2000万元归一分组
- 可追溯：每条必带 URL，点击链完整；租赁/法拍数值以脚本 md 为准，禁止手写
- 格式：编号贯穿全文、表头单位、数值纯数字、滑动提示行、UTF-8无BOM
- 幂等：重渲染必带 --no-record，否则重复写索引/日志

## 抽检（missing evidence 标注）

- 历史 2026-08-19 案例：TOP5 无年份时间导致空说明（已修复 default_year+after 跨年）
- 阿里面积依赖 9222 运行时（missing evidence: 无自动化 E2E 回放）
- 2000万归一：岗厦皇庭大厦A/B座 合计≥2000万 入选已单测，未做全量 xlsx 回放（missing evidence: 全量法拍 xlsx 缺省 fixture）

## 风险

- telemetry/metrics/benchmarks: missing evidence（本地离线 skill）
- trust report: 见 reports/trust_report.md（待补）
