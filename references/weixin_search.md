# S2 微信搜索配置（Chrome DevTools 版）

## 采集方式：Chrome DevTools MCP（首选，需 JS 渲染 + 登录态）

搜狗微信搜索必须用真实浏览器渲染（纯 curl 拿不到文章列表）。Codex 环境下用 **Chrome DevTools MCP** 操作。

### 已知限制（重要，避免踩坑）

- **关键词带精确到月的时间词会导致结果被过滤到极少**：搜狗把时间词当文章正文要出现的**字面文本**匹配，文章正文几乎不会连写"2026年8月"这种串，所以用 `深圳楼市 2026年8月` 只返回1条旧文；改用 `深圳楼市 2026年`（年到年）即返回 1000+ 条正常结果。**时间词只用"YYYY年"级，不要精确到月**
- **微信文章搜索（type=2）不支持时间排序**：`sort=1` 对微信文章搜索无效，结果按相关性排列，需浏览前几页人工筛选窗口内文章（搜狗网页搜索 type≠2 才支持时间排序）
- 搜狗返回的中间链接（`weixin.sogou.com/link?url=` 或 `mp.weixin.qq.com/s?src=11&signature=`）**publishedAt 不可信**，必须打开真实文章核验发布时间（见下文"发布时间核验"）

### 前置：确保 Chrome DevTools 就绪
- daemon 端口 19824 监听即可用（`netstat -ano | grep 19824`）
- 不可用时按 Codex 排查流程检查 Chrome 连接：检查 Chrome DevTools MCP 连接状态

### 搜索 URL（直接拼 URL，不走 fill+click 表单）

```
https://weixin.sogou.com/weixin?type=2&query={关键词}+{YYYY}年&ie=utf8
```

关键词需 URL 编码（空格用 `+`，中文用 `%XX`）。**时间词只用"YYYY年"级**（如 `深圳楼市 2026年`），切勿精确到月——"2026年8月"会被搜狗当字面文本匹配，文章正文几乎不含该连写串，导致结果被过滤到 1 条旧文。用"2026年"级则返回 1000+ 条正常结果。微信文章搜索不支持时间排序，需浏览前几页按相关性筛选。

### 搜索关键词（10 个）

深圳楼市、深圳房价、深圳土拍、深圳买房、深圳保障房、深圳公积金、深圳旧改、深圳城市更新、深圳工改工、深圳棚改

### 操作流程（A+C 策略：多关键词×多页 + s2 粗筛 + 核验兜底）

实测验证：单关键词单页召回太少（10条里仅1-2条近期），需多关键词×2页扩展召回 + s2 时间粗筛减少核验量。

1. **多关键词搜索**：从上方关键词表选 4 个（推荐「深圳楼市、深圳土拍、深圳旧改、深圳保障房」覆盖市场/土地/旧改/保障房四大主题）。每个关键词搜 2 页（`&page=2`），共 8 次搜索。每次 URL：`https://weixin.sogou.com/weixin?type=2&query={关键词}+{YYYY}年&ie=utf8&page={1或2}`

2. **s2 时间粗筛**（每页用 `evaluate_script` 批量提取，不开详情页）：提取每条 `ul.news-list li` 的标题、`.s2` 时间、`h3 a` href。**粗筛规则**——只保留满足以下之一的条目，进核验：
   - s2 为"N天前"且 N ≤ (窗口天数+1)（窗口跨周末约3天，故 N≤4；普通日窗口1天，N≤2）
   - s2 为"2026-M-D"格式且日期 **≥ 窗口起点当天**（如窗口起点7-31，则保留 ≥7-31；7-30 及更早直接丢弃，不进核验）
   - 注意 s2 相对时间有 ±1 天偏差（实测"3天前"实际发布7-30而非7-31），故粗筛只作"减少核验量"，不能替代核验

