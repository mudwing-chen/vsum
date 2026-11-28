# -*- coding: utf-8 -*-
"""vsum holdout：在固定语料上算一个能被引用的数。

语料目录（每项一个子目录，按名字排序）：
  corpus/<item>/manifest.json   （可选；有就读 terms.path）
  corpus/<item>/segments.json
  corpus/<item>/ref.srt|ref.txt（或与 manifest.input.path 同名的文本；假设稿本身不当参考）

口径：
- 单项 CER = 编辑距离 / 参考归一化字数；micro = 总距离 / 总参考字数；macro = 各项平均。
  两个都报：micro 被长条目主导，macro 被短条目主导，只报一个都会骗人。
- 回归比较用配对 bootstrap（按 item 重采样 1000 次，seed=0），报 delta micro 的 95% 区间。
  n=20 时点估计噪声很大，固定 1pp 阈值没有统计依据，所以判据看区间，不看单点。
- 术语只报召回 + Wilson；精确率需要逐处标注，这里没有。
"""
import argparse
import json
import os
import random
import sys

from . import evaluate
from . import manifest as manifest_mod

BOOTSTRAP = 1000
SEED = 0
REGRESSION_PP = 0.005  # 0.5pp：区间下界超过它才算明确变差


def _items(root):
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "segments.json")):
            out.append((name, d))
    return out


def _ref_for(d, man):
    """参考文本：与 manifest.input.path 同名 > ref.* > 唯一候选；假设本身绝不当参考。

    复用 evaluate._pick_ref 的挑选与拒绝规则：目录里只剩假设稿时它会抛
    FileNotFoundError（CLI 退出码 2），而不是给出一个恒为 0 的 CER。
    """
    hyp = os.path.join(d, "segments.json")
    return evaluate._pick_ref(d, (man.get("input") or {}).get("path", ""), [hyp])


def item_record(name, d):
    man_path = os.path.join(d, "manifest.json")
    man = manifest_mod.read(man_path) if os.path.exists(man_path) else {}
    with open(os.path.join(d, "segments.json"), encoding="utf-8") as fh:
        segs = json.load(fh).get("segments", [])
    hyp = "".join(s.get("text", "") for s in segs)
    ref_path = _ref_for(d, man)
    ref = evaluate._read_ref(ref_path)
    rn, hn = evaluate.normalize(ref), evaluate.normalize(hyp)
    if not rn:
        raise ValueError("%s: 参考文本归一化后为空" % name)
    dist = evaluate.levenshtein(rn, hn)
    rec = {"item": name, "ref_chars": len(rn), "hyp_chars": len(hn), "distance": dist,
           "cer": dist / float(len(rn)), "segments": len(segs),
           "ref": os.path.basename(ref_path)}
    terms = (man.get("terms") or {}).get("path")
    if terms and os.path.exists(terms):
        expected, found, missing = evaluate.term_report(ref, hyp, terms)
        rec["terms"] = {"expected": len(expected), "found": len(found), "missing": missing}
    return rec


def aggregate(items):
    ref = sum(i["ref_chars"] for i in items)
    dist = sum(i["distance"] for i in items)
    cers = [i["cer"] for i in items]
    agg = {"n": len(items), "ref_chars": ref, "distance": dist,
           "micro_cer": (dist / float(ref)) if ref else None,
           "macro_cer": (sum(cers) / float(len(cers))) if cers else None}
    exp = sum(i.get("terms", {}).get("expected", 0) for i in items)
    found = sum(i.get("terms", {}).get("found", 0) for i in items)
    if exp:
        lo, hi = evaluate.wilson(found, exp)
        agg["term"] = {"expected": exp, "found": found,
                       "recall": found / float(exp), "wilson": [lo, hi]}
    return agg


