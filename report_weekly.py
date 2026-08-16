"""周度总结生成：本周市场回顾 + 假设验证率 + 持仓状态 + 下周关注
调度：周六 10:00（北京时间）
"""
import os
from datetime import datetime, timedelta

import data_fetcher
import hypothesis_tracker
import holdings_lights
import llm
import market_dashboard
import notifier
import stock_pool
from report_morning import WATCHLIST, fmt_indices, fmt_sectors, fmt_zt, fmt_watch, fmt_news

PROMPT_TEMPLATE = """今天是{date}（周六），请基于以下**本周数据**生成周度总结（个人A股AI投资研究中心）。

## 原始数据

### 周五收盘市场信号
{dashboard}

### 持仓状态灯
{lights}

### 周五主要指数
{indices}

### 周五板块涨幅榜TOP10
{sectors}

### 周五涨跌停（{zt_total}只涨停 / {dt_total}只跌停）
{zt_top}

### 周五涨跌家数：涨{up} / 跌{down} / 平{flat}

### 本周假设验证统计
{hypo_stats}

### 观察池行情
{watchlist}

## 输出要求（严格按此结构）
1. **本周市场回顾**：周内走势、量能变化、主线板块轮动、情绪周期（数据说话，标注数据日期）
2. **假设验证审计**：逐条列出本周验证的假设结果，统计验证率；>60%说明框架有效，<40%指出可能的问题
3. **持仓复盘**：中天科技本周表现、触发条件核对（27铁底/36-38减仓区/H1正式报），通信ETF状态
4. **候选池表现**：本周推荐过的股票池命中情况（回顾、总结规律）
5. **下周关注**：事件日历（财报、数据发布、解禁）、待验证问题
6. **纪律检查**：本周是否有违反纪律的行为记录（满仓、追高等），下周改进点
"""


def main():
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "")
    if not (webhook or os.environ.get("PUSHPLUS_TOKEN", "") or os.environ.get("FEISHU_WEBHOOK_URL", "")):
        print("[warn] 缺少推送通道，降级为DRY_RUN")
        os.environ["DRY_RUN"] = "1"

    print("[1/4] 抓取周五收盘数据...")
    d = data_fetcher.collect_market_data(WATCHLIST)

    dash = market_dashboard.market_dashboard(d["stats"], d["ztdt"]["zt_total"], d["ztdt"]["dt_total"])
    lights = holdings_lights.fmt_lights(holdings_lights.holdings_lights(d["watchlist"]))
    hypo_stats = hypothesis_tracker.weekly_summary()

    now = datetime.now()
    prompt = PROMPT_TEMPLATE.format(
        date=now.strftime("%Y-%m-%d"),
        dashboard=market_dashboard.fmt_dashboard(dash),
        lights=lights,
        indices=fmt_indices(d["indices"]),
        sectors=fmt_sectors(d["sectors"]),
        zt_total=d["ztdt"]["zt_total"], dt_total=d["ztdt"]["dt_total"],
        zt_top=fmt_zt(d["ztdt"]),
        up=d["stats"]["up"], down=d["stats"]["down"], flat=d["stats"]["flat"],
        hypo_stats=hypo_stats,
        watchlist=fmt_watch(d["watchlist"]),
    )

    print("[2/4] 调用DeepSeek生成周度总结...")
    report = llm.chat(llm.SYSTEM_PROMPT, prompt, max_tokens=8000)

    print("[3/4] 推送...")
    title = f"A股周度总结 {now.strftime('%m-%d')}"
    if os.environ.get("DRY_RUN") == "1":
        print("  [DRY_RUN] 跳过推送")
    else:
        template, signal_md = market_dashboard.fmt_visual(dash)
        channel, n = notifier.push_visual(
            title, template, signal_md, lights, report,
            note=f"数据 {d['now']} | 模型 {llm.MODEL} | 仅供研究参考")
        print(f"推送完成（通道={channel}，{n}段）")

    print("[4/4] 存档...")
    os.makedirs("archive", exist_ok=True)
    path = f"archive/周度总结_{now.strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> 数据抓取：{d['now']} | 模型：{llm.MODEL}\n\n{report}")
    print(f"已存档 {path}")


if __name__ == "__main__":
    main()
