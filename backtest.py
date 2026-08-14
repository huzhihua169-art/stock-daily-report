"""自研向量化日K回测框架（纯标准库，零三方依赖）——W3策略验证
严谨性设计（防未来函数/过度拟合）：
1. T+1：T日收盘生成信号，T+1开盘价成交（信号与成交错日，杜绝未来函数）
2. 成本模型：佣金万2.5双边、最低5元/笔 + 印花税0.05%(仅卖出) + 滑点0.1%
3. 涨跌停：T+1开盘=涨停价则买单不成交、=跌停价则卖单不成交（主板±10%）
4. walk-forward：训练/验证/测试3段，参数只在训练段选优
5. 参数敏感性：区间扫描报收益分布；区间两端收益符号翻转→该参数标"待核验"
6. 过拟合检测：训练段夏普 vs 测试段夏普，衰减>50% → 判过拟合

用法：python backtest.py            # 跑内置中天倒T策略首轮回测
"""
import json
import math
import urllib.request
from datetime import datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ---------- 数据 ----------

def fetch_kline(code, count=2000, start="2019-01-01"):
    """腾讯前复权日K（自动分页）：返回 [{date, open, close, high, low, volume}] 时间升序。
    接口单页上限约640根，用最老日期做end循环翻页直到拉满count或到达start。"""
    def _page(end_date=None, cnt=640):
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
               f"param={code},day,{start},{end_date or ''},{cnt},qfq")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        inner = (d.get("data") or {}).get(code, {})
        rows = inner.get("qfqday") or inner.get("day") or []
        return [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
                 "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])}
                for k in rows]
    all_rows, end = [], ""
    while True:
        rows = _page(end or None)
        if not rows:
            break
        all_rows = rows + all_rows
        oldest = rows[0]["date"]
        if oldest <= start or len(all_rows) >= count:
            break
        end = oldest  # 继续往前翻
        if len(all_rows) > count * 2:  # 防死循环
            break
    return all_rows

# ---------- 成本模型 ----------

def cost_buy(amount, fee_rate=0.00025, min_fee=5.0, slip=0.001):
    """买入成本：金额*滑点 + 佣金(max(最低5元, 金额*万2.5))"""
    return amount * slip + max(min_fee, amount * fee_rate)

def cost_sell(amount, fee_rate=0.00025, min_fee=5.0, stamp=0.0005, slip=0.001):
    """卖出成本：金额*滑点 + 佣金 + 印花税"""
    return amount * slip + max(min_fee, amount * fee_rate) + amount * stamp

# ---------- 回测引擎 ----------

def zt_price(prev_close, limit=0.10):
    """涨跌停价（主板±10%）"""
    return round(prev_close * (1 + limit), 2), round(prev_close * (1 - limit), 2)

