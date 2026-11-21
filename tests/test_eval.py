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


def check(name, cond, detail=""):
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

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK eval (%d checks)" % 11)