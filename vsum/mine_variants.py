# -*- coding: utf-8 -*-
"""从语料里挖「词表外的产品名变体」，产出候选队列给人审。

两条通道，都只产出候选：编辑距离 1–2（字母级错写）+ Soundex 骨架相同（元音错位）。
"""
import argparse
import collections
import json
import os
import re

from . import phonetics, termrules

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.]{3,}")

WATCHLIST = {
    "Codex": ["codex"], "GitHub": ["github"], "Opus": ["opus"], "Llama": ["llama"],
    "Qwen": ["qwen"], "Kimi": ["kimi"], "Manus": ["manus"], "Notion": ["notion"],
    "Sora": ["sora"], "VSCode": ["vscode"], "Homebrew": ["homebrew"], "Spotlight": ["spotlight"],
}

COMMON = set("""
model models cover covers sorry focus option options action actions store stores
means makes hands wants comes drama venus astra minus modus linus clause cause
secure source direct object subject report select record result version support
cloudy code codes user users image images music video videos prompt prompts
""".split())


def normalize(s):
    return "".join(c for c in s.lower() if c.isascii() and c.isalnum())


def build_products(terms_path=None):
    """terms.json 的 canonical/写法 + WATCHLIST → {canonical: [normalized forms]}"""
    products = {k: list(v) for k, v in WATCHLIST.items()}
    for r in termrules.load(terms_path):
        forms = products.setdefault(r["canonical"], [])
        for form in (r["canonical"], r["surface"]):
            norm = normalize(form)
            if norm and norm not in forms:
                forms.append(norm)
    return products


def edit_distance(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def mine(counts, products, min_count=2, edit_only=False):
    """counts: {token: 次数} → 候选列表。纯函数，方便单测。"""
    known = {form for forms in products.values() for form in forms}
    phonetic_index = {}
    ambiguous = set()
    for canon, forms in products.items():
        for form in forms:
            key = phonetics.soundex(form)
            if not key:
                continue
            if key in phonetic_index and phonetic_index[key] != canon:
                ambiguous.add(key)
            phonetic_index.setdefault(key, canon)

    cands = []
    for tok, n in counts.items():
        low = normalize(tok)
        if n < min_count or len(low) < 5 or low in COMMON or low in known:
            continue
        best = None
        for canon, forms in products.items():
            for form in forms:
                d = edit_distance(low, form)
                if 1 <= d <= 2 and (best is None or d < best[0]):
                    best = (d, canon)
        if best:
            cands.append({"surface": tok, "count": n, "nearest": best[1],
                          "distance": best[0], "channel": "edit"})
            continue
        if edit_only:
            continue
        key = phonetics.soundex(low)
        if not key or key in ambiguous:
            continue
        canon = phonetic_index.get(key)
        if not canon:
            continue
        if not any(abs(len(low) - len(f)) <= max(2, len(f) // 2) for f in products[canon]):
            continue
        cands.append({"surface": tok, "count": n, "nearest": canon,
                      "distance": None, "channel": "phonetic"})

    cands.sort(key=lambda c: (-c["count"], c["surface"]))
    return cands


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum mine", description="挖词表外的变体候选")
    ap.add_argument("root", help="语料根目录（下面放着各个 vsum 输出目录）")
    ap.add_argument("--top", type=int, default=40, help="最多列几条候选，默认 40")
    ap.add_argument("--min-count", type=int, default=2, help="至少出现几次才算候选，默认 2")
    ap.add_argument("--json", default="", help="候选写进这个文件（默认只打印）")
    ap.add_argument("--edit-only", action="store_true", help="只跑编辑距离通道")
    ap.add_argument("--terms", default=None)
    args = ap.parse_args(argv)

    counts = collections.Counter()
    for dirpath, _dirnames, filenames in os.walk(args.root):
        if "transcript.txt" not in filenames:
            continue
        with open(os.path.join(dirpath, "transcript.txt"), encoding="utf-8", errors="ignore") as fh:
            for tok in TOKEN.findall(fh.read()):
                counts[tok] += 1

    cands = mine(counts, build_products(args.terms), args.min_count, args.edit_only)
    print("candidates: %d  (需人工确认，不要直接进词表)" % len(cands))
    for c in cands[: args.top]:
        d = "-" if c["distance"] is None else str(c["distance"])
        print("%5d  %-16s -> %-10s d=%-2s [%s]" % (c["count"], c["surface"], c["nearest"], d, c["channel"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(cands, fh, ensure_ascii=False, indent=1)
        print("json ->", args.json)
    return 0
