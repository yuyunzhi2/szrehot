"""trigger_eval for szrehot - route positive/negative/near-neighbor tests."""
import re

POSITIVES = [
    "深圳楼市今天怎么样",
    "深圳房价最新",
    "今天有什么深圳房产新闻",
    "深圳土拍结果",
    "深圳城市更新进展",
    "深圳旧改",
    "深圳保障房",
    "万科最新动态深圳",
    "生成日报",
    "深圳法拍资产日报",
    "京东法拍深圳数据采集",
]
NEGATIVES = [
    "北京楼市",
    "广州房价",
    "深圳天气",
    "帮我翻译深圳楼市报告",
    "解释一下什么是城市更新",
    "北京法拍房怎么买",
]
NEAR_NEIGHBORS = [
    ("深圳楼市日报", "szrehot", "general real estate news"),
    ("全国楼市", "not szrehot", "非深圳域"),
    ("生成日报", "szrehot", "空附加提示词走每日链"),
]

BARE_PROMPTS = {"", "生成日报", "生成日报。", "跑日报", "出日报", "生成今日日报"}


def should_trigger(text: str) -> bool:
    # 裸调用（附加提示词为空 / 只写「生成日报」）走每日链
    if text.strip() in BARE_PROMPTS:
        return True
    # 排除解释/翻译/总结类单篇意图
    if re.search(r"翻译|解释|什么是|总结", text):
        return False
    # 触发词：深圳 + 房产主题词（顺序不限，兼容“万科.*深圳”）
    has_sz = "深圳" in text
    has_topic = bool(re.search(r"(楼市|房价|土拍|买房|城市更新|旧改|地产|房产|保障房|公积金|万科|华润|招商)", text))
    if has_sz and has_topic:
        return True
    # 每日链前两步：法拍数据（深圳域内）
    if has_sz and re.search(r"法拍|京东", text):
        return True
    # 兜底短语
    if "深圳楼市" in text or "深圳房产" in text:
        return True
    # 标杆房企单提及且上下文为深圳（已由 has_sz 覆盖），额外兼容房企在前
    if has_sz and any(k in text for k in ["万科", "华润", "招商"]):
        return True
    return False


ok = all(should_trigger(p) for p in POSITIVES)
nok = not any(should_trigger(n) for n in NEGATIVES)
print(f"positives {ok}, negatives {nok}")
for q, exp, note in NEAR_NEIGHBORS:
    print(q, should_trigger(q), exp, note)
print("trigger_eval PASS" if ok and nok else "FAIL")
