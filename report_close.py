"""收盘复盘生成：数据 → 信号仪表盘 → DeepSeek → markdown → 推送 + 存档"""
import os
import sys
from datetime import datetime

import data_fetcher
import decision
import hypothesis_tracker
import holdings_lights
import llm
import market_dashboard
import notifier
import stock_pool
from report_morning import (WATCHLIST, fmt_indices, fmt_sectors, fmt_zt,
                            fmt_watch, fmt_news)

PROMPT_TEMPLATE = """今天是{date}（{weekday}），A股已收盘。请基于以下**实时抓取的收盘数据**生成收盘复盘。

## 原始数据（抓取时间 {fetch_time}）

### 市场信号仪表盘
{dashboard}

### 持仓状态灯
{lights}

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

### 昨日假设待验证（如有到期未验证项，请结合今日数据给出证实/证伪结论）
{hypo_due}

### 今日候选股票池（规则筛选，非买卖建议）
{pool}

## 输出要求（严格按此结构）
0. **决策JSON块（第一段，必须）**：先输出 ```json 代码块，再输出markdown正文。JSON字段：
   {{"direction": "偏多|震荡|偏空", "probability": 55, "position_advice": "持有|加仓|减仓|空仓|只减不加",
    "basis": ["依据1(引用数据日期+数值)", "依据2", "依据3"], "invalid_if": "失效条件(必须具体可核验)",
    "actions": [{{"type": "✅|⚠️|❌", "text": "条件触发式动作，如 若放量破27.02则减仓"}}],
    "hypotheses": [{{"text": "可对错判断的假设", "category": "index|sector|stock|count",
                    "target": "sh000001|板块名|股票代码|null", "direction": "up|down",
                    "threshold": 数值或null, "days": 1}}]}}
   合规：probability限50-90；direction/probability必配invalid_if；禁"必涨/涨停"；hypotheses每条1-3条。
1. **今日盘面**：指数、量能、涨跌结构、涨停情绪（数据说话）
2. **主线与异动**：板块主线、资金流入流出方向、值得注意的异动
3. **自选观察池复盘**：逐一核对——价格变动、是否触及研究卡片中的触发/失效条件（如数据不足则写"需人工核对"）
3.5 **中天科技(600522)专项监控**：对照持仓状态灯触发位距离（减仓区36/铁底27.02）。触及/接近任一触发位（<5%）必须醒目置顶，写动作含义（减仓区=减100股首选路径；放量破27.02=处置）。今日有中天重大信息（公告、中报、龙虎榜、融资余额、海外政策、大额资金异动）单独列出给一句关键判断。提示距8/28中报核验还剩几天。
4. **候选池点评**：对候选股票池逐只给出明日可验证的涨跌假设（如"百花医药明日收涨/收跌"），每只一条，必须可对错判断
5. **纪律检查清单**：提醒今日应记录的事项（交易/未操作理由/是否违反纪律）
6. **四层框架结论**（基金经理视角，每层一句话，引用数据）：环境（仪表盘分数/天气）→ 仓位（对照结论区仓位建议）→ 结构（板块主线）→ 个股（持仓灯+候选池）
7. **明日验证清单**：不超过5条具体可核验的事项（每一条都必须写成"可对错判断"的假设，如"XX板块明日上涨/下跌"，不要写模糊描述）。⚠️ 持仓标的（中天科技600522）**禁止**写成"明日收涨/收跌"式无后果假设，只写触发位判断：若现价在27-36持有区内，写"无动作——仍在27-36持有区，距减仓区还差X%、距铁底还有Y%缓冲"；仅当触及36或27.02时才写可验证动作假设。
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

    # 信号仪表盘 + 持仓灯 + 假设验证 + 推荐池
    dash = market_dashboard.market_dashboard(d["stats"], d["ztdt"]["zt_total"], d["ztdt"]["dt_total"])
    hl = holdings_lights.holdings_lights(d["watchlist"])
    lights = holdings_lights.fmt_lights(hl)
    due = hypothesis_tracker.verify_due()
    hypo_due = "\n".join(f"- [{i[0]}]({i[1]}) {i[2]}" for i in due) if due else "- 无到期假设"
    pool, active_hot = stock_pool.recommend_pool(5)

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
        hypo_due=hypo_due,
        pool=stock_pool.fmt_pool(pool, active_hot),
    )

    print("[3/4] 调用DeepSeek生成复盘...")
    report = llm.chat(llm.SYSTEM_PROMPT, prompt, max_tokens=8000)

    # W2判断层：提取决策JSON块 → 存档 + 结构化假设入库；正则仅兜底
    import json as _json
    dec = llm.extract_json_block(report)
    body = llm.strip_json_block(report)
    if dec and dec.get("hypotheses"):
        os.makedirs("archive", exist_ok=True)
        with open(f"archive/决策_{now.strftime('%Y-%m-%d')}.json", "w", encoding="utf-8") as f:
            _json.dump(dec, f, ensure_ascii=False, indent=2)
        hyps = [h for h in dec["hypotheses"] if h.get("text")]
        if hyps:
            n_h = hypothesis_tracker.add_hypotheses(hyps)
            print(f"决策块入库{n_h}条结构化假设 → archive/决策_{now.strftime('%Y-%m-%d')}.json")
    else:
        print("[warn] 未解析到决策JSON块，走正则兜底")

    print("[4/4] 推送...")
    weather_emoji = {"晴": "☀️", "多云": "⛅", "雨": "🌧️"}[dash["weather"]]
    title = f"{weather_emoji} A股复盘 {now.strftime('%m-%d')} | {dash['weather']} {dash['score']}分"
    concl = decision.conclusion(dash, hl, mode="close")
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
    path = f"archive/复盘_{now.strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> 数据抓取：{d['now']} | 模型：{llm.MODEL}\n\n"
                f"**{concl}**\n\n{cl_md}\n\n---\n\n{body}")
    print(f"已存档 {path}")

    # 正则兜底：仅在决策JSON块未产出假设时执行（避免重复入库）
    import re
    if not (dec and dec.get("hypotheses")):
        # 从复盘报告的"明日验证清单"提取假设存入追踪表
        m = re.search(r"(?:[一二三四五六七八九十]、|#+\s*[0-9.]*\s*)\s*明日验证清单\s*\n(.*?)(?=\n\s*(?:[一二三四五六七八九十]、|#+\s*[0-9.]*\s*)|$)", report, re.S)
        if not m:
            m = re.search(r"明日验证清单\s*\n(.*?)(?=\n\s*(?:[一二三四五六七八九十]、|#+\s*[0-9.]*\s*)|$)", report, re.S)
        if m:
            items = [ln.strip().lstrip("0123456789.、) ") for ln in m.group(1).split("\n")
                     if ln.strip() and not ln.strip().startswith(("```", "⚠️", "*", "- **"))]
            hyps = [(it, 1) for it in items if len(it) > 4 and "以下假设" not in it
                    and "无动作" not in it
                    and not it.startswith(("以下", "说明", "注"))][:5]
            if hyps:
                n_h = hypothesis_tracker.add_hypotheses(hyps)
                print(f"已记录{n_h}条明日假设到追踪表")

        # 从"候选池点评"提取推荐假设（带代码，验证更精确）
        m2 = re.search(r"(?:[一二三四五六七八九十]、|#+\s*[0-9.]*\s*)\s*候选池点评\s*\n(.*?)(?=\n\s*(?:[一二三四五六七八九十]、|#+\s*[0-9.]*\s*)|$)", report, re.S)
        if m2:
            pool_hyps = []
            for ln in m2.group(1).split("\n"):
                ln = ln.strip().lstrip("0123456789.、) ")
                if len(ln) > 6 and not ln.startswith(("以下", "说明", "注", "⚠")):
                    pool_hyps.append(ln)
            if pool_hyps:
                n2 = hypothesis_tracker.add_hypotheses([(h, 1) for h in pool_hyps[:5]])
                print(f"已记录{n2}条推荐池假设到追踪表")

    # 自动验证到期假设：结合当日指数涨跌粗略判定（W3将替换为分类型精确验证）
    # 带category的结构化假设（W2起）跳过粗判，留给分类型验证
    due = hypothesis_tracker.verify_due()
    if due:
        sh_chg = None
        for idx in d["indices"]:
            if idx["code"] == "sh000001":
                sh_chg = idx["chg_pct"]
        for hid, hdate, htext, hcat in due:
            if hcat:
                continue
            up_words = ("涨", "强", "延续", "回升", "突破", "反弹", "上行", "修复", "晋级")
            down_words = ("跌", "弱", "回落", "破位", "下探", "回调", "退潮", "断板")
            if sh_chg is not None:
                if any(w in htext for w in up_words) and sh_chg > 0:
                    hypothesis_tracker.set_result(hid, "confirmed", f"上证{sh_chg:+.2f}%")
                elif any(w in htext for w in down_words) and sh_chg < 0:
                    hypothesis_tracker.set_result(hid, "confirmed", f"上证{sh_chg:+.2f}%")
                elif any(w in htext for w in up_words) and sh_chg < 0:
                    hypothesis_tracker.set_result(hid, "refuted", f"上证{sh_chg:+.2f}%")
                elif any(w in htext for w in down_words) and sh_chg > 0:
                    hypothesis_tracker.set_result(hid, "refuted", f"上证{sh_chg:+.2f}%")
        print(f"已粗判{len([x for x in due if not x[3]])}条到期假设（结构化{len([x for x in due if x[3]])}条待分类型验证）")


if __name__ == "__main__":
    main()