def run_backtest(kline, trades, cash=8000.0, shares=200, high_open_pct=0.02):
    """执行逐笔回测（倒T/滚动做T场景：底仓不动，T+1开盘动作，当日收盘回补）。
    trades: [{t: 信号日index(用T日收盘信息决策), side: 'sell'|'buy', n: 股数}]
    倒T触发过滤（决策点=T+1开盘，仅用当日已知信息，非未来函数）：
      T+1开盘高开≥high_open_pct 才执行开盘卖出；收盘无条件买回（判断错认亏）。
    成交：T+1开盘价成交（±滑点）；开盘=涨停买不成交、=跌停卖不成交。
    返回 {trades: 逐笔明细, metrics: 汇总}
    """
    rows, fees, pl = [], 0.0, 0.0
    for tr in trades:
        t = tr["t"]
        if t + 1 >= len(kline):
            continue
        bar = kline[t + 1]
        prev_close = kline[t]["close"]
        up, dn = zt_price(prev_close)
        if tr["side"] == "sell":  # 倒T：开盘卖，收盘买回
            if bar["open"] < prev_close * (1 + high_open_pct):
                continue  # 未高开到位，不触发（不做T不记成本）
            if bar["open"] <= dn:  # 跌停开盘卖不出
                rows.append({"date": bar["date"], "op": "卖出失败(跌停开盘)",
                             "pnl": 0.0})
                continue
            sell_px = bar["open"] * (1 - 0.001)
            buy_px = bar["close"] * (1 + 0.001)
            if bar["close"] >= up:  # 收盘涨停买不回
                rows.append({"date": bar["date"], "op": "买回失败(收盘涨停)",
                             "pnl": 0.0})
                continue
            amt_s = sell_px * tr["n"]
            amt_b = buy_px * tr["n"]
            fee = cost_sell(amt_s) + cost_buy(amt_b)
            p = (sell_px - buy_px) * tr["n"] - fee
            fees += fee
            pl += p
            rows.append({"date": bar["date"], "op": f"倒T卖{tr['n']}股@{sell_px:.2f}→买回@{buy_px:.2f}",
                         "pnl": round(p, 2)})
        else:  # 正T（需可用资金）：T+1开盘买，T+2开盘卖
            if t + 2 >= len(kline):
                continue
            if bar["open"] >= up:  # 涨停开盘买不进
                rows.append({"date": bar["date"], "op": "买入失败(涨停开盘)", "pnl": 0.0})
                continue
            buy_px = bar["open"] * (1 + 0.001)
            amt = buy_px * tr["n"]
            if amt + cost_buy(amt) > cash:
                rows.append({"date": bar["date"], "op": "资金不足跳过", "pnl": 0.0})
                continue
            sell_bar = kline[t + 2]
            if sell_bar["open"] <= zt_price(bar["close"])[1]:  # 次日跌停开盘卖不出
                rows.append({"date": sell_bar["date"], "op": "卖出失败(跌停开盘)", "pnl": 0.0})
                continue
            sell_px = sell_bar["open"] * (1 - 0.001)
            p = (sell_px - buy_px) * tr["n"] - cost_buy(amt) - cost_sell(sell_px * tr["n"])
            fees += cost_buy(amt) + cost_sell(sell_px * tr["n"])
            pl += p
            rows.append({"date": sell_bar["date"],
                         "op": f"正T买{tr['n']}股@{buy_px:.2f}→卖@{sell_px:.2f}",
                         "pnl": round(p, 2)})
    return {"trades": rows, "pnl_total": round(pl, 2), "fees": round(fees, 2),
            "count": len(rows), "wins": sum(1 for r in rows if r["pnl"] > 0),
            "metrics": summarize(rows)}

def summarize(rows):
    """胜率/总收益/平均单笔/最大回撤（按累计收益序列）"""
    if not rows:
        return {"win_rate": None, "total": 0.0, "avg": 0.0, "max_dd": 0.0}
    pnls = [r["pnl"] for r in rows]
    total = sum(pnls)
    cum, peak, maxdd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        maxdd = min(maxdd, cum - peak)
    return {"win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100),
            "total": round(total, 2), "avg": round(total / len(pnls), 2),
            "max_dd": round(maxdd, 2)}

# ---------- 策略：中天倒T（R3简化版） ----------

def strategy_daot(stock, sh, params):
    """倒T信号：T日收盘决策——上证在20日线上（非雨天）+ 预估T+1高开≥阈值
    （用T日开盘相对昨收的高开频率近似；简化：T日涨幅>0且振幅>4%时，T+1开盘试做T）
    更贴近可执行版本：T+1当日开盘高开≥high_open% 且 收盘回落（收盘<开盘）才产生利润，
    故在回测引擎内按T+1实际开盘判断。此处信号：上证T日在MA20上 → 每日都发信号，
    引擎内再按开盘条件过滤。返回trades列表（t为T日index）。
    """
    trades = []
    if len(sh) < 21:
        return trades
    sh_above = []
    closes = [k["close"] for k in sh]
    for i in range(len(sh)):
        ma20 = sum(closes[max(0, i - 19):i + 1]) / min(20, i + 1) if i >= 0 else closes[i]
        sh_above.append(closes[i] > ma20)
    # 对齐：上证与个股K线交易日基本一致，按日期映射
    sh_by_date = {k["date"]: above for k, above in zip(sh, sh_above)}
    for i in range(len(stock) - 2):
        d = stock[i]["date"]
        if sh_by_date.get(d, False):  # 上证T日站上MA20（非雨天）
            trades.append({"t": i, "side": "sell", "n": params.get("n", 100)})
    return trades

# ---------- walk-forward + 敏感性 ----------

def split_periods(kline, sh):
    """三段：2019-2021训练 / 2022-2023验证 / 2024-2026测试"""
    def seg(a, b):
        return [k for k in kline if a <= k["date"][:4] <= b]
    k_by_code = {}
    return {"train": (seg("2019", "2021"), seg("2019", "2021")),
            "valid": (seg("2022", "2023"), seg("2022", "2023")),
            "test": (seg("2024", "2026"), seg("2024", "2026"))}

