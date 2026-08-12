# 数据源清单（S1 RSS + S1.5 Google Alerts）

采集脚本：`python -X utf8 scripts/fetch_s1.py --json -o scripts/_s1.json` / `python -X utf8 scripts/fetch_s15.py --json -o scripts/_s15.json`（feed/路由已硬编码在脚本中，下表供维护参考）。

## S1 RSS（辅助源，实测贡献≈0）

14 路通用财经媒体几乎不产深圳本地新闻——产出 0 条或全旧闻是常态，直接跳过不阻塞、不修源。

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

## S1.5 Google Alerts（主源，必跑）

8 个 Alert Feed，脚本内置 `socks5h://127.0.0.1:10808` 代理 + 存活检测，代理不可用时退出码 2，主模型据此跳过 S1.5 并注明。

| Alert 名称 | Feed URL |
|---|---|
| 深圳 城市更新 | `https://www.google.com/alerts/feeds/02703135011315330991/17679522208773290885` |
| 深圳 房地产 开发 | `https://www.google.com/alerts/feeds/02703135011315330991/6102386517983312706` |
| 深圳 规划 | `https://www.google.com/alerts/feeds/02703135011315330991/331647959170947056` |
| 深圳 商品房 销售 | `https://www.google.com/alerts/feeds/02703135011315330991/11832382345329285490` |
| 深圳 商业 | `https://www.google.com/alerts/feeds/02703135011315330991/4220399693512442495` |
| 深圳 土地 | `https://www.google.com/alerts/feeds/02703135011315330991/6277879696444141962` |
| 深圳 消费 | `https://www.google.com/alerts/feeds/02703135011315330991/14096619598796495203` |
| 深圳 租金 | `https://www.google.com/alerts/feeds/02703135011315330991/12848020173352227376` |

### 解析要点

- ATOM XML，提取 `<entry>` 的 `<title>`、`<link href>`、`<published>`
- 标题含 HTML `<b>` 标签和 `&lt;`/`&gt;` 实体，需先 `re.sub(r'<[^>]+>', '', title)` 去标签再 `html.unescape()` 还原实体
- 链接为 Google 重定向 URL，**必须先 `html.unescape()` 还原 `&amp;`→`&`**，再提取 `url=` 参数并 `up.unquote()`。否则 `&amp;` 会导致正则截断，URL 残缺
- **`publishedAt` 是 Google 索引时间，非原文发布时间**——进入时效审核前必须按 SKILL.md 步骤 6 从 URL 路径日期或全文核验真实时间。索引时间可能比原文晚 1-8 天，直接用会导致窗口外条目误入选（实例：融创 07-29 文章被 08-10 索引误收）
- **深圳 商业**和**深圳 消费**噪声较高（餐饮/电商/AI 等非房产新闻），需严格地域+主题审核
- 部分 Alert 可能无结果（空 `<feed>`），正常跳过

### 代理排查（历史踩坑）

脚本曾用 `curl.exe`，其 schannel SSL 后端对代理出口节点的 TLS 证书验证失败（curl exit 35），即使代理完全可用也误报 PROXY_DOWN。**已改用 requests（OpenSSL 后端）修复**。若再报代理不可用：

1. 先确认端口在监听：`netstat -ano | findstr 10808`
2. 手测代理（requests 路径）：
   `python -c "import requests;r=requests.get('https://www.google.com/generate_204',proxies={'http':'socks5h://127.0.0.1:10808','https':'socks5h://127.0.0.1:10808'},timeout=10);print(r.status_code)"`
   返回 200/204 = 代理可用（脚本环境问题，检查 requests/PySocks 是否装好）；超时/异常 = 代理真挂
3. **不要**用 `curl.exe` 测代理——它对 socks5 代理的 SSL 证书验证会误判
