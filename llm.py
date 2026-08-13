"""DeepSeek API 调用模块（OpenAI兼容协议）"""
import json
import os
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


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
3. 不预测涨停、不给出确定性买卖指令、不承诺收益；只给条件化的观察框架。
4. 纳入A股约束：T+1、涨跌停、解禁减持、流动性。
5. 输出为简洁的中文markdown（企业微信群阅读场景），总长度控制在2500字以内。
6. 涨用<font color="warning">红</font>标注、跌用<font color="info">绿</font>标注（中国习惯），企微markdown只支持 info/comment/warning 三种字体颜色。
7. 文末固定附一行：⚠️ 本报告由AI生成，仅供研究参考，不构成投资建议。"""
