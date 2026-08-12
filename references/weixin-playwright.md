# S2 微信搜索配置（Playwright MCP 版）

搜狗微信搜索必须用真实浏览器渲染（纯 curl 拿不到文章列表）。opencode 环境下用 **Playwright MCP**。

## bb-browser → Playwright MCP 工具映射

| bb-browser（Claude Code） | Playwright MCP（opencode） |
|---|---|
| `browser_open(url)` | `Playwright_browser_navigate(url)` |
| `browser_tab_new(url)` | `Playwright_browser_tabs(action="new", url=...)`；切换用 `action="select", index=N` |
| `browser_eval(js)` | `Playwright_browser_evaluate(function)`——函数体为 `() => { ... }`，返回 JSON.stringify 结果 |
| `browser_snapshot()` | `Playwright_browser_snapshot()`（a11y 树，含 ref） |
| `browser_fill/click` | `Playwright_browser_fill_form` / `Playwright_browser_click`（本流程不用——直接拼 URL 不走表单） |

**多标签并行注意**：Playwright MCP 的 `browser_evaluate` 无 tab 参数，操作前须 `browser_tabs(action="select", index=N)` 切到目标页。若并行不便，退回逐页串行——本流程仅 2-4 条需开页核验，串行可接受。

## 已知限制（重要，避免踩坑）

- **关键词带精确到月的时间词会导致结果被过滤到极少**：搜狗把时间词当文章正文要出现的**字面文本**匹配，文章正文几乎不会连写"2026年8月"这种串，所以用 `深圳楼市 2026年8月` 只返回 1 条旧文；改用 `深圳楼市 2026年` 即返回 1000+ 条正常结果。**时间词只用"YYYY年"级，不要精确到月**。**不要误判为账号限流**——账号无问题，登录态正常
- **微信文章搜索（type=2）不支持时间排序**：`sort=1` 对微信文章搜索无效，结果按相关性排列，近期文常在第 2 页，必须翻页
- 搜狗返回的中间链接（`weixin.sogou.com/link?url=` 或 `mp.weixin.qq.com/s?src=11&signature=`）**publishedAt 不可信**，必须打开真实文章核验发布时间（见下文）

## 搜索 URL（直接拼 URL，不走 fill+click 表单）

```
https://weixin.sogou.com/weixin?type=2&query={关键词}+{YYYY}年&ie=utf8&page={1或2}
```

关键词 URL 编码（空格用 `+`，中文用 `%XX`）。**时间词只用"YYYY年"级**，切勿精确到月。

## 搜索关键词（10 个）

深圳楼市、深圳房价、深圳土拍、深圳买房、深圳保障房、深圳公积金、深圳旧改、深圳城市更新、深圳工改工、深圳棚改

## 操作流程（A+C 策略：多关键词×多页 + s2 粗筛 + 核验兜底）

实测验证：单关键词单页召回太少（10 条里仅 1-2 条近期），需多关键词×2 页扩展召回 + s2 时间粗筛减少核验量。

1. **多关键词搜索**：选 4 个（推荐「深圳楼市、深圳土拍、深圳旧改、深圳保障房」覆盖市场/土地/旧改/保障房四大主题）。每个关键词搜 2 页（`&page=2`），共 8 次。每次 `browser_navigate` 后 `browser_evaluate` 批量提取

