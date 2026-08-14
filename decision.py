"""决策区生成：规则引擎出结论与操作清单（纯规则，不经LLM，保证方向结论可控）
输入：market_dashboard结果 + holdings_lights结果 + positions.json账户配置
输出：一句话结论 + ✅⚠️❌操作清单
"""
from holdings_lights import load_config


def conclusion(dash, hl, mode="close"):
    """一句话结论。mode: close=复盘 / morning=晨报(今日预案)"""
    weather, score = dash["weather"], dash["score"]
    if weather == "雨":
        pos_advice = "只减不加"
    elif weather == "多云":
        pos_advice = "持有不加仓"
    else:
        pos_advice = "按计划可执行"

    # 最近触发位
    nearest = None
    for h in hl:
        for t in h["triggers"]:
            if nearest is None or t["dist_pct"] < nearest[1]:
                nearest = (f"{h['name']}{t['label']}", t["dist_pct"], t["hit"], t["action"])
    if nearest and nearest[2]:
        action = f"已触发{nearest[0]}：{nearest[3]}"
    elif nearest and nearest[1] < 5:
        action = f"盯盘：{nearest[0]}还差{nearest[1]:.1f}%"
    else:
        action = "无"

    head = "今日预案" if mode == "morning" else "今日动作"
    return f"方向：{weather}（{score}分）| 仓位：{pos_advice} | {head}：{action}"


def checklist(dash, hl, account=None):
    """✅⚠️❌操作清单。✅=已触发可执行 / ⚠️=距触发位<5%盯盘 / ❌=禁止动作"""
    account = account or load_config().get("account", {})
    items = []
    for h in hl:
        for t in h["triggers"]:
            if t["hit"]:
                items.append(("✅", f"{h['name']}已触及{t['label']} → {t['action']}"))
            elif t["dist_pct"] < 5:
                items.append(("⚠️", f"{h['name']}距{t['label']}仅{t['dist_pct']:.1f}%，盯盘准备"))
    pos_pct = account.get("position_pct", 0)
    max_pct = account.get("max_position_pct", 70)
    if pos_pct > max_pct:
        items.append(("❌", f"禁止补仓/加仓：仓位{pos_pct}%超{max_pct}%上限"))
    cash = account.get("cash", 0)
    min_amt = account.get("min_trade_amount", 3000)
    if cash < min_amt:
        items.append(("❌", f"不做T：可用资金{cash:.0f}元低于单笔下限{min_amt}元"))
    items.append(("❌", "不做T：回测证伪（中天倒T 2024-26测试段全亏，胜率25-42%，2026-08-14回测）"))
    if not items:
        items.append(("✅", "无触发，持有观察"))
    return items


def fmt_checklist(items):
    return "\n".join(f"{icon} {text}" for icon, text in items)
