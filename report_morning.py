"""晨报生成：数据 → 信号仪表盘 → DeepSeek → markdown → 推送 + 存档"""
import json
import os
import sys
from datetime import datetime

import data_fetcher
import decision
import holdings_lights
import llm
import market_dashboard
import notifier

# 自选股观察池（新浪代码格式），与WorkBuddy研究体系同步维护
# 只放实际持仓：600522中天科技（600519茅台仅研究卡片，不在此推送）
WATCHLIST = os.environ.get("WATCHLIST", "sh600522").split(",")

PROMPT_TEMPLATE = """今天是{date}（{weekday}），请基于以下**实时抓取的数据**生成A股晨报。

## 原始数据（抓取时间 {fetch_time}）

### 市场信号仪表盘（昨日收盘）
{dashboard}

### 持仓状态灯
{lights}

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
0. **决策JSON块（第一段，必须）**：先输出 ```json 代码块，再输出markdown正文。JSON字段：
   {{"direction": "偏多|震荡|偏空", "probability": 55, "position_advice": "持有|加仓|减仓|空仓|只减不加",
    "basis": ["依据1(引用数据日期+数值)", "依据2", "依据3"], "invalid_if": "失效条件(必须具体可核验)",
    "actions": [{{"type": "✅|⚠️|❌", "text": "条件触发式动作"}}],
    "hypotheses": [{{"text": "今日可对错判断的假设", "category": "index|sector|stock|count",
                    "target": "sh000001|板块名|股票代码|null", "direction": "up|down",
                    "threshold": 数值或null, "days": 0}}]}}
   合规：probability限50-90；direction/probability必配invalid_if；禁"必涨/涨停"；hypotheses 1-3条。
1. **今日信号**：用一句话复述市场信号仪表盘的天气和操作建议，然后解释依据
2. **隔夜与盘前要闻**：从新闻中提炼3-5条对今日A股有实质影响的（注明来源和时间）
3. **昨日市场回顾**：指数表现、量能、板块主线、涨停情绪（数据说话）
4. **自选观察池**：逐一点评，有触发条件变化的标注，对照持仓状态灯
5. **今日关注点**：事件日历、数据发布、风险提示（条件化表述，不下指令）
6. **风险雷达**：概率×影响×预警信号，最多4条
7. **持仓专项监控（中天600522 + 光迅002281）**：对照持仓状态灯触发位距离（中天减仓区36/铁底27.02；光迅成本232.64待定稿）。若有隔夜重大信息（公告、光模块/光纤涨价、美国/欧盟政策、海外映射股异动、龙虎榜）单独醒目标注并给一句关键判断（事实→含义→待验证）。若现价距任一触发位<5%，写进"今日关注点"置顶。提示距8/20光迅中报、8/28中天中报各剩几天。
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

    # 信号仪表盘 + 持仓灯
    dash = market_dashboard.market_dashboard(d["stats"], d["ztdt"]["zt_total"], d["ztdt"]["dt_total"])
    hl = holdings_lights.holdings_lights(d["watchlist"])
    lights = holdings_lights.fmt_lights(hl)

    now = datetime.now()
    weekdays = "一二三四五六日"
    prompt = PROMPT_TEMPLATE.format(
        date=now.strftime("%Y-%m-%d"), weekday=weekdays[now.weekday()],
        fetch_time=d["now"],
        dashboard=market_dashboard.fmt_dashboard(dash),
        lights=lights,
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

    # W2判断层：提取决策JSON块 → 存档 + 假设入库（days=0当日收盘验证）
    import json as _json
    dec = llm.extract_json_block(report)
    body = llm.strip_json_block(report)
    if dec:
        os.makedirs("archive", exist_ok=True)
        with open(f"archive/决策_晨报_{now.strftime('%Y-%m-%d')}.json", "w", encoding="utf-8") as f:
            _json.dump(dec, f, ensure_ascii=False, indent=2)
        hyps = [h for h in (dec.get("hypotheses") or []) if h.get("text")]
        if hyps:
            import hypothesis_tracker
            n_h = hypothesis_tracker.add_hypotheses(hyps)
            print(f"决策块入库{n_h}条结构化假设 → archive/决策_晨报_{now.strftime('%Y-%m-%d')}.json")
        act_lines = "\n".join(f"- {a.get('type', '')} {a.get('text', '')}"
                              for a in dec.get("actions") or [])
        dec_summary = (f"**决策摘要**：方向={dec.get('direction', '未知')}"
                       f"（概率{dec.get('probability', '-')}%）| "
                       f"仓位建议={dec.get('position_advice', '未知')}\n"
                       f"失效条件：{dec.get('invalid_if', '未知')}\n{act_lines}")
        body = dec_summary + "\n\n---\n\n" + body
    else:
        print("[warn] 未解析到决策JSON块，正文照常推送")

    print("[4/4] 推送...")
    weather_emoji = {"晴": "☀️", "多云": "⛅", "雨": "🌧️"}[dash["weather"]]
    title = f"{weather_emoji} A股晨报 {now.strftime('%m-%d')} | {dash['weather']} {dash['score']}分"
    concl = decision.conclusion(dash, hl, mode="morning")
    cl_md = decision.fmt_checklist(decision.checklist(dash, hl))
    if os.environ.get("DRY_RUN") == "1":
        print("  [DRY_RUN] 跳过推送")
        print("  结论:", concl)
        print("  清单:\n" + cl_md)
        if dec:
            print("  决策块: 方向=%s 概率=%s 仓位=%s 失效=%s" % (
                dec.get("direction"), dec.get("probability"),
                dec.get("position_advice"), dec.get("invalid_if")))
    else:
        template, signal_md = market_dashboard.fmt_visual(dash)
        channel, n = notifier.push_decision_card(
            title, template, concl, signal_md, cl_md, lights, body,
            note=f"数据 {d['now']} | 模型 {llm.MODEL} | 条件化判断，非投资建议")
        print(f"推送完成（通道={channel}，{n}段）")

    os.makedirs("archive", exist_ok=True)
    path = f"archive/晨报_{now.strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> 数据抓取：{d['now']} | 模型：{llm.MODEL}\n\n"
                f"**{concl}**\n\n{cl_md}\n\n---\n\n{body}")
    print(f"已存档 {path}")


if __name__ == "__main__":
    main()