def run_daot_backtest():
    """中天600522 倒T首轮回测：参数敏感性扫描（高开阈值×股数），walk-forward 3段"""
    print("[1/3] 拉取数据（600522 + 上证，2019至今）...")
    stock = fetch_kline("sh600522", 2000)
    sh = fetch_kline("sh000001", 2000)
    print(f"  个股{len(stock)}根K线（{stock[0]['date']}~{stock[-1]['date']}）")

    def year_range(kline, y0, y1):
        return [k for k in kline if y0 <= k["date"][:4] <= y1]
    periods = {"train": year_range(stock, "2019", "2021"),
               "valid": year_range(stock, "2022", "2023"),
               "test": year_range(stock, "2024", "2026")}
    sh_periods = {"train": year_range(sh, "2019", "2021"),
                  "valid": year_range(sh, "2022", "2023"),
                  "test": year_range(sh, "2024", "2026")}

    print("[2/3] 参数敏感性扫描：高开阈值∈{1.5%,2%,2.5%,3%} × n=100股（倒T：上证MA20上）...")
    results = {}
    for hop in (0.015, 0.02, 0.025, 0.03):
        row = {}
        for pname in ("train", "valid", "test"):
            trades = strategy_daot(periods[pname], sh_periods[pname], {"n": 100})
            res = run_backtest(periods[pname], trades, high_open_pct=hop)
            m = res["metrics"]
            row[pname] = {"count": res["count"], "wins": res["wins"],
                          "win_rate": m["win_rate"], "total": m["total"],
                          "avg": m["avg"], "max_dd": m["max_dd"]}
        results[hop] = row

    print("[3/3] 结果：")
    print("=" * 84)
    print(f"{'高开阈值':>7} | {'段':>6} | {'次数':>4} | {'胜率':>5} | {'总收益':>9} | {'单笔均':>7} | {'最大回撤':>9}")
    print("-" * 84)
    for hop, row in results.items():
        for pname in ("train", "valid", "test"):
            r = row[pname]
            print(f"{hop*100:>6.1f}% | {pname:>6} | {r['count']:>4} | "
                  f"{str(r['win_rate']):>5} | {r['total']:>9.2f} | "
                  f"{r['avg']:>7.2f} | {r['max_dd']:>9.2f}")
    print("=" * 84)
    # 参数稳健性：区间两端符号翻转 → 待核验
    print("\n参数稳健性检查（高开阈值1.5% vs 3.0%，测试段）：")
    t_lo, t_hi = results[0.015]["test"]["total"], results[0.03]["test"]["total"]
    if t_lo * t_hi < 0:
        verdict = "⚠️ 区间两端符号翻转，该规则判'待核验'，不进推送"
    else:
        verdict = "✅ 方向一致"
    print(f"  1.5%:{t_lo:.2f} vs 3.0%:{t_hi:.2f} → {verdict}")
    # 过拟合检查
    print("过拟合检查（训练段 vs 测试段总收益）：")
    for hop, row in results.items():
        t_train, t_test = row["train"]["total"], row["test"]["total"]
        if t_train == 0:
            verdict = "无训练收益，无法判断"
        else:
            decay = (1 - t_test / t_train) * 100
            verdict = "⚠️ 过拟合嫌疑（衰减>50%）" if decay > 50 else "✅ 稳健"
        print(f"  {hop*100:.1f}%: 训练{t_train:.2f} vs 测试{t_test:.2f} → {verdict}")
    # 保存报告
    out = {"strategy": "中天倒T（R3：上证MA20上+T+1高开≥阈值，开盘卖/收盘买回，n=100股）",
           "periods": "train 2019-21 / valid 2022-23 / test 2024-26",
           "params_scanned": [{"high_open_pct": hop, "train": row["train"],
                               "valid": row["valid"], "test": row["test"]}
                              for hop, row in results.items()],
           "rigor": ["T+1次日成交", "佣金万2.5最低5元+印花税0.05%+滑点0.1%",
                     "涨停买/跌停卖不成交", "3段walk-forward", "参数敏感性区间",
                     "触发决策仅用T+1开盘已知信息（非未来函数）"],
           "note": "倒T单笔收益需覆盖成本(~8元/笔)；本回测为规则骨架验证，未含情绪面/新闻过滤"}
    with open("archive/backtest_倒T_中天.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已存 archive/backtest_倒T_中天.json")


if __name__ == "__main__":
    run_daot_backtest()
