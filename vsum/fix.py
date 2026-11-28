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

from . import atomic, termrules

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
    """给记录补 segment / low_conf（全片 avg_logprob 最低 10%），返回 low_conf 条数。

    低置信判定和 transcript.md 的 ⚠ 用同一个函数，避免两处口径漂移。
    """
    from . import export as export_mod

    low_idx = export_mod.low_conf_indexes(segments)
    low = 0
    for r in records:
        seg = None
        seg_no = None
        if r["sec"] is not None:
            for i, s in enumerate(segments, 1):
                if s["start"] <= r["sec"] <= s["end"]:
                    seg, seg_no = s, i
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
        r["low_conf"] = bool(seg_no in low_idx)
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


def rules_from_ocr(path):
    """把 `vsum ocr` 的候选变成词表规则（中英同一套）。

    置信度不是拍脑袋：同一个「画面写法 vs 转写写法」在 >=2 帧里都成立，且
    形状相似度 >=0.8 / 读音骨架相同 / 是逐字差异聚合出来的，才标 high 让它自动改；
    只出现过一次的标 medium，仍然要人过目。

    单字差异（「大」→「那」）一律不采用：它太容易误伤别的句子。中英都要求
    surface 至少两个字符。
    """
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    rules = []
    for c in doc.get("candidates") or []:
        if c.get("applicable") is False:
            continue
        surface = str(c.get("surface") or "").strip().lower()
        canonical = str(c.get("canonical") or "").strip()
        if len(surface) < 2 or not canonical or surface == canonical.lower():
            continue
        strong = int(c.get("hits") or 0) >= 2 and (
            float(c.get("ratio") or 0) >= 0.8 or c.get("why") in ("sound", "char"))
        rules.append({"canonical": canonical, "surface": surface,
                      "confidence": "high" if strong else "medium"})
    return rules


def process(path, rules, apply, apply_medium, ocr_surfaces=None):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    lines = raw.splitlines()
    new_lines, records = fix_lines(lines, rules, apply_medium)
    if ocr_surfaces:
        for r in records:
            if r["surface"] in ocr_surfaces:
                r["source"] = "ocr"
    dirname = os.path.dirname(path)
    segments = load_segments(dirname)
    result = {"transcript": path, "low_conf": annotate(records, segments),
              "corrections": records}

    fixed_txt = os.path.join(dirname, "transcript.fixed.txt")
    fixed_seg = os.path.join(dirname, "segments.fixed.json")
    if not apply:
        return result
    if any(r["applied"] for r in records):
        atomic.write_text(fixed_txt, "\n".join(new_lines) + ("\n" if raw.endswith("\n") else ""))
        result["fixed"] = fixed_txt
        if segments:
            atomic.write_json(fixed_seg,
                              {"segments": fix_segments(segments, rules, apply_medium)})
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
    ap.add_argument("--terms", default=None, help="词表路径，默认包内 data/terms.json")
    ap.add_argument("--from-ocr", default=None, metavar="OCR_JSON",
                    help="把 vsum ocr 的候选并进本次修正（画面上的拼写当权威），无需改词表")
    ap.add_argument("--apply", action="store_true", help="真的写 transcript.fixed.txt（默认只预演）")
    ap.add_argument("--apply-medium", action="store_true", help="medium 命中也写盘")
    ap.add_argument("--json", default=None, help="corrections.json 输出路径")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐条打印改动")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print("error: 路径不存在：%s" % args.path, file=sys.stderr)
        return 2
    rules = termrules.load(args.terms)
    ocr_surfaces = set()
    if args.from_ocr:
        if not os.path.exists(args.from_ocr):
            print("error: OCR 结果不存在：%s" % args.from_ocr, file=sys.stderr)
            return 2
        extra = rules_from_ocr(args.from_ocr)
        known = {(r["surface"], r["canonical"]) for r in rules}
        confirmed = [r for r in extra if (r["surface"], r["canonical"]) in known]
        extra = [r for r in extra if (r["surface"], r["canonical"]) not in known]
        ocr_surfaces = {r["surface"] for r in extra}
        rules = rules + extra
        rules.sort(key=lambda r: (-len(r["surface"]), r["surface"]))
        print("从 OCR 候选并入 %d 条（high %d / medium %d）"
              % (len(extra), sum(1 for r in extra if r["confidence"] == "high"),
                 sum(1 for r in extra if r["confidence"] != "high")))
        if confirmed:
            print("另有 %d 条与词表已有条目一致（画面也看到了）：%s；"
                  "要改请看上面的 medium 提示" % (
                      len(confirmed),
                      "、".join("%s→%s" % (r["surface"], r["canonical"]) for r in confirmed[:5])))
    files = []
    for path in iter_transcripts(args.path):
        res = process(path, rules, args.apply, args.apply_medium, ocr_surfaces)
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
        # corrections.json 是「输入」不是「产物」：手写清单（顶层 corrections 格式，
        # pipeline.load_corrections 明确支持）和上一轮 fix 写的清单长得一模一样，
        # 删掉就等于删用户数据。只提示，不动文件。
        if os.path.exists(out):
            print("没有需要改的，保留已有 %s（若是过期清单，请自行删除）" % out)
        else:
            print("没有需要改的，不写 corrections.json。")
        return 0
    atomic.write_json(out, {"terms": args.terms or termrules.DEFAULT_TERMS, "total": total,
                            "high": high, "medium": total - high, "applied": applied,
                            "files": files})
    if applied:
        print("已写入 %d 个 transcript.fixed.txt（%d 处）与 %s"
              % (len([f for f in files if "fixed" in f]), applied, out))
        print("有 segments.json 的目录同时写了 segments.fixed.json；"
              "vsum export --segments segments.fixed.json 出的是改过词的字幕。")
    else:
        print("只有 medium 命中，未写 fixed；清单（applied=false）见 %s" % out)
    return 0