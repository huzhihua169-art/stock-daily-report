"""假设-验证闭环：晨报/复盘提取假设 → 次日自动对照 → 周末统计验证率
存储：hypotheses.json
结构：
{
  "items": [
    {"id": "H20260812-1", "date": "2026-08-12", "hypothesis": "CPO龙头明日延续强势",
     "verify_date": "2026-08-13", "result": "pending|confirmed|refuted", "evidence": ""},
  ]
}
"""
import json
import os
from datetime import datetime, timedelta

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hypotheses.json")


def _load():
    if os.path.exists(FILE):
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"items": []}


def _save(data):
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clean(text):
    """清洗LLM输出中的飞书font标签等杂质"""
    import re
    text = re.sub(r"<font[^>]*>|</font>", "", text)
    return text.strip()


def add_hypotheses(hyps):
    """hyps: [(text, days), ...] 或 [dict{text, days, category, target, direction, threshold}, ...]
    category: index|sector|stock|count（W3分类型验证用）；target: 代码/板块名/null；direction: up|down"""
    data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    for h in hyps:
        if isinstance(h, dict):
            text, days = h.get("text", ""), h.get("days", 1)
            extra = {k: h[k] for k in ("category", "target", "direction", "threshold")
                     if k in h and h[k] is not None}
        else:
            text, days = h
            extra = {}
        text = _clean(text)
        if len(text) < 4:
            continue
        vid = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        item = {"id": f"H{today.replace('-', '')}-{len(data['items'])+1}",
                "date": today, "hypothesis": text,
                "verify_date": vid, "result": "pending", "evidence": ""}
        item.update(extra)
        data["items"].append(item)
        added += 1
    _save(data)
    return added


def verify_due():
    """找出到期未验证的假设，返回 [(id, date, hypothesis, category或None), ...]
    category: index|sector|stock|count（结构化假设由verify_by_type精确验证，跳过粗判）"""
    data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    due = []
    for it in data["items"]:
        if it["result"] == "pending" and it["verify_date"] <= today:
            due.append((it["id"], it["date"], it["hypothesis"], it.get("category")))
    return due


def set_result(hid, result, evidence=""):
    data = _load()
    for it in data["items"]:
        if it["id"] == hid:
            it["result"] = result
            it["evidence"] = evidence
            break
    _save(data)


def _norm_code(code):
    """股票代码加市场前缀：600522→sh600522，000001→sz000001，300862→sz300862，83xxxx→bj"""
    c = str(code or "").strip().lower()
    if not c:
        return ""
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return c


def verify_by_type(market_data):
    """分类型精确验证到期结构化假设（W3）。替代"上证代理"粗判。
    market_data: collect_market_data()当日收盘数据。
    按category取数：index→指数行情 / sector→板块榜全量匹配 / stock→个股行情 / count→涨停数vs阈值。
    evidence写真实数据；数据缺失标"未知/待核验"，不臆测。
    返回 [(id, result, evidence), ...]
    """
    import data_fetcher
    data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    judged = []
    for it in data["items"]:
        if it["result"] != "pending" or it["verify_date"] > today:
            continue
        cat = it.get("category")
        if not cat:
            continue
        direction = it.get("direction", "up")
        threshold = it.get("threshold")
        ev = ""
        ok = None
        if cat == "index":
            code = it.get("target") or "sh000001"
            q = next((x for x in market_data.get("indices", []) if x["code"] == code), None)
            if not q:
                ev = "指数数据缺失/待核验"
            else:
                ok = (q["chg_pct"] > 0) if direction == "up" else (q["chg_pct"] < 0)
                if threshold is not None:
                    ok = ok and (q["price"] >= threshold if direction == "up"
                                 else q["price"] <= threshold)
                ev = f"{q['name']}收{q['price']}（{q['chg_pct']:+.2f}%）"
        elif cat == "sector":
            name = it.get("target") or ""
            rows = data_fetcher.get_sector_rank(60)
            r = next((x for x in rows if x["name"] == name or name in x["name"]), None)
            if not r:
                ev = f"板块[{name}]未在榜单匹配/待核验"
            else:
                ok = (r["chg_pct"] > 0) if direction == "up" else (r["chg_pct"] < 0)
                if threshold is not None:
                    ok = ok and (r["chg_pct"] >= threshold if direction == "up"
                                 else r["chg_pct"] <= threshold)
                ev = f"{name}板块{r['chg_pct']:+.2f}%"
        elif cat == "stock":
            code = _norm_code(it.get("target") or "")
            qs = data_fetcher.get_quotes([code]) if code else []
            if not qs:
                ev = f"{it.get('target')}行情缺失/待核验"
            else:
                q = qs[0]
                ok = (q["chg_pct"] > 0) if direction == "up" else (q["chg_pct"] < 0)
                if threshold is not None:
                    ok = ok and (q["chg_pct"] >= threshold if direction == "up"
                                 else q["chg_pct"] <= threshold)
                ev = f"{q['name']}收{q['price']}（{q['chg_pct']:+.2f}%）"
        elif cat == "count":
            zt = (market_data.get("ztdt") or {}).get("zt_total")
            if zt is None:
                ev = "涨停数据缺失/待核验"
            else:
                ok = True
                if threshold is not None:
                    ok = (zt >= threshold) if direction == "up" else (zt <= threshold)
                ev = f"涨停{zt}只"
        else:
            ev = f"未知类别[{cat}]/待核验"
        if ok is None:
            it["result"] = "pending"
            it["evidence"] = ev
        else:
            it["result"] = "confirmed" if ok else "refuted"
            it["evidence"] = ev
            judged.append((it["id"], it["result"], ev))
    _save(data)
    return judged


def stats():
    """统计验证率：{total, confirmed, refuted, pending, rate}"""
    data = _load()
    items = data["items"]
    confirmed = sum(1 for i in items if i["result"] == "confirmed")
    refuted = sum(1 for i in items if i["result"] == "refuted")
    pending = sum(1 for i in items if i["result"] == "pending")
    judged = confirmed + refuted
    rate = round(confirmed / judged * 100) if judged else None
    return {"total": len(items), "confirmed": confirmed, "refuted": refuted,
            "pending": pending, "rate": rate}


def weekly_summary():
    """周末总结用的markdown块"""
    s = stats()
    if s["total"] == 0:
        return "本周无假设记录（待建立假设-验证闭环）"
    lines = [
        f"假设验证统计：共{s['total']}条，证实{s['confirmed']}，证伪{s['refuted']}，待验证{s['pending']}",
        f"验证率：{s['rate']}%" if s["rate"] is not None else "验证率：暂无已判",
    ]
    if s["rate"] is not None:
        lines.append(">60%=框架有效；<40%=框架有问题需调整" if s["rate"] >= 60
                     else "⚠️ 验证率<40%，判断框架需调整")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats(), ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "due":
        for d in verify_due():
            print(d)
    else:
        print(weekly_summary())
