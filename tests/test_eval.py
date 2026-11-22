#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval 口径回归：wilson 区间、CER 归一化、术语召回。零依赖，不跑模型。"""
import os
import sys

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

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK eval (%d checks)" % N[0])