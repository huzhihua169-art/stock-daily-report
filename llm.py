"""DeepSeek API 调用模块（OpenAI兼容协议）"""
import json
import os
import re
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _find_json_span(text):
    """最外层{}区间（括号配对，支持嵌套），无则None。返回 (start, end+1)"""
    s = text.find("{")
    if s == -1:
        return None
    depth = 0
    for i in range(s, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (s, i + 1)
    return None


def extract_json_block(text):
    """从LLM输出提取决策JSON块（优先```json代码块，兜底最外层{}）。失败返回None"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        span = _find_json_span(text)
        raw = text[span[0]:span[1]] if span else None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def strip_json_block(text):
    """从文本中删除决策JSON块（含```json围栏），正文推送不显示决策JSON"""
    span = _find_json_span(text)
    if not span:
        return text
    s, e = span
    # 若外层有```json围栏，一并删除
    prefix = text[:s]
    m = re.search(r"```(?:json)?\s*$", prefix)
    if m:
        s = m.start()
    rest = text[e:]
    m = re.search(r"^\s*```", rest)
    if m:
        e += m.end()
    return (text[:s] + text[e:]).strip()


def chat(system_prompt, user_prompt, temperature=0.3, max_tokens=8000):
    api_key = os.environ["DEEPSEEK_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # 禁用思考模式：V4 Flash默认思考会吃掉全部token导致content为空
        "thinking": {"type": "disabled"},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8"))
    usage = resp.get("usage") or {}
    msg = resp["choices"][0]["message"]
    content = msg.get("content") or ""
    # 若仍为空（如触发安全拦截），抛错而非推送空内容
    if not content:
        raise RuntimeError(f"LLM返回空content: finish={resp['choices'][0].get('finish_reason')}")
    print(f"[LLM] model={MODEL} input={usage.get('prompt_tokens')} "
          f"output={usage.get('completion_tokens')}")
    return content


SYSTEM_PROMPT = """你是"个人A股AI投资研究中心"的研究助手。规则：
1. 先证据后观点：所有关键数字必须来自用户给定的数据，注明数据时间；不得编造数据或引用训练记忆里的旧行情。
2. 区分事实/推断/假设/未知；数据缺失时写"未知/待核验"，不得猜测。
3. 方向判断合规边界：允许给出"方向+概率(50-90%)+依据+失效条件"的条件化判断；禁止"明日必涨/涨停/一定"等确定性措辞；动作只能写条件触发式（"若X则Y"）；不承诺收益。
4. 纳入A股约束：T+1、涨跌停、解禁减持、流动性。
5. 输出为简洁的中文markdown（企业微信群阅读场景），总长度控制在2500字以内。
6. 涨用<font color="warning">红</font>标注、跌用<font color="info">绿</font>标注（中国习惯），企微markdown只支持 info/comment/warning 三种字体颜色。
7. 文末固定附一行：⚠️ 本报告由AI生成，仅供研究参考，不构成投资建议。"""
