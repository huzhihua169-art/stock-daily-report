"""股票池推荐模块（借鉴daily_stock_analysis选股思想，免费数据源）
规则：连板情绪 + 主线领涨 + 新闻热点交叉，排除ST/新股/低流动性
推荐只作研究候选，不构成买卖建议；每只推荐自动进入假设验证闭环
"""
import json
import re

import data_fetcher

EXCLUDE_KEYWORDS = ["ST", "*ST", "退", "N", "C"]  # ST/退市/新股


def _is_excluded(name):
    return any(k in name.upper() for k in EXCLUDE_KEYWORDS)


def name_to_code(name):
    """名称反查代码（腾讯搜索接口）→ 'sh600721' 格式，失败返回None"""
    try:
        import urllib.parse
        url = "https://smartbox.gtimg.cn/s3/?v=2&q=" + urllib.parse.quote(name) + "&t=all"
        text = data_fetcher._get(url, encoding="gbk")
        m = re.search(r'v_hint="([a-z]{2})~(\d{6})~', text)
        if m:
            return f"{m.group(1)}{m.group(2)}"
    except Exception:
        pass
    return None


def _pick_leader_from_sectors(sectors, top_n=5):
    """板块涨幅榜领涨股候选（腾讯源：sector.leader=领涨股名，反查代码）"""
    out = []
    for s in sectors[:top_n]:
        if s.get("leader") and not _is_excluded(s["leader"]):
            code = name_to_code(s["leader"])
            out.append({"name": s["leader"], "code": code,
                        "source": f"板块领涨-{s['name']}"})
    return out


def _pick_from_zt_pool(ztdt, top_n=5):
    """涨停池连板股候选（东财源）"""
    out = []
    for t in ztdt.get("top", []):
        if not _is_excluded(t["name"]):
            out.append({"name": t["name"], "code": t["code"],
                        "source": f"{t['lbc']}连板-{t['sector']}"})
    return out[:top_n]


def _match_news_hotspots(candidates, news):
    """新闻热点交叉：标题含AI/半导体/机器人/算力/光模块等关键词的候选加权"""
    hotspots = ["AI", "算力", "半导体", "芯片", "机器人", "光模块", "CPO", "存储",
                "PCB", "数据中心", "液冷", "光纤", "创新药", "固态电池", "稀土"]
    text = " ".join(n["title"] for n in news)
    active = [h for h in hotspots if h.lower() in text.lower()]
    for c in candidates:
        c["hotspot"] = any(h.lower() in (c["name"] + c.get("source", "")).lower()
                           for h in active)
    return candidates, active


def recommend_pool(top_n=5):
    """生成推荐候选池：连板+领涨+热点加权，返回 [(name, code, source, hotspot), ...]"""
    try:
        ztdt = data_fetcher.get_zt_dt_pool()
    except Exception:
        ztdt = {"top": [], "zt_total": 0}
    try:
        sectors = data_fetcher.get_sector_rank(10)
    except Exception:
        sectors = []
    try:
        news = data_fetcher.get_news("A股", 8)
    except Exception:
        news = []

    cands = _pick_from_zt_pool(ztdt, top_n) + _pick_leader_from_sectors(sectors, top_n)
    # 去重（按名称），剔除无代码候选（无法进验证闭环）
    seen, uniq = set(), []
    for c in cands:
        if c["name"] not in seen and c.get("code"):
            seen.add(c["name"])
            uniq.append(c)
    uniq, active_hot = _match_news_hotspots(uniq, news)
    # 热点命中优先，其次连板高度（保持原序）
    uniq.sort(key=lambda c: (c.get("hotspot", False), c.get("lbc", 0) or 0), reverse=True)
    return uniq[:top_n], active_hot


def fmt_pool(pool, active_hot):
    """格式化推荐池为markdown"""
    if not pool:
        return "候选池为空（数据源异常）"
    hot_str = "、".join(active_hot[:6]) if active_hot else "无明确热点"
    lines = [f"**今日热点**：{hot_str}", ""]
    for i, c in enumerate(pool, 1):
        mark = "🔥" if c.get("hotspot") else "  "
        lines.append(f"{i}. {mark} **{c['name']}**（{c.get('source', '未知')}）")
    lines.append("")
    lines.append("⚠️ 以上为规则筛选的研究候选，非买卖建议；明日自动进入假设验证")
    return "\n".join(lines)


if __name__ == "__main__":
    pool, hot = recommend_pool(5)
    print(fmt_pool(pool, hot))