def paired_bootstrap(items, base_items, n=BOOTSTRAP, seed=SEED):
    pairs = [(i["distance"], i["ref_chars"], b["distance"], b["ref_chars"])
             for i, b in zip(items, base_items)]
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        dn = dr = bn = br = 0
        for _ in range(len(pairs)):
            d, r, bd, brr = pairs[rnd.randrange(len(pairs))]
            dn += d
            dr += r
            bn += bd
            br += brr
        if dr and br:
            vals.append(dn / float(dr) - bn / float(br))
    vals.sort()
    if not vals:
        return None
    return (vals[int(len(vals) * 0.025)], vals[len(vals) // 2], vals[int(len(vals) * 0.975)])


def compare(items, base):
    base_by_name = {i["item"]: i for i in base.get("items", [])}
    names = {i["item"] for i in items}
    missing = [i["item"] for i in items if i["item"] not in base_by_name]
    extra = [k for k in base_by_name if k not in names]
    if missing or extra:
        raise ValueError("语料不一致：新增 %s；基线多出 %s" % (missing or "无", extra or "无"))
    aligned = [base_by_name[i["item"]] for i in items]
    band = paired_bootstrap(items, aligned)
    delta = aggregate(items)["micro_cer"] - aggregate(aligned)["micro_cer"]
    verdict = "inconclusive"
    if band:
        lo, _, hi = band
        if lo > REGRESSION_PP:
            verdict = "regression"
        elif hi < -REGRESSION_PP:
            verdict = "improvement"
    worse = sum(1 for i, b in zip(items, aligned) if i["cer"] > b["cer"] + 1e-12)
    better = sum(1 for i, b in zip(items, aligned) if i["cer"] < b["cer"] - 1e-12)
    return {"delta_micro": delta, "delta_ci": band, "verdict": verdict,
            "worse": worse, "better": better}


def run(corpus, out=None, baseline=None, max_cer=None, quiet=False):
    if not os.path.isdir(corpus):
        raise FileNotFoundError("语料目录不存在：%s" % corpus)
    items = [item_record(n, d) for n, d in _items(corpus)]
    if not items:
        raise ValueError("语料目录里没有含 segments.json 的子目录")
    agg = aggregate(items)
    doc = {"schema": 1, "corpus": os.path.abspath(corpus), "items": items, "aggregate": agg}
    if baseline:
        with open(baseline, encoding="utf-8") as fh:
            base = json.load(fh)
        doc["baseline"] = {"path": os.path.abspath(baseline),
                           "compare": compare(items, base),
                           "aggregate": base.get("aggregate")}
    if not quiet:
        for i in items:
            print("%-24s %6d 字  CER %6.2f%%  %d 段"
                  % (i["item"], i["ref_chars"], i["cer"] * 100, i["segments"]))
        print("-" * 60)
        print("micro CER           : %.2f%%  (%d 字 / %d 距离, n=%d)"
              % (agg["micro_cer"] * 100, agg["ref_chars"], agg["distance"], agg["n"]))
        print("macro CER           : %.2f%%  (各项平均)" % (agg["macro_cer"] * 100))
        if "term" in agg:
            lo, hi = agg["term"]["wilson"]
            print("term recall         : %d/%d = %.1f%%  (95%% Wilson %.1f%% .. %.1f%%)"
                  % (agg["term"]["found"], agg["term"]["expected"],
                     agg["term"]["recall"] * 100, lo * 100, hi * 100))
        if baseline:
            c = doc["baseline"]["compare"]
            lo, mid, hi = c["delta_ci"]
            print("delta micro         : %+.2f pp  (95%% CI %+.2f .. %+.2f, 配对 bootstrap)"
                  % (c["delta_micro"] * 100, lo * 100, hi * 100))
            print("verdict             : %s  (变差 %d 项 / 变好 %d 项)"
                  % (c["verdict"], c["worse"], c["better"]))
    target = out or os.path.join(corpus, "holdout.json")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    if not quiet:
        print("json                : %s" % target)
    if max_cer is not None and agg["micro_cer"] is not None and agg["micro_cer"] > max_cer:
        print("micro CER %.2f%% > --max-cer %.2f%%" % (agg["micro_cer"] * 100, max_cer * 100),
              file=sys.stderr)
        return 5
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum holdout", description="固定语料上的 CER 与回归比较")
    ap.add_argument("--corpus", required=True, help="每项一个子目录：segments.json + ref.txt/.srt")
    ap.add_argument("-o", "--out", default=None, help="默认 corpus/holdout.json")
    ap.add_argument("--baseline", default=None, help="上一轮 holdout.json，做配对 bootstrap")
    ap.add_argument("--max-cer", type=float, default=None, help="micro CER 超过就退出 5")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)
    return run(args.corpus, args.out, args.baseline, args.max_cer, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
