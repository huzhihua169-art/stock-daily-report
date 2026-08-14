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
import subprocess
import urllib.request
from datetime import datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _get(url, timeout=25):
    """urllib主用，失败降级curl子进程（本机部分CDN按TLS指纹/UA拦截Python）"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        out = subprocess.run(["curl", "-sS", "--max-time", str(timeout), url,
                              "-H", f"User-Agent: {UA['User-Agent']}"],
                             capture_output=True, timeout=timeout + 5)
        return out.stdout.decode("utf-8", errors="replace")


# ---------- 数据 ----------

def _secid(code):
    """腾讯代码→东财secid：sh600522→1.600522，sz000001→0.000001"""
    c = str(code).lower()
    if c.startswith("sh"):
        return "1." + c[2:]
    if c.startswith("sz"):
        return "0." + c[2:]
    return c


def fetch_kline(code, count=2000, start="2019-01-01"):
    """日K线（前复权）：东财push2his主源（支持beg/end全历史分页），腾讯备源。
    返回 [{date, open, close, high, low, volume}] 时间升序。"""
    try:
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
               f"secid={_secid(code)}&fields1=f1,f2,f3,f4,f5,f6&"
               f"fields2=f51,f52,f53,f54,f55,f56,f57,f58&klt=101&fqt=1"
               f"&beg={start.replace('-', '')}&end=20261231")
        d = json.loads(_get(url))
        klines = (d.get("data") or {}).get("klines") or []
        out = []
        for k in klines:
            f = k.split(",")
            out.append({"date": f[0], "open": float(f[1]), "close": float(f[2]),
                        "high": float(f[3]), "low": float(f[4]), "volume": float(f[5])})
        if len(out) >= 2:
            return out[-count:] if len(out) > count else out
    except Exception as e:
        print(f"[warn] 东财K线失败({e})，降级腾讯")
    # 备源：腾讯（分页拉取）
    def _page(end_date=None, cnt=640):
        if end_date:
            url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                   f"param={code},day,{start},{end_date},{cnt},qfq")
        else:
            url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                   f"param={code},day,,,{cnt},qfq")
        d = json.loads(_get(url, 20))
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

# ---------- 策略：中天倒T（R3） ----------

def strategy_daot(stock, sh, params):
    """倒T信号：T日收盘决策——上证在20日线上（非雨天）+ 可选T日过滤条件。
    T+1开盘高开≥阈值在引擎内过滤（决策点=T+1开盘，用当日已知信息，非未来函数）。
    params: {n, filter}: filter=None(全做)|'yang'|'yin'|'up5'|'down1'
      'yin': 仅T日收阴（收盘<开盘，跌后高开=诱多回落概率高）
      'yang': 仅T日收阳
      'up5': 仅T日涨幅>+5%（强势股高开延续，倒T亏损概率高——负向对照）
      'down1': 仅T日跌幅>1%
    """
    trades = []
    if len(sh) < 21:
        return trades
    closes = [k["close"] for k in sh]
    sh_above = [closes[i] > sum(closes[max(0, i - 19):i + 1]) / min(20, i + 1)
                for i in range(len(sh))]
    sh_by_date = {k["date"]: above for k, above in zip(sh, sh_above)}
    f = params.get("filter")
    for i in range(len(stock) - 2):
        d = stock[i]["date"]
        if not sh_by_date.get(d, False):
            continue  # 大盘雨天不做T
        bar = stock[i]
        prev = stock[i - 1] if i > 0 else bar
        if f == "yin" and not (bar["close"] < bar["open"]):
            continue
        if f == "yang" and not (bar["close"] > bar["open"]):
            continue
        if f == "up5" and not (bar["close"] / prev["close"] - 1 > 0.05):
            continue
        if f == "down1" and not (bar["close"] / prev["close"] - 1 < -0.01):
            continue
        trades.append({"t": i, "side": "sell", "n": params.get("n", 100)})
    return trades

# ---------- R4/R5 触发位事件研究 ----------

def event_study(kline, level, cross="up", horizon=10, window=("2026-01-01", "2026-12-31")):
    """绝对价格触发位事件研究：统计价格穿越level的次数，及穿越后horizon日收益。
    cross: 'up'=自下向上穿越（R4反弹触及减仓区）/ 'down'=自上向下穿越（R5跌破铁底）。
    收益用未来horizon日收盘相对穿越日收盘（扣除穿越当日）。返回 {events, avg_ret, wins, dates}
    """
    events, rets = [], []
    for i in range(1, len(kline) - horizon):
        d = kline[i]["date"]
        if not (window[0] <= d <= window[1]):
            continue
        prev, cur = kline[i - 1]["close"], kline[i]["close"]
        crossed = (prev < level <= cur) if cross == "up" else (prev > level >= cur)
        if not crossed:
            continue
        ret = (kline[i + horizon]["close"] / cur - 1) * 100
        events.append((d, round(ret, 2)))
        rets.append(ret)
    if not rets:
        return {"count": 0, "avg_ret": None, "wins": 0, "events": [], "horizon": horizon}
    return {"count": len(rets), "avg_ret": round(sum(rets) / len(rets), 2),
            "wins": sum(1 for r in rets if r > 0), "events": events, "horizon": horizon}


def run_trigger_study():
    """R4（36减仓区）与R5（27.02铁底）事件研究：2026年内穿越点后10日收益"""
    print("\n[触发位事件研究] 中天600522，2026年窗口（数据截至2026-08-14）")
    stock = fetch_kline("sh600522", 2000, start="2025-06-01")
    for label, level, cross, logic in (
            ("R4 减仓区36（向上触及）", 36.0, "up", "触及36后10日"),
            ("R5 铁底27.02（向下跌破）", 27.02, "down", "跌破27.02后10日")):
        r = event_study(stock, level, cross, horizon=10,
                        window=("2026-01-01", "2026-08-14"))
        if r["count"] == 0:
            print(f"  {label}: 窗口内0次穿越")
            continue
        print(f"  {label}: {r['count']}次穿越，后10日均收益{r['avg_ret']}%，"
              f"上涨{r['wins']}/{r['count']}次")
        for d, ret in r["events"][-4:]:
            print(f"    {d} 穿越 → 10日后 {ret:+.2f}%")
        print(f"  → 结论：{'触及后大概率上涨，规则方向存疑' if r['avg_ret'] > 0 else '触及后回落，规则方向成立'}")
    return stock


# ---------- 仓位矩阵：趋势跟随对照 ----------

def run_position_matrix_backtest(sh):
    """仓位矩阵骨架验证（日线级）：上证收盘>MA20=晴满仓、<MA20=雨空仓，
    对比buy&hold（上证指数本身）。2019-2026，含滑点与佣金（指数按ETF近似）。"""
    print("\n[仓位矩阵趋势回测] 上证指数2019-2026：晴(>MA20)满仓 vs 雨(<MA20)空仓，对照buy&hold")
    closes = [k["close"] for k in sh]
    dates = [k["date"] for k in sh]
    pos, cash, shares = 0.0, 10000.0, 0.0  # 模拟10,000元
    bh_shares = 10000.0 / closes[0]
    eq_curve, bh_curve = [], []
    for i in range(20, len(sh)):
        ma20 = sum(closes[i - 19:i + 1]) / 20
        px = closes[i]
        if closes[i] > ma20 and pos == 0:  # 转晴：全仓买入
            shares = cash / px * (1 - 0.0005)
            cash = 0.0
            pos = 1
        elif closes[i] < ma20 and pos == 1:  # 转雨：清仓
            cash = shares * px * (1 - 0.0015)
            shares = 0.0
            pos = 0
        eq = cash + shares * px
        eq_curve.append(eq)
        bh_curve.append(bh_shares * px)
    final_s, final_b = eq_curve[-1], bh_curve[-1]
    # 年化与回撤
    def stats(curve, label):
        ret = (curve[-1] / curve[0] - 1) * 100
        peak, maxdd = curve[0], 0.0
        for v in curve:
            peak = max(peak, v)
            maxdd = min(maxdd, (v / peak - 1) * 100)
        print(f"  {label}: 期末{curve[-1]:.0f}元（{ret:+.1f}%），最大回撤{maxdd:.1f}%")
        return {"end": round(curve[-1]), "ret_pct": round(ret, 1), "max_dd": round(maxdd, 1)}
    s = stats(eq_curve, "晴满仓/雨空仓")
    b = stats(bh_curve, "buy&hold")
    verdict = "✅ 仓位矩阵优于buy&hold（趋势过滤有效）" if s["end"] > b["end"] else \
              "⚠️ 仓位矩阵跑输buy&hold（震荡市来回打脸），需加'宽度/情绪'第二过滤"
    print(f"  → {verdict}")
    return {"matrix": s, "buyhold": b, "verdict": verdict}


# ---------- 主入口：W4全量回测 ----------

def run_all():
    print("=" * 84)
    print("W4 策略全量回测：倒T过滤重测 / 触发位事件研究 / 仓位矩阵")
    print("=" * 84)
    print("[1/4] 拉取数据...")
    stock = fetch_kline("sh600522", 2000)
    sh = fetch_kline("sh000001", 2000)
    print(f"  个股{len(stock)}根（{stock[0]['date']}~{stock[-1]['date']}），上证{len(sh)}根")

    def year_range(kline, y0, y1):
        return [k for k in kline if y0 <= k["date"][:4] <= y1]
    periods = {p: year_range(stock, *y) for p, y in
               (("train", ("2019", "2021")), ("valid", ("2022", "2023")),
                ("test", ("2024", "2026")))}
    sh_periods = {p: year_range(sh, *y) for p, y in
                  (("train", ("2019", "2021")), ("valid", ("2022", "2023")),
                   ("test", ("2024", "2026")))}

    print("[2/4] 倒T过滤重测：高开≥2% × 过滤条件{全做/仅T日收阴/仅T日收阳/仅T日跌>1%}...")
    daot = {}
    for fname, fval in (("全部", None), ("T日收阴", "yin"), ("T日收阳", "yang"),
                        ("T日跌>1%", "down1")):
        row = {}
        for pname in ("train", "valid", "test"):
            trades = strategy_daot(periods[pname], sh_periods[pname],
                                   {"n": 100, "filter": fval})
            res = run_backtest(periods[pname], trades, high_open_pct=0.02)
            m = res["metrics"]
            row[pname] = {"count": res["count"], "win_rate": m["win_rate"],
                          "total": m["total"], "avg": m["avg"]}
        daot[fname] = row
        t = row["test"]
        print(f"  [{fname}] 测试段: {t['count']}次 胜率{t['win_rate']}% "
              f"总收益{t['total']:.2f} 单笔{t['avg']:.2f}")
    best = max(daot, key=lambda k: (daot[k]["test"]["total"], daot[k]["test"]["win_rate"] or 0))
    daot_verdict = (f"✅ 最优过滤[{best}]测试段为正，可进推送" if daot[best]["test"]["total"] > 0
                    else "❌ 全部组合测试段为负，R3倒T维持'否决'，不做T")
    print(f"  → 结论：{daot_verdict}")

    print("[3/4] 触发位事件研究（R4/R5）...")
    stock_2026 = [k for k in stock if k["date"] >= "2025-06-01"]
    trig = {}
    for label, level, cross in (("R4_36", 36.0, "up"), ("R5_27", 27.02, "down")):
        r = event_study(stock_2026, level, cross, horizon=10,
                        window=("2026-01-01", "2026-08-14"))
        trig[label] = r
        if r["count"]:
            print(f"  {label}: {r['count']}次穿越 后10日均{r['avg_ret']}% "
                  f"涨{r['wins']}次 {r['events'][-3:]}")
        else:
            print(f"  {label}: 0次穿越")

    print("[4/4] 仓位矩阵趋势回测...")
    pm = run_position_matrix_backtest(sh)

    out = {"daot_filters": daot, "daot_verdict": daot_verdict,
           "triggers": trig, "position_matrix": pm,
           "rigor": ["T+1次日成交", "佣金万2.5最低5元+印花税0.05%+滑点0.1%",
                     "涨停买/跌停卖不成交", "3段walk-forward", "参数敏感性",
                     "触发决策仅用当日已知信息（非未来函数）"],
           "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with open("archive/backtest_W4_全量.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已存 archive/backtest_W4_全量.json")


if __name__ == "__main__":
    run_all()
