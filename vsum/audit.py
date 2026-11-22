# -*- coding: utf-8 -*-
"""术语体检：扫一遍 transcript.txt，挑出可疑的专有名词。

输出的是「可疑清单」，不是最终结论——每条都带时间戳和上下文，方便拿视频抽帧核对。
"""
import argparse
import collections
import sys
import hashlib
import json
import os
import re

from . import termrules

LINE = re.compile(r"^\[\s*([0-9.]+)\s*->\s*([0-9.]+)\]\s*(.*)$")
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")

COMMON = set("""
the and for you are but not with this that have has had was were will would can could
should may might must into from they them their there here what when where which who why
how all any both each few more most other some such only own same than too very just
about after again against before being below between during through under until while
over once out off then once our your his her its who whom
app app apps code codes data file files name names type types user users team teams tool tools
model models agent agents skill skills video videos audio voice image images text word words
open close start stop first last next new old good bad best fast slow high low small big
make makes made take takes took get gets got give gives gave use uses used using work works
worked working run runs ran running build builds built test tests testing error errors issue
issues bug bugs fix fixes fixed line lines point points case cases part parts side sides
system systems service services server servers client clients version versions update updates
install installs installed setup set sets setting settings config configs default option options
value values result results step steps way ways thing things time times day days year years
month months week weeks hour hours minute minutes second seconds
mac windows linux ios android web page pages site sites
pro max mini ultra plus lite air note play store shop
you your our my his her its their us we i he she it they
""".split())


def norm(s):
    return s.lower().replace("_", "").replace("-", "").replace(".", "").replace(" ", "")


def scan_file(path):
    """返回 [(start_sec, line_text)]"""
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                m = LINE.match(raw.strip())
                if m:
                    out.append((float(m.group(1)), m.group(3)))
                elif raw.strip():
                    out.append((None, raw.strip()))
    except OSError:
        pass
    return out


