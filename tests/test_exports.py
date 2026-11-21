#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出器回归：SRT/VTT/MD/TXT 的格式、长 cue 警告、清理规则、确定性。不跑模型。"""
import json
import os
import re
import subprocess
import sys
import tempfile

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import clean, export  # noqa: E402

SRT_TS = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
VTT_TS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")
SEGS = json.load(open(os.path.join(HERE, "fixtures", "segments", "demo.json"), encoding="utf-8"))["segments"]
FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


tmp = mkdtemp()
outputs, warnings = export.export_all(SEGS, tmp)

srt = open(outputs["srt"], encoding="utf-8").read()
vtt = open(outputs["vtt"], encoding="utf-8").read()
plain = open(outputs["plain"], encoding="utf-8").read()
md = open(outputs["md"], encoding="utf-8").read()

check("四件套齐全", all(k in outputs for k in ("txt", "plain", "srt", "vtt", "md")), sorted(outputs))
check("srt cue 数", srt.count("-->") == len(SEGS), srt.count("-->"))
check("srt 序号从 1 连续", [l for l in srt.splitlines() if l.strip().isdigit()] == [str(i) for i in range(1, len(SEGS) + 1)])
check("srt 时间格式", all(SRT_TS.match(l) for l in srt.splitlines() if "-->" in l))
check("vtt 头", vtt.startswith("WEBVTT"))
check("vtt 时间格式", all(VTT_TS.match(l) for l in vtt.splitlines() if "-->" in l))
check("plain 是纯文本", plain == "\n".join(s["text"] for s in SEGS), plain[:40])
check("md 段首时间戳", "[00:00]" in md and "[00:15]" in md, md[:120])
check("长 cue 被标出", warnings["long_cues"] == [3], warnings)
check("无空段", warnings["empty_segments"] == 0, warnings)
check("clean 去口水词", clean.clean_text("嗯，最后一句。") == "最后一句。", clean.clean_text("嗯，最后一句。"))
check("clean 去句首残留标点", clean.clean_text("呃、那个，开始吧") == "开始吧。", clean.clean_text("呃、那个，开始吧"))
check("clean 中英加空格", clean.clean_text("用cloud写代码") == "用 cloud 写代码。", clean.clean_text("用cloud写代码"))
check("clean 不动专名内部", "Claude Code" in clean.clean_text("用Claude Code写代码"), clean.clean_text("用Claude Code写代码"))

# 空文本 / 负起点 / end<=start：导出器必须过滤或修正并记录
dirty = [
    {"start": 0.0, "end": 2.0, "text": "   "},
    {"start": -3.5, "end": -1.0, "text": "负时间"},
    {"start": 4.0, "end": 4.0, "text": "零时长"},
    {"start": 5.0, "end": 6.0, "text": "正常"},
]
tmp3 = mkdtemp()
out3, warn3 = export.export_all(dirty, tmp3)
srt3 = open(out3["srt"], encoding="utf-8").read()
check("空段被丢弃", srt3.count("-->") == 3, srt3.count("-->"))
check("无效 cue 有记录",
      [x["reason"] for x in warn3["invalid_cues"]] == ["empty_text", "negative_start", "end_le_start", "end_le_start"],
      warn3["invalid_cues"])
check("负起点夹到 0", "00:00:00,000 --> 00:00:00,050" in srt3, srt3)
check("空段计数", warn3["empty_segments"] == 1, warn3["empty_segments"])
# 源段号与导出 cue 号必须分开：空段被删后两者会错位
check("invalid 记录 cue 字段", all("cue" in x for x in warn3["invalid_cues"]), warn3["invalid_cues"])
check("空段 cue 为 None", warn3["invalid_cues"][0]["cue"] is None, warn3["invalid_cues"][0])
check("负起点是第 1 条 cue", warn3["invalid_cues"][1]["index"] == 2 and warn3["invalid_cues"][1]["cue"] == 1, warn3["invalid_cues"][1])
check("同一段两条记录指向同一条 cue", warn3["invalid_cues"][2]["index"] == 2 and warn3["invalid_cues"][2]["cue"] == 1, warn3["invalid_cues"][2])
check("零时长是第 2 条 cue", warn3["invalid_cues"][3]["index"] == 3 and warn3["invalid_cues"][3]["cue"] == 2, warn3["invalid_cues"][3])
check("正常段无记录", len(warn3["invalid_cues"]) == 4, warn3["invalid_cues"])

# 出处：给了 source 就写 export.source.json，sha256 与文件一致
src = os.path.join(HERE, "fixtures", "segments", "demo.json")
tmp4 = mkdtemp()
out4, _ = export.export_all(SEGS, tmp4, source=src)
meta_path = os.path.join(tmp4, "export.source.json")
check("写 export.source.json", os.path.exists(meta_path))
meta = json.load(open(meta_path, encoding="utf-8"))
check("source 记路径", meta["segments"] == src, meta)
check("source sha256 对得上", meta["sha256"] == export.sha256_file(src), meta)
check("source 记 cue 数", meta["count"] == len(SEGS), meta)
check("source 列产物", "transcript.srt" in meta["artifacts"] and "export.source.json" not in meta["artifacts"], meta)
check("不给 source 就不写", not os.path.exists(os.path.join(tmp3, "export.source.json")))
check("确定性: export.source.json",
      open(meta_path, "rb").read() ==
      open(export.export_all(SEGS, mkdtemp(), source=src)[0]["source"], "rb").read())

tmp2 = mkdtemp()
outputs2, _ = export.export_all(SEGS, tmp2)
for key in ("srt", "vtt", "md", "plain"):
    check("确定性: " + key,
          open(outputs[key], "rb").read() == open(outputs2[key], "rb").read())

proc = subprocess.run([sys.executable, "-m", "vsum", "export",
                       "--segments", os.path.join(HERE, "fixtures", "segments", "demo.json"),
                       "-o", os.path.join(tmp, "cli")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
check("export 子命令退出 0", proc.returncode == 0, proc.stderr)
check("export 子命令产物", os.path.exists(os.path.join(tmp, "cli", "transcript.srt")))
check("export 子命令写出处", os.path.exists(os.path.join(tmp, "cli", "export.source.json")))

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK exports")