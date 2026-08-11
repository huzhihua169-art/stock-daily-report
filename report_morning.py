"""晨报生成：数据 → DeepSeek → markdown → 企微推送 + 存档"""
import json
import os
import sys
from datetime import datetime

import data_fetcher
import llm
import notifier

# 自选股观察池（新浪代码格式），与WorkBuddy研究体系同步维护
WATCHLIST = os.environ.get("WATCHLIST", "sh600519").split(",")

PROMPT_TEMPLATE = """今天是{date}（{weekday}），请基于以下**实时抓取的数据**生成A股晨报。

## 原始数据（抓取时间 {fetch_time}）

### 主要指数（上一个交易日收盘）
{indices}

### 昨日板块涨幅榜TOP10（括号内为领涨股）
{sectors}

### 昨日涨跌停（{zt_total}只涨停 / {dt_total}只跌停，连板高度股）
{zt_top}

### 涨跌家数：涨{up} / 跌{down} / 平{flat}

### 自选观察池行情
{watchlist}

### 最新财经新闻（按时间排序）
{news}

## 输出要求（严格按此结构）
1. **隔夜与盘前要闻**：从新闻中提炼3-5条对今日A股有实质影响的（注明来源和时间）
2. **昨日市场回顾**：指数表现、量能、板块主线、涨停情绪（数据说话）
3. **自选观察池**：逐一点评，有触发条件变化的标注
4. **今日关注点**：事件日历、数据发布、风险提示（条件化表述，不下指令）
5. **风险雷达**：概率×影响×预警信号，最多4条
"""


def fmt_indices(indices):
    lines = []
    for i in indices:
        lines.append(f"- {i['name']}: 收{i['price']}（{i['chg_pct']:+}%），"
                     f"成交{i['amount_yi']}亿，区间{i['low']}-{i['high']}")
    return "\n".join(lines)


def fmt_sectors(sectors):
    return "\n".join(f"- {s['name']}: {s['chg_pct']:+}%（领涨:{s.get('leader','-')}）"
                     for s in sectors)


def fmt_zt(ztdt):
    lines = [f"- {t['name']}({t['code']}): {t['lbc']}连板，{t['sector']}，封单{t['fund_yi']}亿"
             for t in ztdt["top"]]
    head = f"涨停{ztdt['zt_total']}只 / 跌停{ztdt['dt_total']}只"
    return head + "\n" + ("\n".join(lines) or "- 无连板数据")


def fmt_watch(watchlist):
    if not watchlist:
        return "- 观察池为空"
    return "\n".join(f"- {w['name']}({w['code']}): {w['price']}（{w['chg_pct']:+}%），"
                     f"成交{w['amount_yi']}亿" for w in watchlist)


def fmt_news(news):
    return "\n".join(f"- [{n['date']}] {n['title']}（{n['media']}）" for n in news)


def main():
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "")
    if not (webhook or os.environ.get("PUSHPLUS_TOKEN", "") or os.environ.get("FEISHU_WEBHOOK_URL", "")):
        print("[warn] 缺少推送通道（PUSHPLUS_TOKEN/FEISHU_WEBHOOK_URL/WECOM_WEBHOOK_URL），降级为DRY_RUN")
        os.environ["DRY_RUN"] = "1"

    print("[1/4] 检查交易日...")
    if not data_fetcher.is_trading_day() and os.environ.get("FORCE_RUN") != "1":
        print("今天非交易日，跳过推送"); return

    print("[2/4] 抓取数据...")
    d = data_fetcher.collect_market_data(WATCHLIST)

    now = datetime.now()
    weekdays = "一二三四五六日"
    prompt = PROMPT_TEMPLATE.format(
        date=now.strftime("%Y-%m-%d"), weekday=weekdays[now.weekday()],
        fetch_time=d["now"],
        indices=fmt_indices(d["indices"]),
        sectors=fmt_sectors(d["sectors"]),
        zt_total=d["ztdt"]["zt_total"], dt_total=d["ztdt"]["dt_total"],
        zt_top=fmt_zt(d["ztdt"]),
        up=d["stats"]["up"], down=d["stats"]["down"], flat=d["stats"]["flat"],
        watchlist=fmt_watch(d["watchlist"]),
        news=fmt_news(d["news"]),
    )

    print("[3/4] 调用DeepSeek生成晨报...")
    report = llm.chat(llm.SYSTEM_PROMPT, prompt, max_tokens=8000)

    print("[4/4] 推送...")
    title = f"A股晨报 {now.strftime('%m-%d')} 周{weekdays[now.weekday()]}"
    if os.environ.get("DRY_RUN") == "1":
        print("  [DRY_RUN] 跳过推送")
    else:
        channel, n = notifier.push_report(title, report)
        print(f"推送完成（通道={channel}，{n}段）")

    os.makedirs("archive", exist_ok=True)
    path = f"archive/晨报_{now.strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> 数据抓取：{d['now']} | 模型：{llm.MODEL}\n\n{report}")
    print(f"已存档 {path}")


if __name__ == "__main__":
    main()