3. **核验 var ct**（仅对粗筛通过的条目，2-4条）：并行 `new_page` 打开搜狗中间链接，`evaluate_script` 读 `var ct` 时间戳转北京时间。窗口外的丢弃。核验失败的（页面空/被拦）标注「微信公众号·摘要来源」或丢弃
   - 粗筛 eval 脚本（提取+筛选一次完成）：
   ```javascript
   (()=>{
     const r=[];
     document.querySelectorAll('ul.news-list li, .results li').forEach(li=>{
       const h3=li.querySelector('h3 a'); const t=li.querySelector('.s2')||li.querySelector('.account');
       const p=li.querySelector('p.txt-info')||li.querySelector('p');
       if(h3) r.push({title:h3.innerText.replace(/\s+/g,' ').trim().slice(0,45),time:t?t.innerText.trim():'',snippet:p?p.innerText.replace(/\s+/g,' ').trim().slice(0,50):'',href:h3.href});
     });
     // 粗筛: N天前(N<=4) 或 日期>=窗口起点(示例7-31=731)
     const recent=r.filter(x=>{
       const m=x.time.match(/(\d+)天前/); if(m&&Number(m[1])<=4) return true;
       const d=x.time.match(/2026-(\d{1,2})-(\d{1,2})/); if(d){return Number(d[1])*100+Number(d[2])>=731;} return false;
     });
     return JSON.stringify({total:r.length,recent:recent.length,items:recent});
   })()
   ```
   - 粗筛脚本里的窗口起点日期（`731`=7-31）需按本次实际窗口起点替换：周一=上周五，其余=前一天，格式 `月*100+日`

4. **去重合并**：4关键词的粗筛结果合并，按标题去重（不同关键词可能搜到同一篇）。合并后统一核验 var ct

### 关键点

- **相关性排序导致近期文章占比低**：搜狗按相关性排，历史长文权重高排前面，近期文常在第2页才出现。**必须翻第2页**，单看第1页会漏（实测"深圳旧改"第1页0条近期、第2页3条近期）
- **s2 时间不可信但可用于粗筛**：s2 的"N天前"有偏差，但用于"排除明显旧文"够用；最终时效以核验 var ct 为准
- **微信文章周末发文少**：若窗口跨周末且核验后窗口内文章仅2-3条，是数据稀缺非方法问题，注明即可

### 降级链（搜索异常时）

搜狗结果异常（多为关键词带精确到月时间词所致，换"YYYY年"级时间词可恢复）→ 若确无窗口内文章 → **S1.5 已抓的 `mp.weixin.qq.com` 文章**（首选降级，从 `_s15.json` 筛选）→ 新榜（`https://www.newrank.cn`）→ 微信搜一搜。降级失败即跳过该关键词。

### 提取要点

- 搜狗结果页 HTML 含 `red_beg`/`red_end` 高亮标记，提取标题时 `re.sub(r'<[^>]+>', '', title)` 清理
- 公众号名在 `class="all-time-y2"`，发布时间在 `class="s2"`（**列表页 s2 时间有 ±1 天偏差，不可直接用于时效审核，但可用于粗筛排除明显旧文；最终时效以打开详情页核验 `var ct` 为准**）
- 只保留 `mp.weixin.qq.com` 链接；搜狗中间链接标注「微信公众号」作为来源

### 发布时间核验（强制，微信文章专属）

搜狗微信中间链接（`mp.weixin.qq.com/s?src=11&signature=...` 或 `weixin.sogou.com/link?url=...`）和候选阶段的 `publishedAt` **一律不可信**。必须 `navigate_page` 打开真实文章后，用 `evaluate_script` 核验：

```javascript
(function(){
  var scripts = document.documentElement.innerHTML;
  var ctM = scripts.match(/var\s+ct\s*=\s*["'](\d+)["']/);
  var body = document.body.innerText.replace(/\s+/g,' ').trim();
  var dm = body.match(/(20\d{2})[年.\-\/](\d{1,2})[月.\-\/](\d{1,2})/);
  return JSON.stringify({
    title: document.title.slice(0,50),
    ct: ctM ? ctM[1] : '',        // Unix 秒时间戳，最可靠
    bodyDate: dm ? dm[0] : '',    // 正文开头的 "2026年7月30日"
    bodyHead: body.slice(0,80)
  });
})()
```

- **优先用 `ct` 时间戳**：`new Date(ct*1000)` 转北京时间（UTC+8）。例如 `ct=1785319934` → 2026-07-29 18:12
- 只有核验出的真实时间可进入时效审核（步骤7）。候选阶段填的 `publishedAt` 直接作废
- 未核验或核验后窗口外的微信文章，一律丢弃
