"""消息推送模块：支持企业微信群机器人 / PushPlus(个人微信) 双通道
- 企微：webhook 以 https://qyapi.weixin.qq.com 开头，markdown 分段推送
- PushPlus：token 形式，直达个人微信，免费200条/天，html/markdown
"""
import json
import os
import urllib.parse
import urllib.request

MAX_BYTES = 4000  # 企微单条上限4096字节，留余量


# ---------- 工具 ----------

def _split_utf8(text, max_bytes=MAX_BYTES):
    """按UTF-8字节数切分，不在多字节字符中间切断"""
    chunks, buf = [], ""
    for ch in text:
        if len((buf + ch).encode("utf-8")) > max_bytes:
            chunks.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        chunks.append(buf)
    return chunks


def _http_json(url, body, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- 企微 ----------

def _wecom_send(webhook, content, msgtype="markdown"):
    resp = _http_json(webhook, {"msgtype": msgtype, msgtype: {"content": content}})
    if resp.get("errcode") != 0:
        raise RuntimeError(f"企微推送失败: {resp}")
    return resp


def _wecom_push(webhook, title, md_text):
    full = f"## {title}\n\n{md_text}" if title else md_text
    chunks = _split_utf8(full)
    for i, chunk in enumerate(chunks):
        content = chunk if i == 0 else f"（续 {i+1}/{len(chunks)}）\n" + chunk
        _wecom_send(webhook, content)
    return len(chunks)


# ---------- PushPlus ----------

def _pushplus_send(token, title, content, template="markdown"):
    url = "https://www.pushplus.plus/send"
    resp = _http_json(url, {"token": token, "title": title, "content": content,
                            "template": template})
    if resp.get("code") != 200:
        raise RuntimeError(f"PushPlus推送失败: {resp}")
    return resp


def _pushplus_push(token, title, md_text):
    """PushPlus单条上限约10万字符，一般不需要分段"""
    _pushplus_send(token, title, md_text)
    return 1


# ---------- 飞书 ----------

def _feishu_send(webhook, content, title=""):
    """飞书自定义机器人：interactive卡片+lark_md，原生渲染markdown"""
    body = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title or "A股推送"},
                       "template": "blue"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }
    resp = _http_json(webhook, body)
    if resp.get("code") != 0:
        raise RuntimeError(f"飞书推送失败: {resp}")
    return resp


def feishu_card(webhook, title, template, elements_md, note=""):
    """结构化飞书卡片：header颜色+多元素+底部注释
    template: green/yellow/red/blue（按天气变色）
    elements_md: markdown字符串列表，每个是一个元素
    """
    els = []
    for md in elements_md:
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": md}})
        els.append({"tag": "hr"})
    els = els[:-1] if els else []  # 去掉最后的hr
    if note:
        els.append({"tag": "hr"})
        els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    body = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title},
                       "template": template},
            "elements": els,
        },
    }
    resp = _http_json(webhook, body)
    if resp.get("code") != 0:
        raise RuntimeError(f"飞书推送失败: {resp}")
    return resp


def _feishu_push(webhook, title, md_text):
    """飞书单条≤20KB；markdown全量塞卡片，超限才分段"""
    MAX_FS = 18000  # 留余量给卡片结构
    full = md_text
    if len(full.encode("utf-8")) <= MAX_FS:
        _feishu_send(webhook, full, title)
        return 1
    chunks = _split_utf8(full, MAX_FS)
    for i, chunk in enumerate(chunks):
        header = title if i == 0 else f"{title}（{i+1}/{len(chunks)}）"
        _feishu_send(webhook, chunk, header)
    return len(chunks)


# ---------- 统一入口 ----------

def push_report(title, md_text):
    """自动识别通道推送报告，失败自动降级下一通道。
    优先级：FEISHU_WEBHOOK_URL > PUSHPLUS_TOKEN > WECOM_WEBHOOK_URL（飞书最稳）。
    返回 (通道, 分段数)。都失败则 ("none", 0)。
    """
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    feishu = os.environ.get("FEISHU_WEBHOOK_URL", "")
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "")
    attempts = []
    if feishu:
        attempts.append(("feishu", lambda: _feishu_push(feishu, title, md_text)))
    if token:
        attempts.append(("pushplus", lambda: _pushplus_push(token, title, md_text)))
    if webhook:
        attempts.append(("wecom", lambda: _wecom_push(webhook, title, md_text)))
    for name, fn in attempts:
        try:
            return name, fn()
        except Exception as e:
            print(f"[warn] {name}通道失败: {e}")
            continue
    return "none", 0


def push_visual(title, template, signal_md, lights_md, body_md, note=""):
    """结构化视觉推送（目前仅飞书，其他通道降级为纯文本拼接）。
    返回 (通道, 分段数)。"""
    feishu = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if feishu:
        MAX_FS = 16000
        if len(body_md.encode("utf-8")) <= MAX_FS:
            return "feishu", feishu_card(feishu, title, template,
                                       [signal_md, lights_md, body_md], note) and 1
        # 超长：卡片+后续文字段
        feishu_card(feishu, title, template, [signal_md, lights_md], note)
        chunks = _split_utf8(body_md, MAX_FS)
        for i, c in enumerate(chunks):
            _feishu_send(feishu, c, f"{title}（续{i+1}/{len(chunks)}）")
        return "feishu", 1 + len(chunks)
    # 其他通道降级
    combined = f"{signal_md}\n\n{lights_md}\n\n{body_md}"
    return push_report(title, combined)


def push_text(text):
    """纯文本推送（测试/告警），失败自动降级下一通道"""
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    feishu = os.environ.get("FEISHU_WEBHOOK_URL", "")
    webhook = os.environ.get("WECOM_WEBHOOK_URL", "")
    attempts = []
    if feishu:
        attempts.append(("feishu", lambda: _feishu_send(feishu, text, "A股推送")))
    if token:
        attempts.append(("pushplus", lambda: _pushplus_send(token, "测试", text, template="text")))
    if webhook:
        attempts.append(("wecom", lambda: _wecom_send(webhook, text, msgtype="text")))
    for name, fn in attempts:
        try:
            return name, fn()
        except Exception as e:
            print(f"[warn] {name}通道失败: {e}")
            continue
    return "none", 0
