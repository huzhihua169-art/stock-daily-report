"""持仓标的2026中报归母净利核验（东财datacenter免费接口，云端GitHub Actions跑）
规则（研究卡片I2节）：≥中上沿绿 / 下沿~中上沿黄 / <下沿红（否决持有）。未披露禁止用预告冒充。
标的由环境变量配置（GitHub Actions按workflow cron区分）：
  VERIFY_CODE / VERIFY_NAME / FORECAST_LOW / VERIFY_OK / LAST_YEAR_H1 / DISCLOSE_DATE
默认：中天科技600522（8/28披露，预告23.52-25.08亿，2025H1=15.677亿）
光迅科技002281（8/20披露，预告5.586-6.15亿，2025H1=3.724亿）
"""
import json
import os
from datetime import datetime
import urllib.request

import llm
import notifier

CODE = os.environ.get("VERIFY_CODE", "600522")
NAME = os.environ.get("VERIFY_NAME", "中天科技")
FORECAST_LOW = float(os.environ.get("FORECAST_LOW", "23.52"))   # 预告下沿 = 否决阈值
VERIFY_OK = float(os.environ.get("VERIFY_OK", "24.0"))          # 预告中上沿
FORECAST_HIGH = float(os.environ.get("FORECAST_HIGH", "25.08"))  # 预告上沿（显示用）
LAST_YEAR_H1 = float(os.environ.get("LAST_YEAR_H1", "15.6772998986"))  # 2025H1归母净利(亿)
DISCLOSE_DATE = os.environ.get("DISCLOSE_DATE", "2026-08-28")   # 中报预约披露日


def fetch_financials():
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           "?reportName=RPT_LICO_FN_CPD&columns=ALL"
           f"&filter=(SECURITY_CODE%3D%22{CODE}%22)"
           "&sortColumns=REPORTDATE&sortTypes=-1&pageSize=12")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("result") or {}).get("data") or []


def main():
    rows = fetch_financials()
    h1_2026 = next((r for r in rows
                    if str(r.get("REPORTDATE") or "")[:10] == "2026-06-30"), None)
    if h1_2026 is None or h1_2026.get("PARENT_NETPROFIT") is None:
        # 未披露：如实标注，禁止用预告值冒充
        facts = (f"东财datacenter截至运行时刻未返回{CODE}的2026-06-30业绩报表行。\n"
                 f"中报预约披露日={DISCLOSE_DATE}（公司互动易+东财预约双确认）。\n"
                 "结论：未披露/待核验。请于披露日收盘后手动重跑本任务或等待次日晨报。")
        title = f"{NAME}中报核验：未披露/待核验"
        template = "yellow"
    else:
        net = h1_2026.get("PARENT_NETPROFIT") / 1e8  # 元→亿元
        rev = (h1_2026.get("TOTAL_OPERATE_INCOME") or 0) / 1e8
        yoy = (net / LAST_YEAR_H1 - 1) * 100
        if net >= VERIFY_OK:
            light, template, verdict = "🟢", "green", "持有逻辑验证通过（≥24亿，预告中上沿）"
        elif net >= FORECAST_LOW:
            light, template, verdict = "🟡", "yellow", f"预告下沿附近（{net:.2f}亿，高于否决线23.52但低于中上沿24）"
        else:
            light, template, verdict = "🔴", "red", "**否决持有条件触发**（<预告下沿，研究卡片规定此情形清仓离场）"
        facts = (f"2026H1归母净利：**{net:.2f}亿元**（{light}），同比+{yoy:.1f}%"
                 f"（2025H1={LAST_YEAR_H1:.2f}亿）\n"
                 f"2026H1营业收入：{rev:.1f}亿元\n"
                 f"预告区间：{FORECAST_LOW}-{FORECAST_HIGH:.2f}亿\n"
                 f"对照结论：**{verdict}**")
        title = f"{NAME}中报核验：{light} {net:.2f}亿"

    prompt = (
        f"今天是{datetime.now().strftime('%Y-%m-%d')}，请生成{NAME}({CODE})2026年半年报核验结论。\n\n"
        f"## 已核验事实（东财datacenter，不得更改）\n{facts}\n\n"
        f"## 持仓背景\n{NAME}持仓，成本与触发位见持仓台账/研究卡片（中天：200股成本63.771，减仓区36/铁底27.02；"
        f"光迅：800股成本232.6411，8/20中报<5.586亿否决）。\n\n"
        f"## 输出要求\n1. 中报数字结论（引用上面事实，不得编造）\n"
        f"2. 对持仓的三个选项（不动/做T/减仓清仓）按研究卡片条件化判断，不给确定性买卖指令\n"
        f"3. 一句关键判断（事实→含义→下一步验证）\n"
        f"4. 文末固定附：⚠️ 本报告由AI生成，仅供研究参考，不构成投资建议。"
    )
    report = llm.chat(llm.SYSTEM_PROMPT, prompt, max_tokens=4000)

    note = f"数据 {datetime.now().strftime('%Y-%m-%d %H:%M')} 东财datacenter | 模型 {llm.MODEL}"
    if os.environ.get("DRY_RUN") == "1":
        print("[DRY_RUN]", title, "\n", facts, "\n", report)
    else:
        body = f"{facts}\n\n---\n\n{report}"
        channel, n = notifier.push_report(title, body)
        print(f"推送完成（通道={channel}，{n}段）")

    os.makedirs("archive", exist_ok=True)
    path = f"archive/h1核验_{datetime.now().strftime('%Y-%m-%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n> {note}\n\n{facts}\n\n---\n\n{report}")
    print(f"已存档 {path}")


if __name__ == "__main__":
    main()
