# -*- coding: utf-8 -*-
"""vsum eval：CER（含重采样区间）、术语召回（Wilson）、manifest 自洽检查。

口径：
- 归一化 = NFKC + 只保留 Unicode L/N 类字符（去标点空白）+ 大小写折叠；数字不做文字转数字。
- CER = 编辑距离 / 参考文本长度。**CER 不是比例，不能配 Wilson**；区间按 cue 配对
  重采样（按时间重叠把 cue 和段配对，>=5 个 cue 才算），衡量的是
  「换成另一组同分布 cue 会差多少」。cue 聚合值系统性 >= 全局 CER——cue 边界切断
  跨句对齐，这是配对统计的定义导致的，所以两个数分开打。单条文本没有可重采样的
  单元，就不给区间（不假装能算）。
- 术语指标在无逐处标注时只能算**召回**（参考里出现的词条，假设里还在不在），
  召回是比例，配 Wilson。精确率/F1 需要人工标「这处该不该改」，本项目没有，故不报。
"""
import argparse
import json
import math
import os
import random
import re
import sys
import unicodedata

from . import manifest as manifest_mod

BOOTSTRAP = 1000
SEED = 0


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in text if unicodedata.category(c)[0] in ("L", "N"))


def levenshtein(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref, hyp):
    ref_n, hyp_n = normalize(ref), normalize(hyp)
    if not ref_n:
        return None
    return levenshtein(ref_n, hyp_n) / float(len(ref_n))


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / float(n)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


