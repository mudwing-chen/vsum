#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vsum holdout：micro/macro、配对 bootstrap、语料校验回归。"""
import json
import os
import sys

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import evaluate, holdout  # noqa: E402

CORPUS = os.path.join(HERE, "fixtures", "holdout")
FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


tmp = mkdtemp()
out = os.path.join(tmp, "holdout.json")
rc = holdout.run(CORPUS, out=out, quiet=True)
doc = load(out)
check("退出 0", rc == 0, rc)
check("三项", [i["item"] for i in doc["items"]] == ["alpha", "beta", "gamma"], doc["items"])

beta = [i for i in doc["items"] if i["item"] == "beta"][0]
ref = "我们在用Claude Code写代码"
hyp = "我们在用cloud code写代码"
dist = evaluate.levenshtein(evaluate.normalize(ref), evaluate.normalize(hyp))
check("beta 距离", beta["distance"] == dist, beta)
check("beta CER", abs(beta["cer"] - dist / float(len(evaluate.normalize(ref)))) < 1e-12, beta["cer"])

agg = doc["aggregate"]
total_ref = sum(i["ref_chars"] for i in doc["items"])
total_dist = sum(i["distance"] for i in doc["items"])
check("micro", abs(agg["micro_cer"] - total_dist / float(total_ref)) < 1e-12, agg)
check("macro", abs(agg["macro_cer"] - sum(i["cer"] for i in doc["items"]) / 3.0) < 1e-12, agg)

gamma = [i for i in doc["items"] if i["item"] == "gamma"][0]
check("srt 参考可读", gamma["cer"] == 0.0, gamma)
check("ref 文件名记录", gamma["ref"] == "ref.srt", gamma)

check("max-cer 触发 5", holdout.run(CORPUS, out=out, max_cer=0.01, quiet=True) == 5)
check("max-cer 未超继续 0", holdout.run(CORPUS, out=out, max_cer=0.99, quiet=True) == 0)

rc = holdout.run(CORPUS, out=out + ".2", baseline=out, quiet=True)
cmp = load(out + ".2")["baseline"]["compare"]
check("同语料 delta 0", abs(cmp["delta_micro"]) < 1e-12, cmp)
check("同语料区间 0", all(abs(x) < 1e-12 for x in cmp["delta_ci"]), cmp)
check("同语料 verdict", cmp["verdict"] == "inconclusive", cmp)

a = holdout.run(CORPUS, out=out + ".a", quiet=True)
b = holdout.run(CORPUS, out=out + ".b", quiet=True)
check("两次输出一致", open(out + ".a", "rb").read() == open(out + ".b", "rb").read())

solo = os.path.join(tmp, "solo", "only")
os.makedirs(solo)
write_json(os.path.join(solo, "manifest.json"), {"input": {"path": "only.wav"}})
write_json(os.path.join(solo, "segments.json"), {"segments": [{"text": "无关"}]})
with open(os.path.join(solo, "ref.txt"), "w", encoding="utf-8") as fh:
    fh.write("无关")
try:
    holdout.run(os.path.join(tmp, "solo"), out=out + ".x", baseline=out, quiet=True)
    check("语料不一致报错", False)
except ValueError:
    check("语料不一致报错", True)

try:
    holdout.run(os.path.join(tmp, "nope"), out=out + ".x", quiet=True)
    check("目录不存在报错", False)
except FileNotFoundError:
    check("目录不存在报错", True)

empty = os.path.join(tmp, "empty")
os.makedirs(empty)
try:
    holdout.run(empty, out=out + ".x", quiet=True)
    check("空语料报错", False)
except ValueError:
    check("空语料报错", True)

tc = os.path.join(tmp, "terms")
os.makedirs(os.path.join(tc, "one"))
terms_path = os.path.join(tc, "terms.json")
write_json(terms_path, {"terms": [{"canonical": "Claude"}]})
write_json(os.path.join(tc, "one", "manifest.json"),
           {"input": {"path": "one.wav"}, "terms": {"path": terms_path}})
write_json(os.path.join(tc, "one", "segments.json"), {"segments": [{"text": "我们用cloud写代码"}]})
with open(os.path.join(tc, "one", "ref.txt"), "w", encoding="utf-8") as fh:
    fh.write("我们用Claude写代码")
trc = holdout.run(tc, out=out + ".t", quiet=True)
tagg = load(out + ".t")["aggregate"]["term"]
check("术语 expected", tagg["expected"] == 1, tagg)
check("术语 found 0", tagg["found"] == 0, tagg)
check("术语召回 0", tagg["recall"] == 0.0, tagg)
check("术语 Wilson 下界 0", tagg["wilson"][0] == 0.0, tagg)

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK holdout")
