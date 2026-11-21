#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闭环回归：改词 -> segments.fixed.json -> 字幕；以及「零命中时删掉过期 fixed」。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import evaluate, export, fix as fix_mod, termrules  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


def run(*argv):
    return subprocess.run([sys.executable, "-m", "vsum"] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


work = mkdtemp()
case = os.path.join(work, "case")
shutil.copytree(os.path.join(HERE, "fixtures", "loop"), case)

proc = run("fix", case, "--apply")
check("fix --apply 退出 0", proc.returncode == 0, proc.stderr)
check("写 transcript.fixed.txt", os.path.exists(os.path.join(case, "transcript.fixed.txt")))
check("写 segments.fixed.json", os.path.exists(os.path.join(case, "segments.fixed.json")))
check("有命中时写 corrections.json", os.path.exists(os.path.join(case, "corrections.json")))

fixed_txt = open(os.path.join(case, "transcript.fixed.txt"), encoding="utf-8").read()
check("fixed.txt 换成规范写法", "Claude Code" in fixed_txt and "cloud code" not in fixed_txt, fixed_txt[:60])

segs = json.load(open(os.path.join(case, "segments.fixed.json"), encoding="utf-8"))["segments"]
check("fixed 段只改 text", [s["start"] for s in segs] == [0.0, 3.0], [s["start"] for s in segs])
check("fixed 段文本已改", segs[0]["text"] == "今天我们聊聊 Claude Code 这个工具", segs[0]["text"])

out = os.path.join(work, "out")
proc = run("export", "--segments", os.path.join(case, "segments.fixed.json"), "-o", out)
srt = open(os.path.join(out, "transcript.srt"), encoding="utf-8").read()
check("字幕里是新词", "Claude Code" in srt and "cloud code" not in srt, srt[:80])
meta = json.load(open(os.path.join(out, "export.source.json"), encoding="utf-8"))
check("字幕带出处", meta["segments"] == os.path.join(case, "segments.fixed.json"), meta)
check("出处 sha256 对得上",
      meta["sha256"] == export.sha256_file(os.path.join(case, "segments.fixed.json")), meta)
check("出处记的是 cue 数", meta["count"] == 2, meta)

# eval --hyp：同一个 manifest，分别评 raw 与 fixed
man = {
    "schema": 1, "vsum_version": "0.1.0", "mode": "verbatim",
    "input": {"path": "demo.mp4", "sha256": "0" * 64, "duration": 6.0},
    "audio": {"path": "audio.wav", "sha256": "0" * 64, "sample_rate": 16000},
    "decode": {"model": "medium"}, "terms": {"pack": None, "path": os.path.join(ROOT, "vsum", "data", "terms.json"), "sha256": "x"},
    "counts": {"segments": 2, "chars": 30}, "warnings": {"long_cues": [], "empty_segments": 0, "invalid_cues": []},
    "outputs": {"segments": "segments.json"},
}
man_path = os.path.join(case, "manifest.json")
json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False)

ref_dir = os.path.join(work, "refs")
os.makedirs(ref_dir)
open(os.path.join(ref_dir, "demo.txt"), "w", encoding="utf-8").write(
    "今天我们聊聊 Claude Code 这个工具它叫 Grok\n")

raw_cer = evaluate.cer(open(os.path.join(ref_dir, "demo.txt"), encoding="utf-8").read(),
                       "".join(s["text"] for s in json.load(open(os.path.join(case, "segments.json"), encoding="utf-8"))["segments"]))
fixed_text, _ = evaluate.load_hyp(os.path.join(case, "segments.fixed.json"))
fixed_cer = evaluate.cer(open(os.path.join(ref_dir, "demo.txt"), encoding="utf-8").read(), fixed_text)
check("改前 CER > 0", raw_cer > 0, raw_cer)
check("改后 CER == 0", fixed_cer == 0.0, fixed_cer)
check("--hyp 改变结果", run("eval", "--manifest", man_path, "--ref", ref_dir,
                            "--hyp", os.path.join(case, "segments.fixed.json")).returncode == 0)

proc = run("eval", "--manifest", man_path, "--ref", ref_dir, "--hyp", os.path.join(case, "segments.fixed.json"))
check("eval 打印术语召回", "term recall" in proc.stdout, proc.stdout)
check("eval 明说 CER 区间跳过（无配对 cue）", "CER 95% band        : 跳过" in proc.stdout, proc.stdout)
check("eval 不再伪造 F1", "term precision/F1   : 不报" in proc.stdout, proc.stdout)

# 零命中时必须删掉上一轮 fixed（否则导出/评测会读到过期稿）
open(os.path.join(case, "transcript.txt"), "w", encoding="utf-8").write(
    "[   0.0 ->   3.0] 今天我们聊聊做菜。\n")
json.dump({"segments": [{"start": 0.0, "end": 3.0, "text": "今天我们聊聊做菜。"}]},
          open(os.path.join(case, "segments.json"), "w", encoding="utf-8"), ensure_ascii=False)
proc = run("fix", case, "--apply")
check("零命中退出 0", proc.returncode == 0, proc.stderr)
check("过期 corrections.json 被删", not os.path.exists(os.path.join(case, "corrections.json")))
check("过期 fixed.txt 被删", not os.path.exists(os.path.join(case, "transcript.fixed.txt")))
check("过期 segments.fixed.json 被删", not os.path.exists(os.path.join(case, "segments.fixed.json")))

# cue 配对重采样：段数相同、且每段各有对错时才给区间
cues = ["今天我们聊聊人工智能工具的第%d段内容" % i for i in range(6)]
segs = [{"text": c} for c in cues]
segs[0]["text"] = cues[0][:-2]
segs[3]["text"] = cues[3][:-1]
band = evaluate.bootstrap_cer_cues(cues, segs)
check("cue 配对时给出区间", band is not None, band)
if band:
    check("区间包含聚合值", band[0] <= band[3] <= band[2], band)
    check("聚合值 >= 全局 CER",
          band[3] >= evaluate.cer("".join(cues), "".join(x["text"] for x in segs)) - 1e-9, band)
check("段数不等时不给区间", evaluate.bootstrap_cer_cues(cues, segs[:4]) is None)
check("少于 5 段时不给区间", evaluate.bootstrap_cer_cues(cues[:4], segs[:4]) is None)
check("错误完全一致时区间退化为一点",
      evaluate.bootstrap_cer_cues(["abcdef"] * 6, [{"text": "abcdef"}] * 6)[3] == 0.0)

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK loop")
