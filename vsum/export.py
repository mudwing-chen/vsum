# -*- coding: utf-8 -*-
"""segments.json -> txt / plain.txt / srt / vtt / md（+ clean.md）。

SRT/VTT 不重新切句：Whisper 的 segment 经常在半句断开，按标点重切要线性插值时间戳，
结果会「文本对了、字幕跳了」。超过 7 秒或 42 字的 cue 只记进警告，由人决定切不切。
"""
import hashlib
import json
import os

from .timefmt import mmss as _mmss

LONG_SEC = 7.0
LONG_CHARS = 42


def srt_time(sec):
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def vtt_time(sec):
    return srt_time(sec).replace(",", ".")


def sanitize(segments):
    """丢掉空文本段；负数起点夹到 0；end<=start 补 0.05s。每处修正都记进 invalid_cues。

    invalid 记录里 index 是**源段**序号（1 起，含被丢掉的空段），cue 是它在导出文件里
    的**cue 号**（重排后；被丢掉时为 None）。两者分开写，是因为空段被删之后两个编号
    就对不上了——只给 index 会让用户去 SRT 里找错行。
    """
    clean, invalid = [], []
    for i, s in enumerate(segments, 1):
        text = (s.get("text") or "").strip()
        if not text:
            invalid.append({"index": i, "cue": None, "reason": "empty_text"})
            continue
        cue = len(clean) + 1
        start, end = float(s["start"]), float(s["end"])
        if start < 0:
            invalid.append({"index": i, "cue": cue, "reason": "negative_start", "start": start})
            start = 0.0
        if end <= start:
            invalid.append({"index": i, "cue": cue, "reason": "end_le_start", "start": start, "end": end})
            end = start + 0.05
        cue = {"start": start, "end": end, "text": text}
        for key in ("avg_logprob", "no_speech_prob"):
            if s.get(key) is not None:
                cue[key] = s[key]
        clean.append(cue)
    return clean, invalid


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _end(start, end):
    return end if end > start else start + 0.05


def to_srt(segments):
    out = []
    for i, s in enumerate(segments, 1):
        out.append("%d\n%s --> %s\n%s\n" % (i, srt_time(s["start"]), srt_time(_end(s["start"], s["end"])), s["text"]))
    return "\n".join(out)


def to_vtt(segments):
    out = ["WEBVTT", ""]
    for s in segments:
        out.append("%s --> %s" % (vtt_time(s["start"]), vtt_time(_end(s["start"], s["end"]))))
        out.append(s["text"])
        out.append("")
    return "\n".join(out)


def to_plain(segments):
    return "\n".join(s["text"] for s in segments)


LOW_CONF_Q = 0.10
CHAPTER_SEC = 300


def low_conf_indexes(segments):
    """avg_logprob 落在全片最低 10% 分位的段号（1 起），与 fix.annotate 同口径。"""
    vals = sorted(s["avg_logprob"] for s in segments if s.get("avg_logprob") is not None)
    if not vals:
        return set()
    cut = vals[max(0, int(len(vals) * LOW_CONF_Q) - 1)]
    return {i for i, s in enumerate(segments, 1)
            if s.get("avg_logprob") is not None and s["avg_logprob"] <= cut}


def _chapter_label(start, end):
    return "%s–%s" % (_mmss(start), _mmss(end))


