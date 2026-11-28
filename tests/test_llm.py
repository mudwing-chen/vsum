# -*- coding: utf-8 -*-
"""LLM 摘要的端到端：起一个本地 OpenAI 兼容服务，验证分块、解析、回查、两级渲染。

这里不打真实模型——本机的 Ollama 拉不起 llama-server，云模型要 key 和网络。
但协议是死的：vsum 只发一次 /chat/completions，读 choices[0].message.content。
所以用 http.server 冒充模型，能把「请求长什么样、回复怎么解析、改写被删不删」全验掉。
"""
import json
import sys
import threading
import urllib.error
import urllib.request
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

STATE = {"calls": 0, "last_prompt": "", "last_auth": "unset", "reply": None,
         "status": 200, "error_body": "", "ctype": None, "redirect": None,
         "raw": None, "b_hits": []}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        STATE["calls"] += 1
        STATE["last_prompt"] = body["messages"][0]["content"]
        STATE["last_auth"] = self.headers.get("Authorization")
        if STATE["redirect"]:
            # 反代/网关式跳转：Location 指向另一个端口（跨源）
            self.send_response(302)
            self.send_header("Location", STATE["redirect"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status = STATE.get("status", 200)
        if STATE["raw"] is not None:
            raw = STATE["raw"]
        elif status != 200:
            raw = (STATE.get("error_body") or "{}").encode("utf-8")
        else:
            payload = {"choices": [{"message": {"content": STATE["reply"]}}]}
            raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", STATE["ctype"] or "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except Exception:
            pass  # 客户端按上限提前断开连接是预期行为，不是测试失败

    def log_message(self, *args):
        pass


class TargetHandler(BaseHTTPRequestHandler):
    """重定向的去处：被访问过就记一笔（带没带 Authorization 尤其要记）。"""

    def _hit(self):
        STATE["b_hits"].append({"path": self.path, "method": self.command,
                                "auth": self.headers.get("Authorization")})
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(
            {"overview": "对方域名不该看到这条", "bullets": []}, ensure_ascii=False)}}]},
            ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = _hit

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/v1" % port

