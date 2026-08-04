# S1 RSSHub 路由清单

采集脚本：`python scripts/fetch_s1.py --json`（路由已硬编码在脚本中，下表供维护参考）。

镜像：首选 `https://hub.slarker.me`，备用 `https://rsshub.liumingye.cn`（rsshub.app 国内不可达），脚本自动回退。curl 已加 `-k` 跳过 SSL 验证。路由返回 HTML 欢迎页 = 路由不存在，跳过。

| 路由 | 媒体 | 分类 |
|---|---|---|
| `/cls/telegraph` | 财联社电报 | market |
| `/wallstreetcn/news/global` | 华尔街见闻 | market |
| `/caixin/latest` | 财新最新 | market |
| `/caixin/article` | 财新文章 | research |
| `/yicai/brief` | 第一财经快讯 | market |
| `/yicai/news` | 第一财经新闻 | market |
| `/36kr/newsflashes` | 36氪快讯 | insights |
| `/36kr/hot-list` | 36氪热榜 | insights |
| `/guancha/headline` | 观察者网 | research |
| `/thepaper/list/25457` | 澎湃新闻 | market |
| `/thepaper/sidebar` | 澎湃侧栏 | market |
| `/ifeng/news` | 凤凰新闻 | market |
| `/people/env` | 人民网 | policy |
| `/zhihu/hot` | 知乎热榜 | insights |

百度搜索 RSS 返回首页/导航链接为主，新闻价值极低，已弃用。
