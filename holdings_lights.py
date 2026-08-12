"""持仓状态灯：对照持仓台账的研究卡片触发/失效条件，自动算灯色"""
import json
import os

# 从研究卡片提取的关键条件（人工维护，随卡片更新）
# 格式：代码 -> {name, 条件列表: [(条件, 灯), ...]}
CONDITIONS = {
    "sh600522": {
        "name": "中天科技",
        "conditions": [
            ("放量跌破27.02（铁底，失效处置）", "red"),
            ("反弹至36-38（减仓区，首选路径）", "yellow"),
            ("H1正式报归母净利≥24亿（预告中上沿）", "green"),
            ("现价33.55，成本63.77（浮亏-47.4%）", "red"),
        ],
    },
    "sh600519": {
        "name": "贵州茅台",
        "conditions": [
            ("放量站稳1330（100日均线，建仓触发）", "yellow"),
            ("8/15半年报：营收+5%/净利+4%/毛利率企稳", "green"),
            ("飞天批价维持1650+，渠道库存≤2月", "green"),
        ],
    },
}


def holdings_lights(quotes):
    """quotes: get_quotes()结果。返回 [(名称, 现价, 涨跌%, 灯列表), ...]"""
    out = []
    for q in quotes:
        code = q["code"]
        cfg = CONDITIONS.get(code)
        if not cfg:
            continue
        lights = []
        for cond, light in cfg["conditions"]:
            if light == "red":
                lights.append(f"🔴 {cond}")
            elif light == "yellow":
                lights.append(f"🟡 {cond}")
            else:
                lights.append(f"🟢 {cond}")
        out.append({
            "name": cfg["name"], "code": code, "price": q["price"],
            "chg_pct": q["chg_pct"], "lights": lights,
        })
    return out


def fmt_lights(hl):
    """格式化持仓状态灯为markdown"""
    if not hl:
        return "持仓状态灯：观察池暂无标的（待建立研究卡片）"
    lines = []
    for h in hl:
        lines.append(f"**{h['name']}** ({h['price']}，{h['chg_pct']:+}%)")
        for l in h["lights"]:
            lines.append(f"  - {l}")
    return "\n".join(lines)


if __name__ == "__main__":
    import data_fetcher
    q = data_fetcher.get_quotes(["sh600522", "sh600519"])
    print(fmt_lights(holdings_lights(q)))
