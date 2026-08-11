"""收盘复盘生成：数据 → DeepSeek → markdown → 企微推送 + 存档"""
import os
import sys
from datetime import datetime

import data_fetcher
import llm
import notifier
from report_morning import (WATCHLIST, fmt_indices, fmt_sectors, fmt_zt,
                            fmt_watch, fmt_news)

PROMPT_TEMPLATE = """今天是{date}（{weekday}），A股已收盘。请基于以下**实时抓取的收盘数据**生成收盘复盘。

## 原始数据（抓取时间 {fetch_time}）

### 今日指数收盘
{indices}

### 今日板块涨幅榜TOP10（括号内为领涨股）
{sectors}

### 今日涨跌停（{zt_total}只涨停 / {dt_total}只跌停，连板高度股）
{zt_top}

### 涨跌家数：涨{up} / 跌{down} / 平{flat}

### 自选观察池今日表现
{watchlist}

### 今日财经新闻（按时间排序）
{news}

## 输出要求（严格按此结构）
1. **今日盘面**：指数、量能、涨跌结构、涨停情绪（数据说话）
2. **主线与异动**：板块主线、资金流入流出方向、值得注意的异动
3. **自选观察池复盘**：逐一核对——价格变动、是否触及研究卡片中的触发/失效条件（如数据不足则写"需人工核对"）
4. **纪律检查清单**：提醒今日应记录的事项（交易/未操作理由/是否违反纪律）
5. **明日验证清单**：不超过5条具体可核验的事项
"""


def main():
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "")
    if not (webhook or os.environ.get("PUSHPLUS_TOKEN", "") or os.environ.get("FEISHU_WEBHOOK_URL", "")):
        print("[warn] 缺少推送通道（PUSHPLUS_TOKEN/FEISHU_WEBHOOK_URL/WECOM_WEBHOOK_URL），降级为DRY_RUN")
        os.environ["DRY_RUN"] = "1"

    print("[1/4] 检查交易日...")
    if not data_fetcher.is_trading_day() and os.environ.get("FORCE_RUN") != "1":
        print("今天非交易日，跳过推送"); return

    print("[2/4] 抓取收盘数据...")
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

    print("[3/4] 调用DeepSeek生成复盘...")
    report = llm.chat(llm.SYSTEM_PROMPT, prompt, max_tokens=8000)

    print("[4/4] 推送...")
    title = f"A股收盘复盘 {now.strftime('%m-%d')} 周{weekdays[now.weekday()]}"
    if os.environ.get("DRY_RUN") == "1":
        print("  [DRY_RUN] 跳过推送")
    else:
        channel, n = notifier.push_report(title, report)
        print(f"推送完成（通道={channel}，{n}段）")

    os.makedirs("archive", exist_ok=True)
    path = f"archive/复盘_{now.strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> 数据抓取：{d['now']} | 模型：{llm.MODEL}\n\n{report}")
    print(f"已存档 {path}")


if __name__ == "__main__":
    main()
