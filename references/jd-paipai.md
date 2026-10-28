# 京东法拍采集（原 jd-paipai，已并入 szrehot 每日链阶段 1）

经 `api.m.jd.com` 公开接口全量抓取深圳法拍，按**日期块**追加写入
`D:\Desktop\法拍数据.xlsx` 的 `京东API`（在售）/ `京东成交API`（成交）两个 sheet。
无需登录、无需浏览器。

## 两条轨道

| 轨道 | 状态 | sheet | 去重 | 排序 |
|---|---|---|---|---|
| 1 在售 | 进行中 + 预告中 | 京东API | 块内 + 历史（物业名称 + 起拍价(万元)，历史重复一律剔除，只留新增） | 围观数降序 |
| 2 成交 | 已结束（结束时间窗口内） | 京东成交API | 块内 + 历史（同上，各 sheet 只比对自己历史） | 拍卖开始时间日期降序，同日按出价次数降序 |

```bash
# 工作目录 = szrehot 技能目录；所有 python 带 -X utf8
python -X utf8 scripts/run_daily_chain.py                 # 每日链阶段 1+2（推荐入口）
python -X utf8 scripts/jd_paipai/fetch_active.py [--dry-run]                    # 只跑轨道 1
python -X utf8 scripts/jd_paipai/fetch_closed.py [--since YYYY-MM-DD] [--dry-run]  # 只跑轨道 2
```

- `--dry-run` 只抓取组块、不触碰 xlsx（首次接入或排查时先 dry-run 再实写）。
- 轨道 2 `--since` 默认今天-10 天，`--until` 默认今天；`--date` 可指定块日期。
  `run_daily_chain.py` 会把 `--since/--until` 随业务日期滚动，指定往日也一致。
- 详情补全带 `scripts/jd_paipai/_jd_cache.json` 断点续跑缓存；向 stdout 只写交付行，日志走 stderr。

## 块格式（硬约束）

1. 取 sheet **首个可用行**（空表 A1 为空则行 1，否则 `max_row+1`），A 列写运行当天日期（`2026年10月09日`）。
2. 下一行起写数据行（16 列，**无块内表头**，列定义见下）。
3. 去重分两段：块内去重 + 历史去重（与本 sheet 全部历史行同 key 比对，重复一律剔除，每日跑只追加新增）；
   排序只作用于本次块，不动历史块；缺失排序键沉底。全被历史命中时仍写日期行（当日心跳，数据行=0）。
4. 价格接口单位为元→万元（÷10000，2 位）；折价率自算（起拍÷评估），不读现成列。

## 块列定义（16 列，顺序固定）

`类目 | 竞价状态 | 物业名称 | 所在区县 | 起拍价(万元) | 当前价(万元) | 评估价(万元) |
折价率(%) | 建筑面积(㎡) | 保证金(万元) | 出价次数 | 围观数 | 拍卖开始时间 | 拍卖结束时间 |
竞价轮次 | 详情链接`

- A 列（物业名称）/ B 列（详情链接）与旧 sheet 前两列（项目/网址）对齐。
- 价格：接口元→万元（2 位）；折价率 = 起拍÷评估×100（1 位），自算。
- 时间：`YYYY-MM-DD HH:MM` 字符串；缺失留空。
- 已结束的“当前价”即最终价（有出价=成交价，零出价=起拍价原样，可用出价次数区分）。

### 去重键

（物业名称去首尾空格，起拍价转万元后 2 位小数）二者同时相同判同一物业，块内保留首条。

### 历史去重（增量口径，2026-10-10 起）

- 写入前与**本 sheet 全部历史行**同 key 比对，命中一律剔除，只保留新增行。
- 各 sheet 只比对自己历史（京东API ↔ 京东成交API 互不干扰）。
- 日期行/空行（物业名称为空）不参与比对；同名同价但起拍价变动视为新增。
- 全被历史命中时仍写日期行（当日心跳，数据行=0）。

### 排序键

- 京东API：围观数降序，缺失沉底（稳定排序）。
- 京东成交API：拍卖开始时间**日期部分**降序，同日按出价次数降序，缺失沉底。

## 接口（2026-10-09 实测，全部无需登录、无需 h5st 签名）

基地址 `https://api.m.jd.com/api`，POST，`Content-Type: application/x-www-form-urlencoded`，
`body=<urlencode(JSON)>`，通用查询 `appid=paimai&functionId=…&loginType=3`。
发现路径：PC 详情页 → `auction.jd.com/sifa.html` → `pmsearch.jd.com`，浏览器抓包定参。

### 1. 列表 `getSearchData`（核心）

请求 `searchParamsObj` 关键键：

| 键 | 值 | 说明 |
|---|---|---|
| `multiCityIds` | `1607` | 深圳（`multiProvinceIds=19` 广东） |
| `multiThirdCateIds` | `15/14/11/6` | 住宅/商业/工业/其他用房 |
| `multiStatus` | `101/102/105` | 进行中/预告中/已结束 |
| `endTimeRangeStart/End` | `YYYY-MM-DD` | 结束时间服务端过滤（已结束轨道用） |
| `page/pageSize` | `40` | 翻到空页即穷尽 |

`pageParam=pc_search_judicature`，`tabParam=all`，`lbsEffect=2` 照抄即可。

条目（`resultData[].data`）常用字段：`paimaiId/title/city/startPrice/currentPrice/assessmentPrice`
（**元**）/`paimaiStatus`（1/0）/`bidCount/accessNum`（出价/围观）/`remindCount/endTime`（ms）
/`houseAttributes.houseArea`（面积）/`discountRate`/`labelSet`。

### 2. 详情 `getProductBasicInfo`

`{"paimaiId":"…"}` → `title/startPrice/assessmentPrice/ensurePrice`（保证金，元）
/`startTime/endTime`（ms）/`auctionPrefix`（一拍/二拍/变卖）/`productAddressResult.county`
/法院店铺/经纬度。不含面积与围观数。

### 3. 介绍 `queryProductDescription`（备用）

`{"paimaiId":"…","source":5}` → 标的物介绍 HTML。部分拍品只返回“详见附件”，
此时回退详情页/附件解析（本阶段只用列表面积，缺失留空）。

## 踩坑（全部实测）

1. `searchCounts.total/totalItem` **不可信**（页间重叠、总数虚高/取整封顶）——以翻到空页为准，`paimaiId` 去重。
2. 传 `sortField` 会导致条目 `endTime` 整体缺失——禁用排序参数，用默认排序 + `endTimeRange`。
3. 已结束默认排序非时间序且有 ~600 条可取上限——必须用 `endTimeRangeStart/End` 精确窗口。
4. 价格文本混杂（`2.93亿/¥4.18亿`）是旧 xlsx 采集格式；API 直接给元为单位的数字，除以 10000 即万元。
5. 阿里无公开 API（x5sec 拦截），不在本阶段范围内（阿里面积走 ShadowBot/CDP，见 `references/pipeline.md`）。
6. `openpyxl` 的 `max_row` 含无值空壳单元格，删行残留会把 used-range 撑大——`write_block.first_free_row` 已按「有值最大行+1」落点并告警。

## 不要做

- 不做全表去重/重排历史块；不改历史日期行；不伪造缺失面积/价格。
- 不向 stdout 写非交付日志（`file=sys.stderr`）；不定死窗口（用 `--since/--until` 传参）。
- 不在同一业务日内重复实写同一 sheet（会多出空日期块）——`run_daily_chain.py` 已做幂等跳过，需重跑加 `--force`。
