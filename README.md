<p align="center">
  <strong>szrehot</strong><br/>
  <em>深圳房地产市场热点资讯 — 一句话拿深圳楼市简报</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue" alt="version"/>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license"/>
  <img src="https://img.shields.io/badge/data_sources-11_RSS_%2B_%E6%90%9C%E7%8B%97%E5%BE%AE%E4%BF%A1_%2B_Codex%E6%B5%8F%E8%A7%88%E5%99%A8-orange" alt="sources"/>
  <img src="https://img.shields.io/badge/zero_backend-✓-success" alt="zero backend"/>
  <img src="https://img.shields.io/badge/zero_API_Key-✓-success" alt="zero api key"/>
</p>

<p align="center">
  <strong>灵感来源 <a href="https://github.com/virxcase/aihot">AIHOT</a>（AI 圈） → <a href="https://github.com/zycyyyya/finhot">finhot</a>（金融保险圈） → 深圳房地产圈版</strong>
</p>

---

## 它是什么

szrehot 是一个 Agent Skill（SKILL.md 格式），安装后你可以直接对 AI 说：

> *"深圳楼市有什么"* → 自动拉取最新资讯，分类输出简报
> *"深圳房价"* → 多源聚合，生成分版块的日报
> *"深圳城市更新"* → 搜狗微信搜索 + RSS 聚合城市更新动态

**零后端部署、零 API Key（Tavily 可选兜底）。** Skill 直接在客户端调多个公开数据源，聚合后输出中文 Markdown。

## 架构

```
用户自然语言提问
        │
        ▼
  ┌─ S0 权威原始源 ───────── 住建局/规自委（Codex 浏览器抓取）
  │
  ├─ S1 RSS 聚合 ────────── 财经媒体 + 百度搜索（11+ 实测可用路由）
  │
  ├─ S2 搜狗微信搜索 ─────── 深圳房产公众号（Codex 浏览器 + Tavily 兜底）
  │
  └─ S3 Codex 浏览器直抓 ── 贝壳深圳/乐居深圳/房天下深圳
        │
        ▼
  客户端侧：去重 → 六类分类 → 双视角评分 → Markdown 简报
```

## 六类分类

| 分类 | 覆盖内容 |
|:---:|---|
| 🏛 政策法规 | 限购/限贷/公积金/保障房/预售监管/土拍规则/城市更新条例 |
| 📊 市场行情 | 房价走势/成交数据/土拍结果/二手房挂牌/租金/区域热度 |
| 🏗 城市更新 | 旧改项目/棚改/拆除重建/综合整治/旧村改造/工业上楼 |
| 🏢 标杆公司 | 万科/华润置地/招商蛇口等16家深圳标杆开发商动态 |
| 📑 研究报告 | 券商/中指/克而瑞研报/行业白皮书/市场预测 |
| 💡 观点洞察 | 自媒体观点/购房经验/投资分析/从业者分享 |

**双视角评分**：每条资讯从 **从业者** 和 **购房者/投资者** 两个视角分别打分，帮你快速判断信息的价值维度。

## 快速安装

```bash
# 一键安装到 Skill 目录
curl -fsSL https://raw.githubusercontent.com/zycyyyya/szrehot/main/install.sh | bash
```

或手动安装：

```bash
git clone https://github.com/zycyyyya/szrehot.git ~/.codex/skills/szrehot
```

重启 Agent 后即可使用。

## 使用示例

| 你说 | Skill 响应 |
|---|---|
| 深圳楼市有什么 | RSS + 搜狗微信 → 分类输出当日热点 |
| 深圳房价 | 百度搜索 RSS + 贝壳直抓 → 行情简报 |
| 深圳土拍 | RSS 聚合 + Codex 浏览器 → 土拍结果汇总 |
| 深圳公积金 | 住建局权威源 + RSS 补充 → 政策法规简报 |
| 深圳城市更新 | 搜狗微信搜索 → 城市更新专题简报 |
| 深圳旧改 | 搜狗微信搜索 + 百度 RSS → 旧改进展汇总 |

## RSS 数据源（2026-07-21 实测验证）

通过 RSSHub 镜像站聚合，覆盖 11+ 实测可用路由：

### 通用财经

| 源 | 分类 | 条目 |
|---|---|---|
| 财联社电报 | 行情 | 实时 |
| 华尔街见闻 | 行情 | 30+ |
| 财新 | 行情 | 19 |
| 第一财经 | 行情 | 20 |
| 36氪 | 观点 | 8+ |
| 观察者网 | 研究 | 20 |
| 澎湃 | 行情 | 20+ |
| 凤凰 | 行情 | 15+ |
| 人民网 | 政策 | 15+ |
| 知乎热榜 | 观点 | 10+ |

### 百度搜索（关键词路由）

| 关键词 | 用途 |
|---|---|
| 深圳楼市 | 综合资讯 |
| 深圳房价 | 行情数据 |
| 深圳土拍 | 土地市场 |
| 地产政策 | 政策法规 |
| 城市更新 | 旧改动态 |
| 深圳旧改 | 旧改专题 |

### 标杆公司

| 关键词 | 公司 |
|---|---|
| 万科 | 万科企业 |
| 华润置地 | 华润置地 |
| 招商蛇口 | 招商蛇口 |
| 中海 | 中海地产 |
| 金地 | 金地集团 |
| 华侨城 | 华侨城 |
| 深业 | 深业集团 |
| 龙光 | 龙光集团 |
| 佳兆业 | 佳兆业集团 |
| 卓越 | 卓越集团 |
| 鸿荣源 | 鸿荣源 |
| 星河 | 星河控股 |

> 镜像站 `rsshub.liumingye.cn`，rsshub.app 在国内不可达。

## 文件结构

```
szrehot/
├── SKILL.md                    # Skill 主文件
├── README.md                   # 本文件
├── meta.json                   # Skill 元数据
├── codex_env.json              # 可选配置（Tavily Key）
├── install.sh                  # 一行安装脚本
└── scripts/
    ├── rss_fetcher.py          # RSS 采集器
    ├── daily_generator.py      # 日报生成器
    ├── fetch_all.py            # 全量采集入口
    ├── weixin_fetcher.py       # 微信公众号采集器
    └── browser_fetcher.py      # Codex 浏览器辅助
```

## 可选：离线脚本模式

环境有 Python + feedparser 时，支持离线采集 + 日报生成：

```bash
pip install feedparser

# 采集 + 生成日报
python scripts/rss_fetcher.py --output ./data --days 1
python scripts/daily_generator.py --input ./data --output ./daily --markdown
```

## License

MIT
