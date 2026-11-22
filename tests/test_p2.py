#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2：复用不重跑、result.json 契约、--json 只一行、mode=none 删过期摘要。"""
import json
import os
import shutil
import subprocess
import sys

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import manifest as manifest_mod, pipeline  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


SEGS = [
    {"start": 0.0, "end": 3.0, "text": "今天我们聊聊 cloud code 这个工具", "avg_logprob": -0.2},
    {"start": 3.0, "end": 6.0, "text": "它叫 Grok，还有 DeepSeek 和 Sonnet", "avg_logprob": -0.9},
    {"start": 6.0, "end": 15.0, "text": "这一段故意写得长一些，用来验证超过七秒的 cue 会被记进 manifest", "avg_logprob": -0.5},
    {"start": 15.0, "end": 18.0, "text": "2026 年有 3 个数字，嗯。", "avg_logprob": -0.1},
]

work = mkdtemp()
out = os.path.join(work, "out")
os.makedirs(out)
inp = os.path.join(work, "demo.mp4")
with open(inp, "wb") as fh:
    fh.write(b"fake-video-bytes")
with open(os.path.join(out, "audio.wav"), "wb") as fh:
    fh.write(b"RIFFfake")
with open(os.path.join(out, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": SEGS}, fh, ensure_ascii=False)

man = manifest_mod.build(inp, os.path.join(out, "audio.wav"),
                         {"model": "tiny", "language": "zh", "condition_on_previous_text": True}, SEGS, {},
                         {}, duration=18.0)
manifest_mod.write(man, os.path.join(out, "manifest.json"))

res = pipeline.run(inp, out, model="tiny", summary_mode="extractive")
check("复用成功", res["reused"] is True, res)
check("result.json 落盘", os.path.exists(os.path.join(out, "result.json")))
check("四件套齐全", all(os.path.exists(os.path.join(out, n)) for n in
                         ("transcript.md", "summary.md", "transcript.srt", "transcript.vtt")))
check("counts 对", res["counts"]["segments"] == 4 and res["counts"]["chars"] == sum(len(s["text"]) for s in SEGS), res["counts"])
check("low_conf 计数", res["counts"]["low_conf"] == 1, res["counts"])
check("summary 计数", res["summary"]["mode"] == "extractive" and res["summary"]["bullets"] >= 1, res["summary"])
check("sha256 对得上", all(
    res["sha256"][n] == manifest_mod.sha256_file(os.path.join(out, n))
    for n in res["sha256"]), res["sha256"])
check("artifacts 不写绝对路径", all("/" not in v for v in res["artifacts"].values() if v), res["artifacts"])

md = open(os.path.join(out, "transcript.md"), encoding="utf-8").read()
check("YAML 里低置信为 1", "low_conf_segments: 1" in md, md.splitlines()[:10])
check("低置信段带 ⚠", md.count("⚠") == 1, md)
check("每段一行", len([l for l in md.splitlines() if l.startswith("[")]) == 4, md)
sum_md = open(os.path.join(out, "summary.md"), encoding="utf-8").read()
head = sum_md.split("## 关键词")[0]
check("摘要每条带时间", all(l.endswith("]") for l in head.splitlines()
                             if l.startswith("- ") and not l.startswith("- （"))
      and any(l.startswith("- ") for l in head.splitlines()), sum_md)

# 第二次跑：复用且字节一致
before = {n: open(os.path.join(out, n), "rb").read()
          for n in ("transcript.md", "summary.md", "transcript.srt", "transcript.vtt")}
pipeline.run(inp, out, model="tiny", summary_mode="extractive")
after = {n: open(os.path.join(out, n), "rb").read() for n in before}
check("两次导出字节一致", before == after, [n for n in before if before[n] != after[n]])

# model 不同不得复用
ok, _, why = pipeline.segments_reusable(out, inp, "small")
check("模型不同不复用", ok is False and "small" in why, why)
ok, _, _ = pipeline.segments_reusable(out, inp, "tiny", force=True)
check("--force 不复用", ok is False)

# 语言/上下文变了也必须重跑：以前只比模型名，--lang en 会静默复用中文转写
ok, _, why = pipeline.segments_reusable(out, inp, "tiny", language="en")
check("语言不同不复用", ok is False and "语言" in why, why)
ok, _, why = pipeline.segments_reusable(out, inp, "tiny", context=False)
check("context 不同不复用", ok is False and "context" in why, why)
ok, _, _ = pipeline.segments_reusable(out, inp, "tiny", language="zh", context=True)
check("参数一致才复用", ok is True)

# mode=none 删过期摘要
with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
    fh.write("stale")
res = pipeline.run(inp, out, model="tiny", summary_mode="none")
check("mode=none 删过期摘要", not os.path.exists(os.path.join(out, "summary.md")))
check("mode=none 在 result 里", res["summary"]["mode"] == "none" and res["artifacts"]["summary_md"] is None, res["summary"])

# corrections.json 是 vsum fix 写的 files[].corrections 形状：必须拍平，进计数与附录
with open(os.path.join(out, "corrections.json"), "w", encoding="utf-8") as fh:
    json.dump({"total": 1, "high": 1, "medium": 0, "applied": 1, "files": [
        {"transcript": "x/transcript.txt", "corrections": [
            {"line": 1, "sec": 0.0, "surface": "cloud code", "replacement": "Claude Code",
             "confidence": "high", "applied": True}]}]}, fh, ensure_ascii=False)
res = pipeline.run(inp, out, model="tiny")
check("corrections 拍平并计数", res["counts"]["corrections_applied"] == 1, res["counts"])
md = open(os.path.join(out, "transcript.md"), encoding="utf-8").read()
check("术语附录列出 applied", "| 00:00 | cloud code | Claude Code | high |" in md, md[-400:])

# --json：stdout 只一行，日志走 stderr
proc = subprocess.run([sys.executable, "-m", "vsum", inp, "-o", out, "--model", "tiny",
                       "--json"], cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
lines = [l for l in proc.stdout.splitlines() if l.strip()]
check("--json 退出 0", proc.returncode == 0, proc.stderr[-300:])
check("--json stdout 一行", len(lines) == 1, proc.stdout[:300])
try:
    parsed = json.loads(lines[0])
except ValueError as exc:
    parsed = None
    check("--json 可解析", False, str(exc))
check("--json 是 result", isinstance(parsed, dict) and parsed.get("schema") == 1, parsed)
check("--json 日志在 stderr", "复用已有转写" in proc.stderr, proc.stderr[-300:])

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK p2")
