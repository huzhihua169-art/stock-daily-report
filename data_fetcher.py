"""免费行情与新闻数据抓取模块
数据源（全部免费无需key，多源互备，绕开被限流端点）：
- 指数/个股行情：新浪 hq.sinajs.cn（主）/ 腾讯 qt.gtimg.cn（备）
- 板块榜：腾讯 proxy.finance.qq.com（主）/ 新浪 newSinaHy（备）
- 涨停/跌停池、涨跌分布：东财 push2ex
- 新闻：东财 search-api-web
"""
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
SINA_H = {**UA, "Referer": "https://finance.sina.com.cn"}
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _get(url, headers=None, timeout=12, encoding="utf-8"):
    """urllib主用，失败降级curl子进程（部分CDN按TLS指纹拦截Python）"""
    hdrs = headers or UA
    raw = None
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception:
        try:
            cmd = ["curl", "-sS", "--max-time", str(timeout)]
            for k, v in hdrs.items():
                cmd += ["-H", f"{k}: {v}"]
            cmd.append(url)
            out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
            raw = out.stdout
        except Exception:
            return ""
    if not raw:
        return ""
    return raw.decode(encoding, errors="replace")


# ---------- 行情 ----------

def _parse_sina(text):
    out = []
    for line in text.strip().splitlines():
        m = re.match(r'var hq_str_(\w+)="([^"]*)";', line.strip())
        if not m or not m.group(2):
            continue
        f = m.group(2).split(",")
        try:
            name, open_, prev, price = f[0], float(f[1]), float(f[2]), float(f[3])
            high, low = float(f[4]), float(f[5])
            amount = float(f[9]) if f[9] else 0.0
        except (ValueError, IndexError):
            continue
        # 新浪个股/指数字段数不一致且尾逗号不统一，用正则找日期字段
        date_s = next((x for x in f if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x)), "")
        chg_pct = (price - prev) / prev * 100 if prev else 0.0
        out.append({"code": m.group(1), "name": name, "price": price,
                    "chg_pct": round(chg_pct, 2), "open": open_, "high": high,
                    "low": low, "prev": prev,
                    "amount_yi": round(amount / 1e8, 2), "date": date_s})
    return out


def _parse_tencent(text):
    out = []
    for line in text.strip().split(";"):
        m = re.search(r'v_(\w+)="([^"]*)"', line)
        if not m or not m.group(2):
            continue
        f = m.group(2).split("~")
        try:
            name, price, prev, open_ = f[1], float(f[3]), float(f[4]), float(f[5])
            high, low = float(f[33]), float(f[34])
            amount_wan = float(f[37]) if len(f) > 37 and f[37] else 0.0
            date_s = f[30][:8] if len(f) > 30 else ""
            date_s = f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]}" if len(date_s) == 8 else ""
        except (ValueError, IndexError):
            continue
        chg_pct = (price - prev) / prev * 100 if prev else 0.0
        out.append({"code": m.group(1), "name": name, "price": price,
                    "chg_pct": round(chg_pct, 2), "open": open_, "high": high,
                    "low": low, "prev": prev,
                    "amount_yi": round(amount_wan / 1e4, 2), "date": date_s})
    return out


def get_quotes(codes):
    """行情（新浪主GBK，腾讯备UTF-8）。codes如 ['sh000001','sh600519']"""
    if not codes:
        return []
    text = _get(f"https://hq.sinajs.cn/list={','.join(codes)}", SINA_H, encoding="gbk")
    out = _parse_sina(text)
    if out:
        return out
    text = _get(f"https://qt.gtimg.cn/q={','.join(codes)}")
    return _parse_tencent(text)


def is_trading_day():
    """用上证指数数据日期判断今天是否交易日"""
    try:
        q = get_quotes(["sh000001"])
        if not q or not q[0].get("date"):
            return False
        return q[0]["date"] == datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return False


# ---------- 板块 ----------