TS = re.compile(r"^\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _secs(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _cues(path):
    """把 srt/vtt 切成 [(start, end, 文本)]；不是 srt/vtt 就返回 None。"""
    if not path.endswith((".srt", ".vtt")):
        return None
    with open(path, encoding="utf-8", errors="ignore") as fh:
        raw = fh.read()
    cues, cur, span = [], [], None
    for line in raw.splitlines():
        t = line.strip()
        if not t:
            if cur and span:
                cues.append((span[0], span[1], "".join(cur)))
            cur, span = [], span
            continue
        m = TS.match(t)
        if m:
            span = (_secs(*m.groups()[:4]), _secs(*m.groups()[4:]))
            continue
        if t.isdigit() or t.upper() == "WEBVTT":
            continue
        cur.append(t)
    if cur and span:
        cues.append((span[0], span[1], "".join(cur)))
    return cues or None


def pair_cues(ref_cues, segments):
    """把参考 cue 和转写段配对，返回 [(参考字数, 距离)]。

    两种输入都收：
    - [(start, end, text)]：按**时间重叠**配对（正常路径；cue 数不必等于段数）
    - [text]：只有文本时按序号配对，要求数量相等（老调用方的兼容路径）

    早先只支持后者，真实字幕几乎不满足「cue 数 == 段数」，那个能力永远输出「跳过」。
    """
    if not ref_cues:
        return []
    if isinstance(ref_cues[0], str):
        if len(ref_cues) != len(segments):
            return []
        return [(len(normalize(t)), levenshtein(normalize(t), normalize(s.get("text") or "")))
                for t, s in zip(ref_cues, segments)]
    pairs = []
    for start, end, text in ref_cues:
        ref_n = normalize(text)
        if not ref_n:
            continue
        hyp = "".join(s.get("text") or "" for s in segments
                      if float(s.get("start") or 0) < end and float(s.get("end") or 0) > start)
        pairs.append((len(ref_n), levenshtein(ref_n, normalize(hyp))))
    return pairs


def bootstrap_cer_cues(ref_cues, segments, n=BOOTSTRAP, seed=SEED):
    """按 cue 配对重采样 CER。返回 (低, 中位, 高, cue 聚合值)；不足 5 个 cue 返回 None。

    cue 聚合值系统性 ≥ 全局 CER：cue 边界切断了跨句对齐，这是配对统计的定义，
    不是 bug。两个数分开打，别混着用。
    """
    if not ref_cues or not segments:
        return None
    pairs = pair_cues(ref_cues, segments)
    if len(pairs) < 5 or not sum(l for l, _ in pairs):
        return None
    k = len(pairs)
    agg = sum(d for _, d in pairs) / float(sum(l for l, _ in pairs))
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        num = den = 0
        for _ in range(k):
            ln, dist = pairs[rnd.randrange(k)]
            num += dist
            den += ln
        if den:
            vals.append(num / float(den))
    vals.sort()
    return (vals[int(len(vals) * 0.025)], vals[len(vals) // 2], vals[int(len(vals) * 0.975)], agg)


def _read_ref(path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        raw = fh.read()
    if path.endswith(".srt") or path.endswith(".vtt"):
        lines = []
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.isdigit() or "-->" in s or s.upper() == "WEBVTT":
                continue
            lines.append(s)
        return "".join(lines)
    return raw


def _pick_ref(ref_dir, input_path):
    files = [os.path.join(ref_dir, f) for f in sorted(os.listdir(ref_dir))
             if f.endswith((".txt", ".srt", ".vtt"))]
    if not files:
        return None
    stem = os.path.splitext(os.path.basename(input_path))[0]
    for f in files:
        if os.path.splitext(os.path.basename(f))[0] == stem:
            return f
    return files[0] if len(files) == 1 else None


def load_hyp(path):
    """假设文本：segments.json / segments.fixed.json / transcript.fixed.txt / 纯文本。"""
    with open(path, encoding="utf-8", errors="ignore") as fh:
        raw = fh.read()
    if path.endswith(".json"):
        data = json.loads(raw)
        return "".join(s.get("text", "") for s in data.get("segments", [])), data.get("segments", [])
    import re

    lines = [re.sub(r"^\s*\[\s*[0-9.]+\s*->\s*[0-9.]+\s*\]\s*", "", l) for l in raw.splitlines()]
    return "".join(lines), None


def term_report(ref_text, hyp_text, terms_path):
    with open(terms_path, encoding="utf-8") as fh:
        canon = [t["canonical"] for t in json.load(fh)["terms"]]
    ref_n, hyp_n = normalize(ref_text), normalize(hyp_text)
    expected = [c for c in canon if normalize(c) and normalize(c) in ref_n]
    found = [c for c in expected if normalize(c) in hyp_n]
    missing = [c for c in expected if c not in found]
    return expected, found, missing


def run(manifest_path, ref_dir=None, min_cer=None, hyp_path=None, quiet=False):
    if not os.path.exists(manifest_path):
        raise FileNotFoundError("manifest 不存在：%s" % manifest_path)
    man = manifest_mod.read(manifest_path)
    out_dir = os.path.dirname(os.path.abspath(manifest_path))

    if hyp_path:
        hyp, segs = load_hyp(hyp_path)
        label = os.path.basename(hyp_path)
    else:
        seg_path = os.path.join(out_dir, "segments.json")
        if not os.path.exists(seg_path):
            raise FileNotFoundError("没有 segments.json：%s（或用 --hyp 指定）" % seg_path)
        hyp, segs = load_hyp(seg_path)
        label = "segments.json"

    if not quiet:
        print("hypothesis          : %s" % label)
        if segs is not None:
            print("segments            : %d" % len(segs))
        print("chars               : %d" % len(normalize(hyp)))
        warnings = man.get("warnings", {})
        print("long cues           : %d" % len(warnings.get("long_cues", [])))
        print("invalid cues        : %d" % len(warnings.get("invalid_cues", [])))
        low = [s for s in (segs or []) if s.get("avg_logprob") is not None]
        if low:
            low.sort(key=lambda s: s["avg_logprob"])
            print("low-confidence segs : %d (bottom 10%%)" % max(1, len(low) // 10))

    if not ref_dir:
        if not quiet:
            print("no --ref: 只做 manifest 自洽检查，不算 CER")
        return 0

    ref_path = _pick_ref(ref_dir, man.get("input", {}).get("path", ""))
    if not ref_path:
        raise FileNotFoundError("参考目录里没有匹配文本（文件名与输入不一致，且不止一个文件）")
    ref_text = _read_ref(ref_path)
    value = cer(ref_text, hyp)
    if value is None:
        raise ValueError("参考文本归一化后为空")

    ref_len = len(normalize(ref_text))
    print("CER                 : %.2f%%  (ref %s, %d 字)" % (value * 100, os.path.basename(ref_path), ref_len))
    band = bootstrap_cer_cues(_cues(ref_path), segs)
    if band:
        cues = _cues(ref_path) or []
        print("CER cues (paired)   : %.2f%%  (%d 个 cue 按时间配对；cue 边界切断对齐，系统性 ≥ 全局值)"
              % (band[3] * 100, len(cues)))
        print("CER cue 95%% band    : %.2f%% .. %.2f%%  (%d 次按 cue 重采样)"
              % (band[0] * 100, band[2] * 100, BOOTSTRAP))
    else:
        print("CER 95% band        : 跳过（参考不是 .srt/.vtt，或 cue 少到没有可重采样的单元）")

    terms_path = man.get("terms", {}).get("path")
    if terms_path and os.path.exists(terms_path):
        expected, found, missing = term_report(ref_text, hyp, terms_path)
        if expected:
            lo, hi = wilson(len(found), len(expected))
            print("term recall         : %d/%d = %.1f%%  (95%% Wilson %.1f%% .. %.1f%%)"
                  % (len(found), len(expected), 100.0 * len(found) / len(expected), lo * 100, hi * 100))
            if missing:
                print("terms missing       : %s" % ", ".join(missing))
            print("term precision/F1   : 不报——需要逐处人工标注「该不该改」，本项目没有")
        else:
            print("term recall         : 参考文本里没有词表词条，跳过")

    if min_cer is not None and value > min_cer:
        print("CER %.2f%% > --min-cer %.2f%%" % (value * 100, min_cer * 100), file=sys.stderr)
        return 5
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum eval", description="按 manifest 评测")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--ref", default=None, help="参考文本目录（.txt/.srt/.vtt）")
    ap.add_argument("--hyp", default=None,
                    help="假设文本：segments.json / segments.fixed.json / transcript.fixed.txt；默认用 manifest 同目录 segments.json")
    ap.add_argument("--min-cer", type=float, default=None, help="超过就退出 5")
    args = ap.parse_args(argv)
    return run(args.manifest, args.ref, args.min_cer, args.hyp)