target = HTTPServer(("127.0.0.1", 0), TargetHandler)
target_port = target.server_address[1]
threading.Thread(target=target.serve_forever, daemon=True).start()

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
        SEGS, META, "llm", llm_url=url, llm_model="fake", rules=RULES, llm_key="sk-test-123")
    check("mode 是 llm", mode == "llm", mode)
    check("带 key 时发 Bearer", STATE["last_auth"] == "Bearer sk-test-123", STATE["last_auth"])
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
    check("不给 key 就不发 Authorization", STATE["last_auth"] is None, STATE["last_auth"])

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

    # 6) 服务端 500 且带原因（Ollama 装坏时就是这样）：warning 必须把它带出来，
    #    否则用户只看到「已退回抽取式」，完全不知道该修什么。
    STATE["status"] = 500
    STATE["error_body"] = json.dumps(
        {"error": {"message": "error starting llama-server: llama-server binary not found"}})
    md6, mode6, _, _, warn6 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                            rules=RULES)
    check("HTTP 500 退回抽取式", mode6 == "extractive", mode6)
    check("warning 带上服务端原因",
          any("llama-server binary not found" in w for w in warn6), warn6)
    check("HTTP 500 标出状态码", any("500" in w for w in warn6), warn6)
    STATE["status"] = 200

    # 5) 长输入要分块（每块 <= CHUNK_CHARS 左右），不是一次全塞
    many = [{"start": float(i * 3), "end": float(i * 3 + 3),
             "text": "第%d段，随便说点有内容的话吧。" % i, "avg_logprob": -0.2} for i in range(600)]
    STATE["calls"] = 0
    STATE["reply"] = json.dumps({"overview": "长片综述", "bullets": []}, ensure_ascii=False)
    summary.build(many, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("长输入分多次请求", STATE["calls"] > 1, STATE["calls"])

    # 7) H-1：跨源重定向必须拒，Authorization 不能跟着 Location 走到别的端口/域名
    STATE["b_hits"] = []
    STATE["redirect"] = "http://127.0.0.1:%d/v1/chat/completions" % target_port
    STATE["reply"] = json.dumps({"overview": "不该走到这里", "bullets": []}, ensure_ascii=False)
    md7, mode7, _, _, warn7 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                            rules=RULES, llm_key="sk-real-secret")
    check("跨源重定向后对方一条都没收到", STATE["b_hits"] == [], STATE["b_hits"])
    check("跨源重定向退回抽取式", mode7 == "extractive", mode7)
    check("跨源重定向有 warning", any("拒绝跨主机重定向" in w for w in warn7), warn7)
    STATE["redirect"] = None

    # 同源重定向照常放行（只挡跨源）
    hop = summary.SameHostRedirect()
    kept = hop.redirect_request(urllib.request.Request(url + "/chat/completions"), None,
                                302, "Found", {}, url + "/v1-eu/chat/completions")
    check("同源重定向放行", kept is not None and kept.full_url.endswith("/v1-eu/chat/completions"),
          kept)
    try:
        hop.redirect_request(urllib.request.Request(url + "/chat/completions"), None, 302,
                             "Found", {}, "https://evil.example.com/v1/chat/completions")
        check("跨源重定向抛 HTTPError", False)
    except urllib.error.HTTPError as exc:
        check("跨源重定向抛 HTTPError", "拒绝跨主机重定向" in str(exc), exc)

    # 8) M-1：sec 是 inf（JSON 里的 1e999）不能崩，那条要点丢掉，其余照常出版
    STATE["reply"] = ('{"overview": "价格那段有个数字。", "bullets": ['
                      '{"text": "它每个月 20 美元，比 Copilot 便宜", "sec": 3.0},'
                      '{"text": "最后说说价格，Pro 版 20 美元", "sec": 1e999}]}')
    md8, mode8, b8, _, _ = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                         rules=RULES)
    check("inf 的 sec 不崩（仍是 llm）", mode8 == "llm", mode8)
    check("inf 那条被丢", "最后说说价格，Pro 版 20 美元" not in md8, md8[:300])
    check("正常那条还在", b8 == 1 and "它每个月 20 美元" in md8, b8)
    check("find_segment 对 inf 返回 None", summary.find_segment(float("inf"), SEGS) is None)
    check("find_segment 对坏值返回 None",
          summary.find_segment("abc", SEGS) is None and summary.find_segment(None, SEGS) is None)
    check("_parse_reply 丢掉非有限 sec",
          summary._parse_reply('{"bullets": [{"text": "x", "sec": 1e999}]}') is None)
    check("_parse_reply 丢掉 NaN",
          summary._parse_reply('{"bullets": [{"text": "x", "sec": NaN}]}') is None)

    # 9) M-5：超大响应体只读到上限就降级，不把 9MB 全读进内存再解析
    STATE["raw"] = ('{"choices": [{"message": {"content": "' + "A" * (9 * 1024 * 1024)
                   + '"}}]}').encode("utf-8")
    md9, mode9, _, _, warn9 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                            rules=RULES)
    check("超大响应退回抽取式", mode9 == "extractive", mode9)
    check("超大响应有 warning", any("超过" in w for w in warn9), warn9)
    STATE["raw"] = None

    # Content-Type 不是 JSON：仍然尝试解析，但记一条 warning
    STATE["ctype"] = "text/html; charset=utf-8"
    STATE["reply"] = json.dumps({"overview": "类型不对也照样解析。", "bullets": []},
                                ensure_ascii=False)
    md10, mode10, _, _, warn10 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                               rules=RULES)
    check("Content-Type 不对仍解析", mode10 == "llm" and "类型不对也照样解析" in md10,
          (mode10, md10[:200]))
    check("Content-Type 不对有 warning", any("Content-Type" in w for w in warn10), warn10)
    STATE["ctype"] = None

    # 10) H-1 入口：明文 http 只许本机，其余在发请求前就报错（cli 映射成退出码 2）
    try:
        summary.build(SEGS, META, "llm", llm_url="http://192.0.2.10:8080/v1",
                      llm_model="fake", rules=RULES)
        check("非回环 http 抛 ValueError", False)
    except ValueError as exc:
        check("非回环 http 抛 ValueError", "https" in str(exc), exc)
    check("https 放行", summary.check_url("https://api.deepseek.com/v1")
          == "https://api.deepseek.com/v1")
    check("本机 http 放行", summary.check_url(url) == url)
    check("本机 localhost 放行",
          summary.check_url("http://localhost:11434/v1") == "http://localhost:11434/v1")

    # 11) M-2：综述有长度上限，超了整段丢掉（要点不受影响）
    STATE["reply"] = json.dumps({
        "overview": "结论" * 120,
        "bullets": [{"text": "它每个月 20 美元，比 Copilot 便宜", "sec": 3.0}]},
        ensure_ascii=False)
    md11, _, b11, _, warn11 = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake",
                                            rules=RULES)
    check("超长综述整段删除", "## 一句话" not in md11 and "结论结论" not in md11, md11[:300])
    check("超长综述有 warning", any("上限" in w for w in warn11), warn11)
    check("综述删了要点还在", b11 == 1 and "它每个月 20 美元" in md11, b11)
    STATE["reply"] = json.dumps({"overview": "短" * summary.OVERVIEW_MAX, "bullets": []},
                                ensure_ascii=False)
    md12, _, _, _, _ = summary.build(SEGS, META, "llm", llm_url=url, llm_model="fake", rules=RULES)
    check("刚好到上限的综述保留", "## 一句话" in md12, md12[:200])
finally:
    server.shutdown()
    target.shutdown()

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK llm")
