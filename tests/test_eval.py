#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval 口径回归：wilson 区间、CER 归一化、术语召回。零依赖，不跑模型。"""
import json
import os
import sys

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import evaluate  # noqa: E402

FAIL = []


N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    if not cond:
        FAIL.append("%s %s" % (name, detail))


lo, hi = evaluate.wilson(1, 15)
check("Wilson 1/15 下界 > 0", lo > 0, lo)
check("Wilson 1/15 上界 < 1", hi < 1, hi)
check("Wilson 0/15 下界为 0", evaluate.wilson(0, 15)[0] == 0.0)
check("Wilson 15/15 上界为 1", evaluate.wilson(15, 15)[1] == 1.0)
check("样本翻倍区间变窄",
      (evaluate.wilson(10, 150)[1] - evaluate.wilson(10, 150)[0])
      < (evaluate.wilson(1, 15)[1] - evaluate.wilson(1, 15)[0]))
check("n=0 返回 None", evaluate.wilson(0, 0) is None)

check("只留字母数字，转小写", evaluate.normalize("Claude Code，第二版！") == "claudecode第二版",
      evaluate.normalize("Claude Code，第二版！"))
check("空参考 CER 为 None", evaluate.cer("", "") is None)
check("完全不同 CER 为 1", evaluate.cer("abc", "xyz") == 1.0)
check("一字之差 CER = 1/len", abs(evaluate.cer("abcd", "abce") - 0.25) < 1e-9,
      evaluate.cer("abcd", "abce"))

# cue 配对：以前要求 cue 数 == 段数才能重采样，真实字幕几乎不满足，等于永远跳过
SEGS = [{"start": float(i), "end": float(i + 1), "text": "这是第%d句测试文本" % i} for i in range(30)]
CUES = [(float(k * 3), float(k * 3 + 2.9),
         "".join("这是第%d句测试文本" % i for i in range(k * 3, k * 3 + 3))) for k in range(10)]
band = evaluate.bootstrap_cer_cues(CUES, SEGS)
check("cue 数与段数不等也能出区间", band is not None, band)
check("完全对得上时聚合 CER 为 0", band is not None and band[3] == 0.0, band)
check("区间下界 <= 中位 <= 上界", band is not None and band[0] <= band[1] <= band[2], band)
check("cue 太少不给区间", evaluate.bootstrap_cer_cues(CUES[:3], SEGS) is None)
misaligned = [(0.0, 2.0, "完全不同的另一句话")]
check("错配时 CER > 0", evaluate.bootstrap_cer_cues(misaligned * 6, SEGS)[3] > 0)

# cue 解析：数字正文是字幕原文，`cue-1` 是 VTT 标识行，两种以前都会把 CER 算歪
tmp = mkdtemp()


def write(name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


srt = write("num.srt", "\n".join([
    "1",
    "00:00:01,000 --> 00:00:02,000",
    "1984",
    "",
    "2",
    "00:00:02,000 --> 00:00:04,000",
    "一共 3500 元",
    ""]))
cues = evaluate._cues(srt)
check("数字字幕不被当序号丢", [t for _s, _e, t in cues] == ["1984", "一共 3500 元"], cues)
check("数字字幕进参考文本", "1984" in evaluate._read_ref(srt), evaluate._read_ref(srt))

vtt = write("ids.vtt", "\n".join([
    "WEBVTT",
    "",
    "cue-1",
    "00:00:01.000 --> 00:00:02.000",
    "第一句",
    "",
    "cue-2",
    "00:00:03.000 --> 00:00:04.000",
    "第二句",
    ""]))
cues2 = evaluate._cues(vtt)
check("cue-N 标识不粘进上一句", [t for _s, _e, t in cues2] == ["第一句", "第二句"], cues2)
check("参考文本也不带标识行", evaluate._read_ref(vtt) == "第一句第二句", evaluate._read_ref(vtt))

# 参考目录里只有假设稿：不能自评自得 0 分，必须报错（CLI 会映射成退出码 2）
selfdir = os.path.join(tmp, "selfref")
os.makedirs(selfdir)
hyp_file = os.path.join(selfdir, "demo.txt")
with open(hyp_file, "w", encoding="utf-8") as fh:
    fh.write("这是我们自己的转写")
try:
    evaluate._pick_ref(selfdir, "demo.mp4", [hyp_file])
    check("参考不能是假设本身", False)
except FileNotFoundError as exc:
    check("参考不能是假设本身", "只有假设文本本身" in str(exc), exc)
check("不给 hyp 时唯一候选可用",
      os.path.basename(evaluate._pick_ref(selfdir, "demo.mp4", [])) == "demo.txt")

# 多候选时优先与输入同名者；一个都对不上就报错，不许瞎挑
refs2 = os.path.join(tmp, "refs2")
os.makedirs(refs2)
for name in ("other.txt", "demo.txt"):
    with open(os.path.join(refs2, name), "w", encoding="utf-8") as fh:
        fh.write(name)
check("优先与输入同名", os.path.basename(evaluate._pick_ref(refs2, "demo.mp4", [])) == "demo.txt")
try:
    evaluate._pick_ref(refs2, "nothing.mp4", [])
    check("多候选无同名要报错", False)
except FileNotFoundError as exc:
    check("多候选无同名要报错", "没有一个跟输入" in str(exc), exc)

# run() 这条路上也得报错：CER 0 会让 --max-cer 静默通过，比直接失败更危险
with open(os.path.join(selfdir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"input": {"path": "demo.mp4"}}, fh)
with open(os.path.join(selfdir, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": [{"text": "这是我们自己的转写"}]}, fh)
try:
    evaluate.run(os.path.join(selfdir, "manifest.json"), selfdir, hyp_path=hyp_file, quiet=True)
    check("run 拒收自评自得", False)
except FileNotFoundError as exc:
    check("run 拒收自评自得", "只有假设文本本身" in str(exc), exc)

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK eval (%d checks)" % N[0])