# -*- coding: utf-8 -*-
"""LLM 摘要的端到端：起一个本地 OpenAI 兼容服务，验证分块、解析、回查、两级渲染。

这里不打真实模型——本机的 Ollama 拉不起 llama-server，云模型要 key 和网络。
但协议是死的：vsum 只发一次 /chat/completions，读 choices[0].message.content。
所以用 http.server 冒充模型，能把「请求长什么样、回复怎么解析、改写被删不删」全验掉。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0]
sys.path.insert(0, HERE + "/..")

from vsum import summary, termrules  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


SEGS = [{"start": 0.0, "end": 3.0, "text": "今天我们聊聊 Claude Code 这个工具", "avg_logprob": -0.2},
        {"start": 3.0, "end": 6.0, "text": "它每个月 20 美元，比 Copilot 便宜", "avg_logprob": -0.3},
        {"start": 200.0, "end": 203.0, "text": "最后说说价格，Pro 版 20 美元", "avg_logprob": -0.2}]
META = {"title": "demo", "duration_sec": 210.0, "low_conf": {2}}

STATE = {"calls": 0, "last_prompt": "", "reply": None}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        STATE["calls"] += 1
        STATE["last_prompt"] = body["messages"][0]["content"]
        payload = {"choices": [{"message": {"content": STATE["reply"]}}]}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/v1" % port
RULES = termrules.load()

try:
    # 1) 正常回复：一句话综述 + 两条要点（一条原文、一条改写）
    STATE["reply"] = json.dumps({
        "overview": "作者认为 Claude Code 单价便宜，但要看实际用量。",
        "bullets": [
            {"text": "它每个月 20 美元，比 Copilot 便宜", "sec": 3.0},
            {"text": "作者觉得它性价比一般", "sec": 3.0},       # 改写，必须被回查门删掉
        ]}, ensure_ascii=False)
    md, mode, bullets, dropped, warn = summary.build(
        SEGS, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("mode 是 llm", mode == "llm", mode)
    check("调了一次接口", STATE["calls"] == 1, STATE["calls"])
    check("综述保留", "作者认为 Claude Code 单价便宜" in md, md[:300])
    check("综述标了未校验", "未经逐字校验" in md, md[:400])
    check("原文要点保留", "它每个月 20 美元，比 Copilot 便宜 [00:03]" in md, md)
    check("改写要点被删", "作者觉得它性价比一般" not in md, md)
    check("记了删除数", dropped == 1 and bullets == 1, (dropped, bullets))
    check("prompt 里带时间戳", "[00:00]" in STATE["last_prompt"] and "[00:03]" in STATE["last_prompt"])
    check("prompt 要求 JSON 对象", "overview" in STATE["last_prompt"] and "bullets" in STATE["last_prompt"])
    check("有两级小节", "## 一句话" in md and "## 要点" in md, md)

    # 2) 只有综述、没有要点：也必须出版（以前空 bullets 会整体退回抽取式）
    STATE["reply"] = json.dumps({"overview": "一句话就够了。"}, ensure_ascii=False)
    md2, mode2, b2, d2, _ = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("只有综述也算成功", mode2 == "llm" and "一句话就够了" in md2, (mode2, md2[:200]))
    check("只有综述时要点 0 条", b2 == 0, b2)

    # 3) 返回不合法：退回抽取式，并留 warning
    STATE["reply"] = "这不是 JSON"
    md3, mode3, _, _, warn3 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("坏回复退回抽取式", mode3 == "extractive", mode3)
    check("坏回复有 warning", any("退回抽取式" in w for w in warn3), warn3)

    # 4) 服务不可达：同样退回，不抛
    md4, mode4, _, _, warn4 = summary.build(SEGS, META, "llm", llm_url="http://127.0.0.1:1/v1",
                                            llm_model="fake", rules=RULES)
    check("连不上也退回", mode4 == "extractive", mode4)
    check("连不上有 warning", any("退回抽取式" in w for w in warn4), warn4)

    # 5) 长输入要分块（每块 <= CHUNK_CHARS 左右），不是一次全塞
    many = [{"start": float(i * 3), "end": float(i * 3 + 3),
             "text": "第%d段，随便说点有内容的话吧。" % i, "avg_logprob": -0.2} for i in range(600)]
    STATE["calls"] = 0
    STATE["reply"] = json.dumps({"overview": "长片综述", "bullets": []}, ensure_ascii=False)
    summary.build(many, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("长输入分多次请求", STATE["calls"] > 1, STATE["calls"])
finally:
    server.shutdown()

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK llm")
