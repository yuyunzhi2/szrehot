# -*- coding: utf-8 -*-
"""Fill semantics for 2026-09-09 daily (12 items). Run: python -X utf8 scripts/_fill_0909.py"""
import json

SEM = [
 ("华强南西片区旧改确认实施主体", "urban_renewal",
  "9月3日深圳华强财富置业获确认为福田华强南西片区旧改实施主体，项目自2010年列入计划、654户业主签约历时16年终转入开发，福田核心区旧改取得实质性突破，片区居住商业配套预期改善，带动周边成交预期升温。",
  "bullish", 3),
 ("坪山一土整项目规划公示", "market",
  "9月7日坪山管理局公示金沙社区卢屋居民小组土整利益统筹项目规划草案，实施范围约21.3公顷、整体规划容积约60.1万平方米，含7宗住宅用地及54班九年一贯制学校，东部片区中长期供应蓝图初现。",
  "neutral", 3),
 ("第一波", "market",
  "咚咚梳理828新政落地10天深圳市场：300万元三房成爆款、低总价占比创年内新高，贝壳合作门店9月上旬签约量同比增17%，深铁珑境云赋加推认购超10亿元；各地收回折扣多为营销包装，深圳尚无大面积涨价。",
  "bullish", 4),
 ("星悦睿府", "company",
  "星河潭罗村旧改第四组团星悦睿府总平图公示，用地2.51万平方米、容积率9.38为四组团最高，规划1186户、最小75平方米，已入市两期去化分化，龙华大浪片区旧改供应继续放量。",
  "neutral", 2),
 ("实探", "market",
  "证券时报实探深圳金九开局：罗湖中介带看量较8月中旬升近20%，9月上旬二手房录得1062套环比增5.8%，贝壳签约量同比增17%；开发商转向实景样板间营销，买卖双方预期趋同，业主大幅降价意愿减弱。",
  "bullish", 4),
 ("房企现金流", "company",
  "证券时报统计92家上市房企上半年经营现金流均值升至7.36亿元，招商蛇口由负转正流入241亿元，但整体利润仍下滑；828新政拉长回款周期，行业竞争将向资金稳健的头部房企集中。",
  "neutral", 3),
 ("四个新盘集中入市", "market",
  "凤凰对比新政前后四个新盘备案价：臻华府、锦和玉鸣府基本持平或微降，万星林均价降9.9%系产品结构差异；深圳可售住宅约4.79万套、去化周期15.4个月，短期开发商仍需拼价格拼去化。",
  "neutral", 4),
 ("商改医", "urban_renewal",
  "宝安将蓝湾商务中心存量商业楼改造为深大总医院口腔医学中心，近8500平方米配34张牙椅并同步引入市中医院针灸推拿中心，打造三甲口腔加公立中医康复综合体，存量空间民生化利用添样板。",
  "neutral", 2),
 ("组合拳", "market",
  "央视报道828组合拳落地10天：深圳刚需热度攀升、部分门店签约创年内单日新高，300万元三房成爆款，公寓贷款延至15年月供直降1907元；北京上海武汉三地带看签约同步回暖，武汉加码1亿元购房补贴。",
  "bullish", 3),
 ("土拍和供地", "research",
  "丁祖昱评楼市梳理新政后首轮土拍：上海普陀地王底价成交、北京将台地块暂停出让优化条件，成都青岛多城底价成交；深圳111个待供应项目中79个已取规证，真正供应收缩或至2028年才显现。",
  "neutral", 3),
 ("8月住房租赁", "research",
  "中指梳理8月租赁市场：公积金条例修订取消租房提取收入比例限制，证监会明确支持租赁住房REITs；50城平均租金环比微涨0.03%，上海深圳领涨而多数城市下跌，万科泊寓龙湖冠寓营收下滑、央企重资产运营商逆势增长。",
  "neutral", 3),
 ("腾讯搬走", "insights",
  "腾讯搬离科兴科学园后周边华润城8月成交约24套企稳回暖，润玺单价最高超17万元创年内新高；科兴租金回调约40%叠加毕业季，片区租赁成交走热，核心地段加名校加商圈支撑房价韧性。",
  "bullish", 2),
]

DROPS = ["楼市新政落地十天", "义学街", "保利宝中地王", "保利宝中项目",
         "租金", "开局向好", "等了16年", "进入开发建设"]

tpl = json.load(open("scripts/_final.json", encoding="utf-8"))
kept = [x for x in tpl if not any(d in x["title"] for d in DROPS)]
assert len(kept) == 12, f"kept={len(kept)}"
assert len(SEM) == 12
out = []
for item, (key, cat, summ, sent, w) in zip(kept, SEM):
    assert key in item["title"], f"mismatch {key} vs {item['title'][:30]}"
    assert 50 <= len(summ) <= 110, f"{key} len={len(summ)}"
    item["category"] = cat
    item["summary"] = summ
    item["sentiment"] = sent
    item["weight"] = w
    item["publishedAt"] = item.get("verified_time") or item.get("publishedAt")
    out.append(item)
json.dump(out, open("scripts/_final.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
bull = sum(i["weight"] for i in out if i["sentiment"] == "bullish")
tot = sum(i["weight"] for i in out)
print(f"done n={len(out)} index={50 + 50 * bull / tot:.1f} (bull={bull} tot={tot})")
