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
check("md 段首时间戳", "[00:00]" in md and "[00:16]" in md, md[:120])
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

# 舍入与小时格式：md 与 srt 必须是同一套规则（md 走 mmss，srt 走毫秒）
from vsum.timefmt import mmss  # noqa: E402

check("mmss 四舍五入到秒", mmss(59.9996) == "01:00", mmss(59.9996))
check("mmss 满一小时给小时", mmss(3725) == "1:02:05", mmss(3725))
check("mmss 一小时整", mmss(3600) == "1:00:00", mmss(3600))
check("mmss 不足一小时只给 mm:ss", mmss(3599) == "59:59", mmss(3599))
check("mmss 负数按 0", mmss(-3) == "00:00", mmss(-3))

edge = [{"start": 59.9996, "end": 61.5, "text": "刚好一分钟"},
        {"start": 3725.0, "end": 3726.0, "text": "一小时零两分"}]
md_edge = export.to_md(edge)
check("md 59.9996 进位到 [01:00]", "[01:00]" in md_edge, md_edge)
check("md 超过一小时带小时位", "[1:02:05]" in md_edge, md_edge)
srt_edge = export.to_srt(edge)
check("srt 59.9996 进位", "00:01:00,000 --> 00:01:01,500" in srt_edge, srt_edge)
check("srt 超过一小时", "01:02:05,000 --> 01:02:06,000" in srt_edge, srt_edge)


# 低置信：并列同分同进退，不按索引切
def _segs(vals):
    return [{"start": float(i), "end": float(i) + 1.0, "text": "段%d" % i, "avg_logprob": v}
            for i, v in enumerate(vals, 1)]


tie_vals = ([-0.90] * 6 + [-0.70] * 14 + [-0.50] * 23 + [-0.40] * 20 +
            [-0.30] * 30 + [-0.25] * 20 + [-0.22] * 18 + [-0.21] * 12)
check("并列表是 8 个不同值 / 143 段", len(tie_vals) == 143 and len(set(tie_vals)) == 8)
low = export.low_conf_indexes(_segs(tie_vals))
check("并列整组带上（6 + 14）", len(low) == 20, sorted(low))
check("并列组不按索引切半", low == set(range(1, 21)), sorted(low))

cap_vals = [-0.90] * 8 + [-0.60] * 200 + [-0.10] * 192
low = export.low_conf_indexes(_segs(cap_vals))
check("并列超过 25% 时退回上一档", low == set(range(1, 9)), sorted(low))
check("全并列不整片标满", export.low_conf_indexes(_segs([-0.9] * 40)) == set())

short_ok = _segs([-0.05, -0.02, -0.08])
check("短片全是好值时不硬标第一段", export.low_conf_indexes(short_ok) == set(),
      export.low_conf_indexes(short_ok))
check("短片里真的差才标", export.low_conf_indexes(_segs([-0.02, -0.55, -0.03])) == {2})

# demo 只有第 3 段（avg_logprob -0.9）真的差；计数必须和 ⚠ 数一致
md_meta = export.to_md(SEGS, meta={"title": "demo"})
yaml_count = int(re.search(r"^low_conf_segments: (\d+)$", md_meta, re.M).group(1))
check("md low_conf 计数等于 ⚠ 数", yaml_count == md_meta.count("⚠"),
      (yaml_count, md_meta.count("⚠")))
check("demo 只标最差那段", yaml_count == 1 and "[00:06] ⚠" in md_meta, (yaml_count, md_meta[:600]))

# 负起点被夹到 0 之后，原本从 0 开始的下一句会压在它身上：播放器里会闪
overlap = [
    {"start": -1.0, "end": 1.5, "text": "开头被裁掉"},
    {"start": 0.0, "end": 0.5, "text": "压在它身上"},
]
fixed, overlap_warn = export.sanitize(overlap)
check("夹时间后不产生重叠 cue", fixed[1]["start"] >= fixed[0]["end"],
      [(c["start"], c["end"]) for c in fixed])
check("重叠修正被记录", "overlap_after_fix" in [x["reason"] for x in overlap_warn],
      overlap_warn)

# ⚠ 必须和 result.counts.low_conf 同源：空段被 sanitize 丢掉后不能把 ⚠ 挪到别的段上
mixed = [
    {"start": 0.0, "end": 2.0, "text": "第一句正常", "avg_logprob": -0.2},
    {"start": 2.0, "end": 3.0, "text": "", "avg_logprob": -1.4},
    {"start": 3.0, "end": 5.0, "text": "第三句也正常", "avg_logprob": -0.1},
]
raw_low = export.low_conf_indexes(mixed)
check("空段也在低置信集合里", raw_low == {2}, raw_low)
out_mixed = mkdtemp()
export.export_all(mixed, out_mixed, meta={"low_conf": raw_low})
md_mixed = open(os.path.join(out_mixed, "transcript.md"), encoding="utf-8").read()
check("丢掉空段后不留错位的 ⚠", "⚠" not in md_mixed, md_mixed)
check("low_conf_segments 与实际 ⚠ 一致", "low_conf_segments: 0" in md_mixed, md_mixed[:400])

# 清洗稿是「额外一版」：不该带术语附录（它不改词表）
clean_md_text = clean.clean_md(SEGS)
check("清洗稿不带术语修订附录", "## 术语修订" not in clean_md_text, clean_md_text[-120:])

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK exports")