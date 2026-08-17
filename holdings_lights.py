"""持仓状态灯：读positions.json配置，算触发位距离+灯色。新增持仓改配置不改代码。"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "positions.json")


def load_config(path=None):
    with open(path or _CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def holdings_lights(quotes, config=None):
    """quotes: get_quotes()结果。返回 [{name, code, price, chg_pct, cost, pnl_pct,
    lights:[展示字符串], triggers:[...], veto:[...], is_watch:bool}]"""
    cfg = config or load_config()
    by_code = {q["code"]: q for q in quotes}
    out = []

    def build(pos, is_watch=False):
        q = by_code.get(pos["code"])
        if not q:
            return None
        price = q["price"]
        lights, triggers = [], []
        for t in pos.get("triggers", []):
            level, light, action = t.get("level"), t["light"], t["action"]
            icon = "🟡" if light == "yellow" else "🔴"
            direction = t.get("direction", "up")  # up=压力位(现价涨到level触发)；down=回调位(现价跌到level触发)
            if level is None:  # 事件型触发（无价格位，如中报核验）：原样显示
                lights.append(f"{icon} {t['label']} → {action}")
                triggers.append({"label": t["label"], "level": None, "light": light,
                                 "action": action, "dist_pct": None, "hit": False})
                continue
            if direction == "down":  # 回调买入位：现价在上，报还需跌多少才到
                hit = price <= level
                dist = (price / level - 1) * 100
                txt = (f"{icon} 已回调至{t['label']}({level}) → {action}" if hit
                       else f"{icon} 距{t['label']}({level})回调位还有{dist:.1f}%空间，不动作")
            elif light == "yellow":  # 压力/目标位：现价在下，报还需涨多少
                hit = price >= level
                dist = (level / price - 1) * 100
                txt = (f"{icon} 已触及{t['label']}({level}) → {action}" if hit
                       else f"{icon} 距{t['label']}({level})还差{dist:.1f}%，不动作")
            else:  # 支撑/失效位：现价在上，报还剩多少缓冲
                hit = price <= level
                dist = (price / level - 1) * 100
                txt = (f"{icon} 已跌破{t['label']}({level}) → {action}" if hit
                       else f"{icon} 距{t['label']}({level})还有{dist:.1f}%缓冲，不动作")
            lights.append(txt)
            triggers.append({"label": t["label"], "level": level, "light": light,
                             "action": action, "dist_pct": dist, "hit": hit})
        pnl = (price / pos["cost"] - 1) * 100 if pos.get("cost") else None
        if pnl is not None:
            lights.append(f"🔴 现价{price}，成本{pos['cost']}，浮亏{pnl:.1f}%"
                          if pnl < 0 else f"🟢 现价{price}，成本{pos['cost']}，浮盈+{pnl:.1f}%")
        for v in pos.get("veto", []):
            lights.append(f"🟢 {v}")
        return {"name": pos["name"], "code": pos["code"], "price": price,
                "chg_pct": q["chg_pct"], "cost": pos.get("cost"),
                "pnl_pct": pnl, "lights": lights, "triggers": triggers,
                "veto": pos.get("veto", []), "shares": pos.get("shares"),
                "is_watch": is_watch, "note": pos.get("note", "")}

    for pos in cfg.get("positions", []):
        h = build(pos, is_watch=False)
        if h:
            out.append(h)
    for pos in cfg.get("watchlist", []):  # 观察池（非持仓，但有研究卡片/触发位）
        h = build(pos, is_watch=True)
        if h:
            out.append(h)
    return out


def fmt_lights(hl):
    """格式化持仓状态灯为markdown（持仓+观察池分块）"""
    if not hl:
        return "状态灯：暂无标的"
    lines = []
    holds = [h for h in hl if not h["is_watch"]]
    watch = [h for h in hl if h["is_watch"]]
    if holds:
        lines.append("**持仓**")
        for h in holds:
            lines.append(f"🔸 {h['name']} ({h['price']}，{h['chg_pct']:+}%)")
            for l in h["lights"]:
                lines.append(f"  - {l}")
    if watch:
        lines.append("**观察池**（非持仓，研究跟踪）")
        for h in watch:
            lines.append(f"🔸 {h['name']} ({h['price']}，{h['chg_pct']:+}%)")
            for l in h["lights"]:
                lines.append(f"  - {l}")
    return "\n".join(lines)


if __name__ == "__main__":
    import data_fetcher
    cfg = load_config()
    codes = [p["code"] for p in cfg["positions"]]
    q = data_fetcher.get_quotes(codes)
    print(fmt_lights(holdings_lights(q, cfg)))