def get_sector_rank(top_n=10):
    """板块涨幅榜（腾讯主，新浪备）"""
    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/rank"
           "?l=60&p=1&t=01/averatio&o=0")
    try:
        data = json.loads(_get(url))
        items = (data.get("data") or [])
        rows = [{"name": d.get("bd_name", ""), "chg_pct": float(d.get("bd_zdf") or 0),
                 "leader": d.get("nzg_name", ""),
                 "leader_chg_pct": float(d.get("nzg_zdf") or 0)} for d in items]
        rows = [r for r in rows if r["name"]]
        if rows:
            rows.sort(key=lambda x: -x["chg_pct"])
            return rows[:top_n]
    except Exception:
        pass
    # 备用：新浪行业（GBK）
    try:
        text = _get("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
                    SINA_H, encoding="gbk")
        m = re.search(r"= (\{.*\})", text, re.S)
        data = json.loads(m.group(1))
        rows = []
        for v in data.values():
            f = v.split(",")
            if len(f) > 6:
                rows.append({"name": f[1], "chg_pct": round(float(f[5]), 2),
                             "leader": f[-1], "leader_chg_pct": None})
        rows.sort(key=lambda x: -x["chg_pct"])
        return rows[:top_n]
    except Exception:
        return []


# ---------- 涨停/跌停/涨跌分布（东财push2ex） ----------

def _em_pool(api, today, extra=""):
    url = (f"https://push2ex.eastmoney.com/{api}?ut={EM_UT}&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=200&sort=fbt%3Aasc&date={today}{extra}")
    try:
        return json.loads(_get(url)).get("data") or {}
    except Exception:
        return {}


def get_zt_dt_pool():
    """涨停池+跌停池"""
    today = datetime.now().strftime("%Y%m%d")
    zt = _em_pool("getTopicZTPool", today)
    dt = _em_pool("getTopicDTPool", today, "&sort=fund%3Aasc")
    zt_list = zt.get("pool") or []
    top = sorted(zt_list, key=lambda x: -(x.get("lbc") or 0))[:8]
    return {
        "zt_total": zt.get("tc", 0),
        "dt_total": dt.get("tc", 0),
        "top": [{"name": t["n"], "code": t["c"], "lbc": t.get("lbc", 0),
                 "sector": t.get("hybk", ""),
                 "fund_yi": round((t.get("fund") or 0) / 1e8, 2)} for t in top],
    }


def get_market_stats():
    """涨跌家数分布（东财push2ex getTopicZDFenBu）"""
    today = datetime.now().strftime("%Y%m%d")
    url = (f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={EM_UT}"
           f"&dpt=wz.ztzt&date={today}")
    try:
        data = json.loads(_get(url))
        fenbu = ((data.get("data") or {}).get("fenbu")) or []
        up = down = flat = 0
        for bucket in fenbu:
            for k, v in bucket.items():
                k = int(k)
                if k > 0:
                    up += v
                elif k < 0:
                    down += v
                else:
                    flat += v
        return {"up": up, "down": down, "flat": flat}
    except Exception:
        return {"up": None, "down": None, "flat": None}


# ---------- 新闻 ----------

def get_news(keyword="A股", count=8):
    """东财新闻搜索（免费，无需key）"""
    param = {"uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
             "client": "web", "clientType": "web", "clientVersion": "curr",
             "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                                            "pageIndex": 1, "pageSize": count,
                                            "preTag": "", "postTag": ""}}}
    url = ("https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param="
           + urllib.parse.quote(json.dumps(param)))
    text = _get(url)
    m = re.search(r"cb\((.*)\)", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    arts = (data.get("result") or {}).get("cmsArticleWebOld") or []
    clean = lambda s: re.sub(r"</?em>", "", s or "")
    return [{"date": a.get("date", ""), "title": clean(a.get("title")),
             "media": a.get("mediaName", ""), "url": a.get("url", "")} for a in arts]


# ---------- 汇总 ----------

INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300"]


def collect_market_data(watchlist_codes=None):
    """汇总一次晨报/复盘所需的全部数据（单点失败不拖垮整体）"""
    def safe(fn, default):
        try:
            return fn()
        except Exception as e:
            print(f"[warn] {fn.__name__} failed: {e}")
            return default

    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "indices": safe(lambda: get_quotes(INDEX_CODES), []),
        "sectors": safe(lambda: get_sector_rank(10), []),
        "ztdt": safe(get_zt_dt_pool, {"zt_total": 0, "dt_total": 0, "top": []}),
        "stats": safe(get_market_stats, {"up": None, "down": None, "flat": None}),
        "watchlist": safe(lambda: get_quotes(watchlist_codes or []), []),
        "news": safe(lambda: get_news("A股", 8) + get_news("政策 央行", 4), []),
    }


if __name__ == "__main__":
    d = collect_market_data(["sh600519"])
    print(json.dumps(d, ensure_ascii=False, indent=1)[:4000])
