"""企业微信群机器人推送模块（markdown，超长自动分段）"""
import json
import urllib.request

MAX_BYTES = 4000  # 企微单条上限4096字节，留余量


def _send(webhook, content):
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("errcode") != 0:
        raise RuntimeError(f"企微推送失败: {resp}")
    return resp


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


def push_markdown(webhook, title, md_text):
    """推送markdown到企微群，超长自动分段，返回分段数"""
    full = f"## {title}\n\n{md_text}" if title else md_text
    chunks = _split_utf8(full)
    for i, chunk in enumerate(chunks):
        content = chunk if i == 0 else f"（续 {i+1}/{len(chunks)}）\n" + chunk
        _send(webhook, content)
    return len(chunks)


def push_text(webhook, text):
    """纯文本推送（用于告警/测试）"""
    body = json.dumps({"msgtype": "text", "text": {"content": text}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("errcode") != 0:
        raise RuntimeError(f"企微推送失败: {resp}")
    return resp
