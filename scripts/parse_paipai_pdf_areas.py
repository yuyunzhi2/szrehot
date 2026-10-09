# -*- coding: utf-8 -*-
"""从已存档的法拍附件 PDF 解析建筑面积，生成 sidecar 给 fetch_paipai_hot.py 做兜底。

输入: outputs/2026-09-08/paipai_attach/*.pdf（2026-09-09 用 ShadowBot 浏览器登录态下载）
输出: upsert 到 scripts/_paipai_area_store.json（fetch_paipai_hot.py 启动加载、命中即跳过开页）

- 吉祥里/竹子林/铭德：查档单文本正则提取（确定性）。
- 蔚蓝海岸：查档单第1-2页为扫描图，无文本层；面积 111.82 系保存的扫描图
  weilan_p0.png 经图像读取的见证值（住宅/证号4000438272/坐落均吻合），此处如实记录来源。
- 铭德大厦：拍卖标的为 A座401~1801（奇数层15套），PDF 中 15 单元面积求和。
"""
import datetime
import json
import os
import re

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
ATT = os.path.join(HERE, "..", "outputs", "2026-09-08", "paipai_attach")
OUT = os.path.join(HERE, "_paipai_area_store.json")


def pdf_text(name):
    with pdfplumber.open(os.path.join(ATT, name)) as pdf:
        return "\n".join([(pg.extract_text() or "") for pg in pdf.pages])


def single_area(txt):
    m = re.search(r"单独所有\s*([0-9]+(?:\.[0-9]+)?)\s*平方米", txt)
    return float(m.group(1)) if m else None


result = {}

# 吉祥里豪庭3栋5D（住宅，证0236188号）
t = pdf_text("jixiangli.pdf")
a = single_area(t)
assert a == 143.06, a
result["1081181114988"] = {
    "area": a,
    "provenance": "不动产信息查询结果告知单文本：龙岗区龙城街道吉祥里豪庭3栋5D/证0236188号/住宅/单独所有143.06平方米",
}

# 竹子林教育科技大厦塔楼04层（办公）
t = pdf_text("zhuzilin.pdf")
a = single_area(t)
assert a == 1373.45, a
result["1083148196035"] = {
    "area": a,
    "provenance": "不动产信息查询结果告知单文本：福田区竹子林教育科技大厦塔楼04层/办公/单独所有1373.45平方米",
}

# 铭德大厦A座401~1801奇数层15套（商业性办公）：逐单元提取后求和
t = pdf_text("mingde.pdf")
units = re.findall(r"坐落于龙岗区龙城街道铭德大厦(.{1,60}?)，权属证号", t)
areas = re.findall(r"单独所有\s*([0-9]+(?:\.[0-9]+)?)\s*平方米", t)
assert len(units) == len(areas) == 15, (len(units), len(areas))
detail = {u.strip(): float(x) for u, x in zip(units, areas)}
want = [f"A座{f}" for f in (401, 501, 601, 701, 801, 901, 1001, 1101, 1201, 1301, 1401, 1501, 1601, 1701, 1801)]
for w in want:
    assert any(w in u for u in detail), w
total = round(sum(detail.values()), 2)
assert total == 18499.28, total
result["1082220257853"] = {
    "area": total,
    "provenance": f"不动产信息单15单元面积求和={total}（明细："
    + "；".join(f"{u}{x}" for u, x in detail.items()) + "）",
}

# 蔚蓝海岸二期25栋6C：查档单为扫描图，见证值
result["1083203804632"] = {
    "area": 111.82,
    "provenance": "查档单扫描图 outputs/2026-09-08/paipai_attach/weilan_p0.png 图像读取："
    "南山区粤海街道蔚蓝海岸社区二期25栋6C/住宅/证4000438272/整套111.82平方米",
}

# 写入持久 store（upsert，保留既有条目与日期）
try:
    store = json.load(open(OUT, encoding="utf-8"))
except Exception:
    store = {}
if not isinstance(store, dict):
    store = {}
today = datetime.date.today().isoformat()
for k, v in result.items():
    v = dict(v)
    v.setdefault("date", today)
    store[str(k)] = v
json.dump(store, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"wrote {OUT} n={len(result)}")
for k, v in result.items():
    print(f"  {k}: {v['area']}")
