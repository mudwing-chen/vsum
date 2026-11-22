# -*- coding: utf-8 -*-
"""vsum find / vsum clip：把「可引用」变成能摸到的东西。

find：在 segments.json 里全文搜索，命中项直接给 [mm:ss]。
clip：按时间点把原视频那一段剪出来（默认前后各留 1.5 秒）。

这两个命令是 vsum 和「一个总结工具」的分界线：总结工具给你结论，
这两个给你「他到底在哪一秒说的、原话是什么、点开就能听」。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from .timefmt import mmss


def load_segments(path):
    """接受目录或 segments.json 路径。返回 (segments, 所在目录)。"""
    if os.path.isdir(path):
        seg_path = os.path.join(path, "segments.json")
        base = path
    else:
        seg_path = path
        base = os.path.dirname(os.path.abspath(path))
    if not os.path.exists(seg_path):
        raise FileNotFoundError("没有 segments.json：%s" % seg_path)
    with open(seg_path, encoding="utf-8") as fh:
        data = json.load(fh)
    segs = data.get("segments") or []
    if not segs:
        raise ValueError("segments.json 是空的")
    return segs, base


def parse_time(text):
    """'03:41' / '3:41.5' / '221.5' / '1:02:03' -> 秒。"""
    text = str(text).strip()
    if not text:
        raise ValueError("空的 time")
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("时间格式不对：%s（用 mm:ss 或秒数）" % text)
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def search(segments, terms, limit=20):
    """所有词都要命中（AND），大小写不敏感。返回按时间排序的命中。"""
    needles = [t.lower() for t in terms if t.strip()]
    if not needles:
        return []
    hits = []
    for i, s in enumerate(segments):
        text = s.get("text") or ""
        low = text.lower()
        if all(n in low for n in needles):
            hits.append({"index": i, "start": s["start"], "end": s["end"], "text": text})
    return hits[:limit]


def find_main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum find",
                                 description="在逐字稿里搜索，命中项带 [mm:ss]")
    ap.add_argument("path", help="输出目录，或某个 segments.json")
    ap.add_argument("query", nargs="+", help="一个或多个词（全部命中才算）")
    ap.add_argument("--limit", type=int, default=20, help="最多显示几条命中，默认 20")
    ap.add_argument("--context", type=int, default=0,
                    help="每个命中再带上前后各 N 段，方便看上下文")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 JSON")
    args = ap.parse_args(argv)

    segments, base = load_segments(args.path)
    hits = search(segments, args.query, args.limit)
    if args.json:
        print(json.dumps({"query": args.query, "dir": os.path.abspath(base),
                          "hits": hits}, ensure_ascii=False))
        return 0 if hits else 1
    if not hits:
        print("没找到：%s" % " ".join(args.query), file=sys.stderr)
        return 1
    print("%d 处命中（%s）" % (len(hits), " ".join(args.query)))
    seen = set()
    for h in hits:
        idx = h["index"]
        rng = range(max(0, idx - args.context), min(len(segments), idx + args.context + 1))
        for j in rng:
            if j in seen:
                continue
            seen.add(j)
            s = segments[j]
            mark = ">" if j == idx else " "
            print("%s [%s] %s" % (mark, mmss(s["start"]), (s.get("text") or "").strip()))
        if args.context:
            print("")
    print("")
    print("剪出来听：vsum clip %s %s -o clip.mp4" % (base, mmss(hits[0]["start"])))
    return 0


def _source_media(base):
    """找原视频：manifest 记的输入优先，其次 video.mp4 / audio.wav。"""
    man_path = os.path.join(base, "manifest.json")
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as fh:
                man = json.load(fh)
            cand = (man.get("input") or {}).get("path")
            if cand and os.path.exists(cand):
                return cand, "manifest"
        except (OSError, ValueError):
            pass
    for name, why in (("video.mp4", "video.mp4"), ("audio.wav", "audio.wav")):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p, why
    return None, None


def clip_main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum clip",
                                 description="按时间点把原视频剪出来（找不到视频就剪音频）")
    ap.add_argument("path", help="输出目录，或某个 segments.json")
    ap.add_argument("time", help="时间点：mm:ss 或秒数")
    ap.add_argument("-o", "--out", required=True, help="输出文件，如 clip.mp4")
    ap.add_argument("--pad", type=float, default=1.5, help="前后各留几秒，默认 1.5")
    ap.add_argument("--duration", type=float, default=None, help="固定时长，覆盖自动边界")
    args = ap.parse_args(argv)

    segments, base = load_segments(args.path)
    try:
        at = parse_time(args.time)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    seg = None
    for s in segments:
        if float(s["start"]) <= at <= float(s["end"]):
            seg = s
            break
    if seg is None:
        nxt = min(segments, key=lambda s: abs(float(s["start"]) - at))
        if abs(float(nxt["start"]) - at) > 3:
            print("error: %s 不在任何一段里（最近的一段在 %s）"
                  % (mmss(at), mmss(nxt["start"])), file=sys.stderr)
            return 2
        seg = nxt

    start = max(0.0, float(seg["start"]) - args.pad)
    if args.duration:
        end = start + args.duration
    else:
        end = float(seg["end"]) + args.pad

    src, why = _source_media(base)
    if not src:
        print("error: 这个目录里没有原视频/音频（只在 manifest、video.mp4、audio.wav 里找）",
              file=sys.stderr)
        return 4
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("error: 没找到 ffmpeg", file=sys.stderr)
        return 3

    out_ext = os.path.splitext(args.out)[1].lower()
    audio_only = out_ext in (".wav", ".mp3", ".m4a", ".aac", ".flac")
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", "%.3f" % start, "-to", "%.3f" % end, "-i", src]
    if audio_only:
        cmd += ["-vn"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k"]
    cmd.append(args.out)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(args.out):
        print("error: ffmpeg 剪切失败：%s" % (proc.stderr.strip()[:300] or "exit %d" % proc.returncode),
              file=sys.stderr)
        return 4

    print("片段 %s – %s（%s）-> %s" % (mmss(start), mmss(end), why, args.out))
    print("原话 [%s] %s" % (mmss(seg["start"]), (seg.get("text") or "").strip()))
    return 0
