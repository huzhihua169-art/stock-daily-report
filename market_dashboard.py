"""市场信号仪表盘：4指标综合评分 → 天气 + 操作信号
指标：趋势(上证vs20日均线 30%) / 量能(成交vs5日均 25%) / 情绪(涨停数 25%) / 宽度(涨跌家数比 20%)
"""
import json
import urllib.request
from datetime import datetime

EM_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _get(url, timeout=12):
    import subprocess
    try:
        req = urllib.request.Request(url, headers=EM_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        out = subprocess.run(["curl", "-sS", "--max-time", str(timeout), url],
                             capture_output=True, timeout=timeout + 5)
        return out.stdout.decode("utf-8", errors="replace")


def get_sh_kline(lmt=30):
    """上证指数日K线（腾讯源，稳定）：返回 [(date, close, volume), ...] 最新在后"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{lmt},qfq")
    try:
        data = json.loads(_get(url))
        klines = (data.get("data") or {}).get("sh000001", {}).get("day") or []
        out = []
        for k in klines:
            out.append({"date": k[0], "close": float(k[2]), "volume": float(k[5])})
        return out
    except Exception:
        return []


def market_dashboard(stats, zt_total, dt_total):
    """综合评分 → 天气 + 信号。
    stats: {up, down, flat}；zt_total/dt_total: 涨停/跌停数。
    返回 dict: score/weather/signal/parts
    """
    parts = {}
    kline = get_sh_kline(30)

    # 1. 趋势 30分：上证收盘 vs 20日均线
    if kline:
        closes = [k["close"] for k in kline]
        ma20 = sum(closes[-20:]) / 20
        cur = closes[-1]
        if cur > ma20:
            parts["趋势"] = (30, f"上证{cur:.0f}>20日线{ma20:.0f}")
        else:
            parts["趋势"] = (5, f"上证{cur:.0f}<20日线{ma20:.0f}")
    else:
        parts["趋势"] = (15, "K线数据缺失")

    # 2. 量能 25分：今日成交量 vs 5日均
    if kline and len(kline) >= 6:
        vols = [k["volume"] for k in kline]
        today_v = vols[-1]
        ma5_v = sum(vols[-6:-1]) / 5
        ratio = today_v / ma5_v if ma5_v else 1
        if ratio >= 1.1:
            parts["量能"] = (25, f"放量({ratio:.2f}x)")
        elif ratio >= 0.9:
            parts["量能"] = (15, f"平量({ratio:.2f}x)")
        else:
            parts["量能"] = (5, f"缩量({ratio:.2f}x)")
    else:
        parts["量能"] = (12, "量能数据缺失")

    # 3. 情绪 25分：涨停数
    if zt_total >= 80:
        parts["情绪"] = (25, f"涨停{zt_total}只(火热)")
    elif zt_total >= 50:
        parts["情绪"] = (18, f"涨停{zt_total}只(活跃)")
    elif zt_total >= 30:
        parts["情绪"] = (10, f"涨停{zt_total}只(温和)")
    else:
        parts["情绪"] = (3, f"涨停{zt_total}只(冰点)")

    # 4. 宽度 20分：上涨家数占比
    total = (stats.get("up") or 0) + (stats.get("down") or 0)
    if total > 0:
        up_ratio = (stats.get("up") or 0) / total
        if up_ratio >= 0.6:
            parts["宽度"] = (20, f"普涨({up_ratio:.0%}上涨)")
        elif up_ratio >= 0.4:
            parts["宽度"] = (12, f"分化({up_ratio:.0%}上涨)")
        else:
            parts["宽度"] = (4, f"普跌({up_ratio:.0%}上涨)")
    else:
        parts["宽度"] = (10, "宽度数据缺失")

    score = sum(v[0] for v in parts.values())
    if score >= 60:
        weather = "晴"
        signal = "积极研究，可考虑操作"
    elif score >= 40:
        weather = "多云"
        signal = "观望为主，等待方向"
    else:
        weather = "雨"
        signal = "谨慎，减仓或空仓"

    return {"score": score, "weather": weather, "signal": signal,
            "parts": parts, "dt_total": dt_total,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")}


def fmt_dashboard(d):
    """格式化仪表盘为markdown"""
    lines = [f"### 市场信号：{d['weather']} | {d['score']}/100 | {d['signal']}"]
    for k, (s, note) in d["parts"].items():
        lines.append(f"- {k}：{s}分（{note}）")
    return "\n".join(lines)


if __name__ == "__main__":
    import data_fetcher
    st = data_fetcher.get_market_stats()
    zt = data_fetcher.get_zt_dt_pool()
    d = market_dashboard(st, zt["zt_total"], zt["dt_total"])
    print(fmt_dashboard(d))