def run(root, out_path="term-audit.md", json_path="term-audit.json", top=30, terms=None):
    rules = termrules.load(terms)

    videos = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if "transcript.txt" in filenames:
            p = os.path.join(dirpath, "transcript.txt")
            videos.append((os.path.basename(dirpath), p, scan_file(p)))

    hits = []
    for vid, path, lines in videos:
        for sec, text in lines:
            for h in termrules.find_hits(text, rules):
                hits.append({
                    "detector": "dict", "video": vid, "file": path, "sec": sec,
                    "found": text[max(0, h["start"] - 12): h["end"] + 12].strip(),
                    "surface": h["surface"], "suggest": h["canonical"],
                    "confidence": h["confidence"],
                })

    forms = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(list)
    for vid, path, lines in videos:
        for sec, text in lines:
            for tok in TOKEN.findall(text):
                tok = tok.strip("._-")
                k = norm(tok)
                if len(k) < 3 or tok.lower() in COMMON:
                    continue
                forms[k][tok] += 1
                where[k].append((vid, path, sec, text))

    variants = []
    for k, counter in forms.items():
        if sum(counter.values()) < 5:
            continue
        lowered = {t.lower() for t in counter}
        if len(lowered) < 2:
            continue
        if all(t.lower() in COMMON for t in counter):
            continue
        if not any(any(ch.isdigit() for ch in t) or "." in t or "_" in t or len(t) >= 4 for t in counter):
            continue
        vids = {w[0] for w in where[k]}
        variants.append((sum(counter.values()), k, counter, len(vids)))
    variants.sort(reverse=True)

    shape = []
    for vid, path, lines in videos:
        for sec, text in lines:
            for m in re.finditer(r"(?i)\b(gpt|claude|iphone|macbook|m|v|sora|gemini)\s+([0-9]{1,2}(?:\.[0-9])?)\b", text):
                shape.append({"detector": "shape", "video": vid, "file": path, "sec": sec,
                              "found": m.group(0), "suggest": "%s-%s" % (m.group(1), m.group(2))})

    n_videos = len(videos)
    n_lines = sum(len(v[2]) for v in videos)
    touched = {h["video"] for h in hits} | {s["video"] for s in shape}
    by_term = collections.Counter(h["suggest"] for h in hits)

    report = ["# 术语体检报告\n"]
    report.append("- 词表 %s sha256 %s" % (
        os.path.basename(terms or termrules.DEFAULT_TERMS),
        hashlib.sha256(open(terms or termrules.DEFAULT_TERMS, "rb").read()).hexdigest()[:12]))
    report.append("- 语料：%d 条 transcript，%d 行字幕" % (n_videos, n_lines))
    report.append("- 疑似命中：%d 处（已知错写）+ %d 处（版本号写法）" % (len(hits), len(shape)))
    report.append("- 涉及视频：%d 条（占 %.0f%%）" % (len(touched), 100.0 * len(touched) / max(1, n_videos)))
    report.append("")
    report.append("## 一、已知产品名的错写\n")
    report.append("| 正确写法 | 命中 | 涉及视频 |")
    report.append("|---|---|---|")
    for term, c in by_term.most_common():
        vids = {h["video"] for h in hits if h["suggest"] == term}
        report.append("| %s | %d | %d |" % (term, c, len(vids)))
    report.append("")
    report.append("### 明细（前 %d 条）" % top)
    report.append("")
    report.append("| 时间 | 视频 | 命中 | 建议 | 置信 |")
    report.append("|---|---|---|---|---|")
    for h in sorted(hits, key=lambda x: (x["suggest"], x["video"], x["sec"] or 0))[:top]:
        report.append("| %s | %s | %s | %s | %s |" % (
            "--" if h["sec"] is None else "%.1fs" % h["sec"],
            h["video"][:14], h["found"], h["suggest"], h["confidence"]))
    report.append("")
    report.append("## 二、同一术语的多种拼法（跨视频）\n")
    report.append("| 命中 | 规范形 | 拼法 | 视频数 |")
    report.append("|---|---|---|---|")
    for total, k, counter, nv in variants[:top]:
        spelled = " / ".join("%s×%d" % (t, c) for t, c in counter.most_common(4))
        report.append("| %d | %s | %s | %d |" % (total, k, spelled, nv))
    report.append("")
    report.append("## 三、版本号/型号写法异常\n")
    for s in shape[:top]:
        report.append("- %.1fs %s：%s → 建议 %s" % (s["sec"] or 0, s["video"][:14], s["found"], s["suggest"]))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report))

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({
            "terms": os.path.abspath(terms or termrules.DEFAULT_TERMS),
            "terms_sha256": hashlib.sha256(open(terms or termrules.DEFAULT_TERMS, "rb").read()).hexdigest(),
            "videos": n_videos, "lines": n_lines, "hits": hits,
            "variants": [{"total": t, "norm": k, "forms": dict(c), "videos": nv} for t, k, c, nv in variants],
            "shape": shape,
        }, fh, ensure_ascii=False, indent=1)

    print("videos=%d lines=%d hits=%d variants=%d shape=%d touched=%d" % (
        n_videos, n_lines, len(hits), len(variants), len(shape), len(touched)))
    print("report ->", os.path.abspath(out_path))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum audit", description="术语体检")
    ap.add_argument("root", help="语料根目录（下面放着各个 vsum 输出目录）")
    ap.add_argument("--out", default="term-audit.md", help="报告路径，默认 term-audit.md")
    ap.add_argument("--json", default="term-audit.json", help="机器可读报告路径")
    ap.add_argument("--top", type=int, default=30, help="每个词条最多列几条例子，默认 30")
    ap.add_argument("--terms", default=None, help="词表路径，默认包内 data/terms.json")
    args = ap.parse_args(argv)
    if not os.path.exists(args.root):
        print("error: 目录不存在：%s" % args.root, file=sys.stderr)
        return 2
    return run(args.root, args.out, args.json, args.top, args.terms)