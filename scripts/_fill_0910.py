# -*- coding: utf-8 -*-
import json, pathlib
base = pathlib.Path(r"D:\git\opencode\.opencode\skills\szrehot\scripts")
tmpl = json.loads((base/"_final.json").read_text(encoding="utf-8"))
by_title = {t["title"][:12]: t for t in tmpl}

def pick(prefix):
    for k, v in by_title.items():
        if prefix in v["title"]:
            return v
    raise KeyError(prefix)

specs = [
    ("深市首单双主题", "urban_renewal",
     "创金合信北京国资REIT在深交所上市，为深市首单商业不动产双主题REIT。以隆福寺稳收益搭配AI产业园成长性，打通城市更新存量盘活与资本退出闭环，为深圳存量资产证券化提供可复制样本。",
     "bullish", 2),
    ("白石洲天悦花园", "urban_renewal",
     "南山管理局公示白石洲天悦花园总平面修改，取消两处地下通道并微调景观泳池出入口，总建面由53.06万㎡调至52.99万㎡。属存量大盘常规优化，无新增货值，对白石洲旧改整体推进影响中性。",
     "neutral", 2),
    ("房企如何重塑资金流", "policy",
     "中指解读8.28新政后房企进入高资本金沉淀、慢现金流回流阶段，预售门槛提至封顶、回款专户封闭，项目与集团现金流逻辑均需重塑。全国性制度重构，决定深圳房企未来拿地与周转上限。",
     "neutral", 4),
    ("深圳规模最大高铁站", "policy",
     "西丽站3标119根地铁13号线间工程桩完工，变形仅1.3毫米。西丽枢纽规划4高铁2城际4地铁，建成后深圳客运运力提升约40%。利好南山西丽片区通达性与TOD居住配套，支撑周边刚需去化。",
     "bullish", 2),
    ("项目如何成为经营", "research",
     "克而瑞提出828新政三重迁移：经营单元、信用锚、融资入口均转向项目制，股东资金峰值升至1.7倍，房企能力转向资本厚度与项目信用。属全国性框架研究，为理解深圳项目制落地提供分析标尺。",
     "neutral", 3),
    ("十五五", "policy",
     "广东21个地市十五五纲要全部发布，明确下阶段空间与产业格局。属省级规划信息集中落地，对深圳十四五收官与十五五项目储备具方向指引，暂无具体供地或政策增量。",
     "neutral", 3),
    ("新政效应持续释放深圳楼市", "market",
     "证券时报实探深圳金九开局：罗湖带看升近20%，9月首周二手成交1062套环增5.8%，贝壳签约同比增17%，置业偏好低总价刚需与优质学区房。政策与季节共振，业主降价意愿减弱，议价空间收窄。",
     "bullish", 4),
    ("香港发行40亿元", "policy",
     "深圳市政府在香港发行40亿元离岸债，5年期绿色债25亿投向地铁等项目，10年期15亿利率1.81%。为连续第六年赴港发行，属常规财政融资安排，利好基建资金面，对楼市无直接传导。",
     "neutral", 2),
    ("95万在深圳买套房", "market",
     "贝壳监测深圳百万元内部分二手房月租高于月供，罗湖大中华95万元房源租3892元供2923元。40年期房贷压低月供约634元，9月初二手签约环涨33%。以价换量下刚需窗口显现，但仅24个片区报价上涨。",
     "bullish", 3),
    ("一线城市住宅租金", "market",
     "中指数据显示一线住宅租金连六月环涨，8月上海涨0.61%、深圳涨0.27%领头，深圳均83.05元/㎡/月，光明宝安南山等区齐涨。购房需求滞留租赁叠加毕业季，租金企稳领先房价，强化核心区企稳预期。",
     "bullish", 3),
]

out = []
for prefix, cat, summ, sent, w in specs:
    t = pick(prefix)
    out.append({
        "title": t["title"],
        "url": t["url"],
        "source": t["source"],
        "publishedAt": t.get("verified_time") or t.get("publishedAt", ""),
        "category": cat,
        "summary": summ,
        "sentiment": sent,
        "weight": w,
        "verified_time": t.get("verified_time"),
        "window_result": t.get("window_result", "窗口内"),
        "note": t.get("note", ""),
    })

(base/"_final.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote final {len(out)}")
for o in out:
    print(o["category"], o["sentiment"], o["weight"], o["title"][:40])