def to_md(segments, meta=None, corrections=None):
    """transcript.md：YAML 头 + 每段一行（[mm:ss]，低置信加 ⚠）+ 5 分钟分章 + 术语附录。

    每段一行是有意的：合并段落会丢引用精度。YAML 头不写生成时间，保证重跑字节一致。
    meta 缺省时只出正文；corrections 只列 applied 的记录。
    """
    meta = meta or {}
    lines = []
    if meta:
        lines.append("---")
        lines.append("schema: 1")
        lines.append("title: %s" % json.dumps(str(meta.get("title") or ""), ensure_ascii=False))
        lines.append("source: %s" % json.dumps(str(meta.get("source") or ""), ensure_ascii=False))
        duration = meta.get("duration_sec")
        lines.append("duration_sec: %s" % ("%.1f" % duration if duration else "null"))
        lines.append("segments: %d" % len(segments))
        lines.append("chars: %d" % sum(len(s["text"]) for s in segments))
        lines.append("model: %s" % (meta.get("model") or "unknown"))
        lines.append("language: %s" % (meta.get("language") or "unknown"))
        lines.append("terms_pack: %s" % (meta.get("terms_pack") or "null"))
        lines.append("low_conf_segments: %d" % len(low_conf_indexes(segments)))
        lines.append("---")
        lines.append("")
        lines.append("# 逐字稿")
        lines.append("")

    low = low_conf_indexes(segments)
    last = max((s["end"] for s in segments), default=0.0)
    if meta.get("duration_sec"):
        last = max(last, float(meta["duration_sec"]))
    bucket = None
    for i, s in enumerate(segments, 1):
        b = int(max(0.0, s["start"]) // CHAPTER_SEC)
        if b != bucket:
            if bucket is not None:
                lines.append("")
            bucket = b
            start = b * CHAPTER_SEC
            lines.append("## %s" % _chapter_label(start, min(start + CHAPTER_SEC, last)))
            lines.append("")
        prefix = "⚠ " if i in low else ""
        lines.append("[%s] %s%s" % (_mmss(s["start"]), prefix, s["text"]))

    applied = [r for r in (corrections or []) if r.get("applied")]
    applied.sort(key=lambda r: (r.get("sec") if r.get("sec") is not None else 1e9, r.get("line") or 0))
    lines.append("")
    lines.append("## 术语修订")
    lines.append("")
    if not applied:
        lines.append("无")
    else:
        lines.append("| 时间 | 原文 | 改为 | 置信 |")
        lines.append("|---|---|---|---|")
        for r in applied:
            sec = r.get("sec")
            stamp = _mmss(sec) if sec is not None else "--:--"
            lines.append("| %s | %s | %s | %s |" % (stamp, r.get("surface"), r.get("replacement"), r.get("confidence")))
        lines.append("")
        lines.append("> 置信度是人工标签，不是概率；medium 默认只报不改。")
    return "\n".join(lines).rstrip() + "\n"


def long_cues(segments):
    return [i for i, s in enumerate(segments, 1)
            if s["end"] - s["start"] > LONG_SEC or len(s["text"]) > LONG_CHARS]


def export_all(segments, out_dir, clean_md=None, source=None, meta=None, corrections=None):
    """写 txt/plain/srt/vtt/md（以及可选的 clean.md），返回输出清单与警告。

    给了 source（segments 文件路径）就再写一份 export.source.json：记录路径、sha256、
    导出的 cue 数。字幕自己证明不了出处，这份文件是那条链子。
    """
    segments, invalid = sanitize(segments)

    def write(name, content):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    outputs = {
        "txt": write("transcript.txt", "\n".join(
            "[%6.1f -> %6.1f] %s" % (s["start"], s["end"], s["text"]) for s in segments)),
        "plain": write("transcript.plain.txt", to_plain(segments)),
        "srt": write("transcript.srt", to_srt(segments)),
        "vtt": write("transcript.vtt", to_vtt(segments)),
        "md": write("transcript.md", to_md(segments, meta=meta, corrections=corrections)),
    }
    if clean_md is not None:
        outputs["clean_md"] = write("transcript.clean.md", clean_md)
    if source is not None:
        meta = {"segments": source, "sha256": sha256_file(source), "count": len(segments),
                "artifacts": sorted(os.path.basename(p) for p in outputs.values())}
        outputs["source"] = write("export.source.json",
                                  json.dumps(meta, ensure_ascii=False, indent=1) + chr(10))
    return outputs, {"long_cues": long_cues(segments),
                     "empty_segments": sum(1 for x in invalid if x["reason"] == "empty_text"),
                     "invalid_cues": invalid}