2. **s2 时间粗筛**（每页用 `browser_evaluate` 批量提取+筛选一次完成，不开详情页）。**粗筛规则**——只保留满足之一的条目进核验：
   - s2 为"N天前"且 N ≤ (窗口天数+1)（窗口跨周末约 3 天，故 N≤4；普通日窗口 1 天，N≤2）
   - s2 为"2026-M-D"格式且日期 **≥ 窗口起点当天**（如窗口起点 7-31，则保留 ≥7-31；更早直接丢弃）
   - s2 相对时间有 ±1 天偏差（实测"3天前"实际发布 7-30 而非 7-31），粗筛只作"减少核验量"，不能替代核验

   ```javascript
   () => {
     const r = [];
     document.querySelectorAll('ul.news-list li, .results li').forEach(li => {
       const h3 = li.querySelector('h3 a');
       const t = li.querySelector('.s2') || li.querySelector('.account');
       const p = li.querySelector('p.txt-info') || li.querySelector('p');
       if (h3) r.push({title: h3.innerText.replace(/\s+/g,' ').trim().slice(0,45),
         time: t ? t.innerText.trim() : '', href: h3.href});
     });
     // 粗筛: N天前(N<=4) 或 日期>=窗口起点(示例7-31=731)。窗口起点按本次实际替换: 周一=上周五, 其余=前一天, 格式 月*100+日
     const recent = r.filter(x => {
       const m = x.time.match(/(\d+)天前/); if (m && Number(m[1]) <= 4) return true;
       const d = x.time.match(/2026-(\d{1,2})-(\d{1,2})/);
       if (d) return Number(d[1]) * 100 + Number(d[2]) >= 731;
       return false;
     });
     return JSON.stringify({total: r.length, recent: recent.length, items: recent});
   }
   ```

3. **核验 var ct**（仅对粗筛通过的 2-4 条）：对每条中间链接 `browser_navigate`（中间链接会 302 跳转到真实文章页）→ `browser_evaluate` 核验（脚本见下）。窗口外丢弃；核验失败（页面空/被拦）标注「微信公众号·摘要来源」或丢弃

4. **去重合并**：4 关键词粗筛结果合并，按标题去重（不同关键词可能搜到同一篇），合并后统一核验 var ct。**微信条目必须进入统一管道（SKILL.md 步骤 4-9）**，不得绕过去重

## 发布时间核验（强制，微信文章专属）

搜狗微信中间链接和候选阶段的 `publishedAt` **一律不可信**。必须 `browser_navigate` 打开真实文章后 `browser_evaluate`：

```javascript
() => {
  const ctM = document.documentElement.innerHTML.match(/var\s+ct\s*=\s*["'](\d+)["']/);
  const body = document.body.innerText.replace(/\s+/g, ' ').trim();
  const dm = body.match(/(20\d{2})[年.\-\/](\d{1,2})[月.\-\/](\d{1,2})/);
  return JSON.stringify({
    title: document.title.slice(0, 50),
    ct: ctM ? ctM[1] : '',          // Unix 秒时间戳，最可靠
    bodyDate: dm ? dm[0] : '',      // 正文开头 "2026年7月30日"
    bodyHead: body.slice(0, 80)
  });
}
```

- **优先用 `ct` 时间戳**：`new Date(ct*1000)` 转北京时间（UTC+8）。例如 `ct=1785319934` → 2026-07-29 18:12
- 只有核验出的真实时间可进入时效审核。候选阶段 `publishedAt` 直接作废
- 未核验或核验后窗口外的微信文章，一律丢弃

## 关键点

- **相关性排序导致近期文章占比低**：搜狗按相关性排，历史长文权重高排前面，近期文常在第 2 页才出现。**必须翻第 2 页**
- **微信文章周末发文少**：窗口跨周末且核验后窗口内仅 2-3 条，是数据稀缺非方法问题，注明即可
- 只保留 `mp.weixin.qq.com` 链接；搜狗中间链接标注「微信公众号」作为来源
- 搜狗结果页 HTML 含 `red_beg`/`red_end` 高亮标记，提取标题时清理

## 降级链（搜索异常时）

搜狗结果异常（多为关键词带精确到月时间词所致，换「YYYY年」级可恢复）→ 若确无窗口内文章 → **S1.5 已抓的 `mp.weixin.qq.com` 文章**（首选降级，从 `_s15.json` 筛 `mp.weixin` URL）→ 新榜（`https://www.newrank.cn`）→ 微信搜一搜。降级失败即跳过该关键词；S2 全缺时在日报注明，不阻塞（S1.5 已实质覆盖微信内容）。
