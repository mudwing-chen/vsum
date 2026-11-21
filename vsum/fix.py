# -*- coding: utf-8 -*-
"""按词表修正 transcript.txt 里的错写。

产出（只在 apply 时写盘，原 transcript.txt 永不改动）：
  <同目录>/transcript.fixed.txt   替换后的全文（默认只含 high 改动）
  <修订单>/corrections.json       每处命中：行号、时间戳、原词、替换词、置信度、applied

置信门槛：high 才进 fixed；medium 默认只报不改，要写必须 apply_medium。
"""
import argparse
import json
import os
import re
import sys

from . import termrules

TS = re.compile(r"^\s*\[\s*([0-9.]+)\s*->\s*([0-9.]+)\s*\]")


def iter_transcripts(root):
    """root 是文件就直接用它；是目录就找所有 transcript.txt。"""
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if "transcript.txt" in filenames:
            yield os.path.join(dirpath, "transcript.txt")


def load_segments(dirpath):
    """读同目录的 segments.json；没有就返回 []。只用于标注，不参与决策。"""
    path = os.path.join(dirpath, "segments.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("segments") or []
    except (OSError, ValueError, AttributeError):
        return []


def annotate(records, segments):
    """给记录补 segment / low_conf（全片 avg_logprob 最低 10% 分位），返回 low_conf 条数。"""
    vals = sorted(s["avg_logprob"] for s in segments if s.get("avg_logprob") is not None)
    cut = vals[max(0, int(len(vals) * 0.1) - 1)] if vals else None
    low = 0
    for r in records:
        seg = None
        if r["sec"] is not None:
            for s in segments:
                if s["start"] <= r["sec"] <= s["end"]:
                    seg = s
                    break
        if seg is None:
            r["segment"] = None
            r["low_conf"] = False
            continue
        r["segment"] = {
            "avg_logprob": seg.get("avg_logprob"),
            "no_speech_prob": seg.get("no_speech_prob"),
            "compression_ratio": seg.get("compression_ratio"),
        }
        r["low_conf"] = bool(cut is not None and seg.get("avg_logprob") is not None
                             and seg["avg_logprob"] <= cut)
        low += 1 if r["low_conf"] else 0
    return low


def fix_lines(lines, rules, apply_medium=False):
    """返回 (写入用的行列表, 所有命中记录)。"""
    out, records = [], []
    for lineno, line in enumerate(lines, 1):
        hits = termrules.find_hits(line, rules)
        if not hits:
            out.append(line)
            continue
        m = TS.match(line)
        sec = float(m.group(1)) if m else None
        keep = []
        for h in hits:
            applied = h["confidence"] == "high" or apply_medium
            if applied:
                keep.append(h)
            records.append({
                "line": lineno, "sec": sec, "surface": h["surface"],
                "replacement": h["canonical"], "confidence": h["confidence"],
                "applied": applied,
                "before": line[max(0, h["start"] - 12):h["end"] + 12].strip(),
            })
        out.append(termrules.apply_hits(line, keep) if keep else line)
    return out, records


def fix_segments(segments, rules, apply_medium=False):
    """把同样的规则落到段上：只改 text，start/end/avg_logprob 原样保留。"""
    out = []
    for s in segments:
        seg = dict(s)
        hits = termrules.find_hits(seg.get("text", ""), rules)
        keep = [h for h in hits if h["confidence"] == "high" or apply_medium]
        if keep:
            seg["text"] = termrules.apply_hits(seg["text"], keep)
        out.append(seg)
    return out


def process(path, rules, apply, apply_medium):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    lines = raw.splitlines()
    new_lines, records = fix_lines(lines, rules, apply_medium)
    dirname = os.path.dirname(path)
    segments = load_segments(dirname)
    result = {"transcript": path, "low_conf": annotate(records, segments),
              "corrections": records}

    fixed_txt = os.path.join(dirname, "transcript.fixed.txt")
    fixed_seg = os.path.join(dirname, "segments.fixed.json")
    if not apply:
        return result
    if any(r["applied"] for r in records):
        with open(fixed_txt, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + ("\n" if raw.endswith("\n") else ""))
        result["fixed"] = fixed_txt
        if segments:
            with open(fixed_seg, "w", encoding="utf-8") as fh:
                json.dump({"segments": fix_segments(segments, rules, apply_medium)}, fh,
                          ensure_ascii=False, indent=1)
                fh.write("\n")
            result["fixed_segments"] = fixed_seg
    else:
        # 上一轮的 fixed 是旧词表的产物：这轮零命中就必须删掉，否则导出/评测读到过期稿
        stale = [p for p in (fixed_txt, fixed_seg) if os.path.exists(p)]
        for p in stale:
            os.remove(p)
        if stale:
            result["removed"] = stale
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum fix", description="按术语表修正转写文本")
    ap.add_argument("path", help="语料根目录，或单个 transcript.txt")
    ap.add_argument("--terms", default=None)
    ap.add_argument("--apply", action="store_true", help="真的写 transcript.fixed.txt（默认只预演）")
    ap.add_argument("--apply-medium", action="store_true", help="medium 命中也写盘")
    ap.add_argument("--json", default=None, help="corrections.json 输出路径")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐条打印改动")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print("error: 路径不存在：%s" % args.path, file=sys.stderr)
        return 2
    rules = termrules.load(args.terms)
    files = []
    for path in iter_transcripts(args.path):
        res = process(path, rules, args.apply, args.apply_medium)
        files.append(res)
        print("%s: %d 处" % (path, len(res["corrections"])))
        for p in res.get("removed", []):
            print("  删除过期产物 %s" % os.path.basename(p))
        if args.verbose:
            for c in res["corrections"]:
                print("  %s L%d @%ss  %s → %s [%s]" % (
                    os.path.basename(os.path.dirname(path)), c["line"], c["sec"],
                    c["surface"], c["replacement"], c["confidence"]))

    flat = [c for f in files for c in f["corrections"]]
    total, high = len(flat), sum(1 for c in flat if c["confidence"] == "high")
    applied = sum(1 for c in flat if c["applied"])
    print("---")
    print("文件 %d 个，命中 %d 处（high %d / medium %d），将写入 fixed %d 处"
          % (len(files), total, high, total - high, applied))
    if total - high and not args.apply_medium:
        print("medium %d 处只报不改（裸 cloud / cloud.md / open cloud / 克劳得 等）；"
              "确认过的词可用 --apply-medium 写入。" % (total - high))
    low = sum(f["low_conf"] for f in files)
    if low:
        print("其中 %d 处落在低置信段（全片 avg_logprob 最低 10%%）——只标注，不改变改不改；"
              "要当成证据得先在标注集上验它分得开对错。" % low)

    if not args.apply:
        print("dry-run：未写任何文件。确认无误后加 --apply。")
        return 0
    out = args.json or os.path.join(args.path if os.path.isdir(args.path) else os.path.dirname(args.path), "corrections.json")
    if not total:
        # 同 fixed：上一轮的清单在零命中之后就是过期产物，留着会和「什么都没改」矛盾
        if os.path.exists(out):
            os.remove(out)
            print("没有需要改的，不写 corrections.json；删除过期产物 %s" % os.path.basename(out))
        else:
            print("没有需要改的，不写 corrections.json。")
        return 0
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"terms": args.terms or termrules.DEFAULT_TERMS, "total": total,
                   "high": high, "medium": total - high, "applied": applied, "files": files},
                  fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    if applied:
        print("已写入 %d 个 transcript.fixed.txt（%d 处）与 %s"
              % (len([f for f in files if "fixed" in f]), applied, out))
        print("有 segments.json 的目录同时写了 segments.fixed.json；"
              "vsum export --segments segments.fixed.json 出的是改过词的字幕。")
    else:
        print("只有 medium 命中，未写 fixed；清单（applied=false）见 %s" % out)
    return 0