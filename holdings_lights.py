"""持仓状态灯：对照持仓台账的研究卡片触发/失效条件，自动算灯色+触发位距离"""
import json
import os

# 从研究卡片提取的触发位与否决条件（人工维护，随卡片更新）
# 只放实际持仓标的（用户确认：茅台未持有，不在推送范围）
# triggers: (标签, 触发价, 灯色, 动作)；veto: 否决持有的硬条件（与价格无关）
POSITIONS = {
    "sh600522": {
        "name": "中天科技",
        "cost": 63.771,
        "triggers": [
            ("减仓区下沿36-38", 36.0, "yellow", "减100股，回收~3600元"),
            ("铁底27.02", 27.02, "red", "放量跌破则处置减仓"),
        ],
        "veto": [
            "8/28中报归母净利<23.52亿 → 否决持有",
            "美国/欧盟光纤政策权威坐实 → 否决持有",
        ],
    },
}


def holdings_lights(quotes):
    """quotes: get_quotes()结果。返回 [(名称, 现价, 涨跌%, 灯列表), ...]"""
    out = []
    for q in quotes:
        code = q["code"]
        cfg = POSITIONS.get(code)
        if not cfg:
            continue
        price = q["price"]
        lights = []
        for label, level, light, action in cfg["triggers"]:
            if light == "yellow":  # 压力/目标位：现价在下，报还需涨多少
                if price >= level:
                    lights.append(f"🟡 已触及{label}({level}) → {action}")
                else:
                    dist = (level / price - 1) * 100
                    lights.append(f"🟡 距{label}({level})还差{dist:.1f}%，不动作")
            else:  # 支撑/失效位：现价在上，报还需跌多少
                if price <= level:
                    lights.append(f"🔴 已跌破{label}({level}) → {action}")
                else:
                    dist = (price / level - 1) * 100
                    lights.append(f"🔴 距{label}({level})还有{dist:.1f}%缓冲，不动作")
        pct = (price / cfg["cost"] - 1) * 100
        lights.append(f"🔴 现价{price}，成本{cfg['cost']}，浮亏{pct:.1f}%")
        for v in cfg["veto"]:
            lights.append(f"🟢 {v}")
        out.append({
            "name": cfg["name"], "code": code, "price": price,
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
