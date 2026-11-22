# -*- coding: utf-8 -*-
"""抽帧 + OCR：把「画面上的拼写」取回来，给专名纠错提供证据。

为什么需要它：语音识别对专有名词几乎必错，而且**换大模型也不解决**（实测 small 和
medium、CPU 和 GPU，`Cloud`/`GPD` 的错法完全一致）。而正确写法通常就印在画面上——
字幕、标题卡、输入框、安装输出。所以这里做两件事：

1. 挑最值得看的帧（低置信段 + 含可疑拉丁词 + 均匀采样），抽出来 OCR；
2. 把 OCR 结果和转写对照：
   - 拉丁词：画面上出现一个「转写里从没出现过的拼写」，且与某个转写词很像 → 候选修正
   - 中文：不自动猜，只把「这一段画面上写的是什么」列出来给人看

OCR 引擎是可选依赖（`pip install 'vsum[ocr]'`），没装就明确报错，不影响其他功能。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from . import phonetics
from .timefmt import mmss

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

# 这些词在画面上高频出现但不是专名，别拿它们当候选
STOP = set("""
the and for you are but not with this that have has had was were will would can could
app apps code data file files name type user users team tool tools model models agent
skill video videos audio voice image images text word words open close start stop new old
good best fast slow high low small big make take get give use work run build test error
issue bug fix line point case part side system service server client version update install
setup set config default option value result step way thing time day year month week hour
minute second pro max mini ultra plus lite air note play store shop
png jpg jpeg gif mp4 mp3 http https www com cn html css js py
""".split())


def latin_tokens(text):
    """抽出长度 >=3 的拉丁词，小写去重，丢掉常见词。"""
    out = []
    for tok in TOKEN.findall(text or ""):
        low = tok.strip("._-").lower()
        if len(low) < 3 or low in STOP:
            continue
        out.append(low)
    return out


def ratio(a, b):
    """两串的相似度 0..1（不依赖 difflib 的 autojunk 行为）。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / float(max(la, lb))


def char_diff(a, b, max_ops=4):
    """对齐两段文字，返回差异片段 [(转写片段, 画面片段)]。

    返回的是**带一个字符上下文**的片段。中文错法多是同音单字（加/嘉、大/那），
    只给单字没法安全替换——「大」几乎每句都有；带上前后各一个字（「有加奖」/「有嘉奖」、
    「罗大」/「罗那」）就变成只在那个词里生效的替换。
    """
    import difflib

    if not a or not b:
        return []
    ops = []
    last_end = 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 - i1 > 6 or j2 - j1 > 6:
            continue
        left = 1 if (i1 > max(last_end, 0) and j1 > 0) else 0
        ai, bj = i1 - left, j1 - left
        a_frag = a[max(0, ai):min(len(a), i2 + 1)]
        b_frag = b[max(0, bj):min(len(b), j2 + 1)]
        if a_frag and b_frag and a_frag != b_frag:
            ops.append((a_frag, b_frag))
        last_end = i2 + 1
        if len(ops) >= max_ops:
            break
    return ops


def best_match(ocr_text, segments, sec, before=3.0, after=8.0):
    """画面上的这一行，最像哪一段转写？

    字幕和语音不是严格对齐的：画面常常比这句话早一点或晚一点出现（实测差一整句）。
    所以不能只取「包含这一秒」的那段，要在时间窗里找最像的那段。
    返回 (段, 相似度)；找不到够像的返回 (None, 0)。
    """
    best, best_r = None, 0.0
    for s in segments:
        start = float(s.get("start") or 0.0)
        if start < sec - before or start > sec + after:
            continue
        text = s.get("text") or ""
        if not text:
            continue
        r = ratio(ocr_text, text)
        if r > best_r:
            best, best_r = s, r
    return best, best_r


def cjk_candidates(pairs):
    """把逐字差异聚成中文候选：{转写里的写法 -> 画面上的写法}。

    中文的错法通常是同音字（加奖/嘉奖、巴塞罗大/巴塞罗那），整体句子相似度很高，
    所以用逐字对齐而不是整句相似度。单字差异（「大」→「那」）只展示不采用：
    一个字的替换太容易误伤（「大」几乎每句都有），要改必须至少两个字。
    """
    agg = {}
    for p in pairs:
        for a, b in p.get("diffs") or []:
            a = (a or "").strip()
            b = (b or "").strip()
            if not a or not b or a == b or len(a) > 3 or len(b) > 3:
                continue
            key = (a, b)
            cur = agg.get(key)
            if cur:
                cur["hits"] += 1
                cur["times"].append(p["sec"])
            else:
                agg[key] = {"surface": a, "canonical": b, "hits": 1,
                            "times": [p["sec"]], "ratio": round(p.get("similarity") or 0, 3),
                            "why": "char", "kind": "cjk", "applicable": len(a) >= 2}
    return list(agg.values())


def suggest(frame_text, transcript_vocab, threshold=0.8, phonetic_floor=0.6):
    """画面上的拉丁词 -> 转写里的可疑写法。

    transcript_vocab: {小写词: 出现次数}

    两条通道都要，因为语音错误基本是**同音替换**，编辑距离常常够不到：
    - 形状像：相似度 >= threshold
    - 读音像：Soundex 骨架相同，且相似度 >= phonetic_floor
      （Cloud / Claude 相似度只有 0.67，但骨架都是 C430，正是最该改的那种）

    返回 [{"canonical": 画面上的写法, "surface": 转写里的写法, "ratio": 0.86, "why": "shape|sound"}]
    """
    seen = set()
    out = []
    for tok in TOKEN.findall(frame_text or ""):
        clean = tok.strip("._-")
        low = clean.lower()
        if len(low) < 3 or low in STOP or low in seen:
            continue
        seen.add(low)
        if low in transcript_vocab:          # 转写本来就是这个拼写，没什么可改
            continue
        key = phonetics.soundex(low)
        best, best_r, best_why = None, 0.0, None
        for surface in transcript_vocab:
            if surface == low or len(surface) < 3:
                continue
            r = ratio(low, surface)
            same_sound = bool(key) and key == phonetics.soundex(surface)
            if r >= threshold:
                why = "shape"
            elif same_sound and r >= phonetic_floor:
                why = "sound"
            else:
                continue
            if r > best_r:
                best, best_r, best_why = surface, r, why
        if best:
            out.append({"canonical": clean, "surface": best,
                        "ratio": round(best_r, 3), "why": best_why})
    return out


def pick_times(segments, low_conf=None, every=None, times=None, limit=24):
    """挑要抽帧的秒数：显式 > 低置信段 > 含拉丁词段 > 均匀采样；去重、限量。"""
    picks = []

    def add(t):
        t = round(float(t), 2)
        if all(abs(t - p) > 1.0 for p in picks):
            picks.append(t)

    for t in (times or []):
        add(t)
    if every:
        end = max((float(s.get("end") or 0) for s in segments), default=0.0)
        t = 0.0
        while t <= end and len(picks) < limit:
            add(t)
            t += float(every)
    low_conf = low_conf or set()
    for i, s in enumerate(segments, 1):
        if i in low_conf and len(picks) < limit:
            add(s["start"])
    for s in segments:
        if len(picks) >= limit:
            break
        if latin_tokens(s.get("text") or ""):
            add(s["start"])
    return sorted(picks)[:limit]


def extract_frames(video, times, out_dir, quality=3):
    """按秒抽帧，返回 [(秒, 路径)]。失败的时间点直接跳过，不中断。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("没找到 ffmpeg")
    frame_dir = os.path.join(out_dir, "frames")
    os.makedirs(frame_dir, exist_ok=True)
    made = []
    for t in times:
        path = os.path.join(frame_dir, "t%06.1f.jpg" % t)
        if not os.path.exists(path):
            proc = subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", "%.2f" % t, "-i", video,
                                   "-frames:v", "1", "-q:v", str(quality), path],
                                  capture_output=True, text=True)
            if proc.returncode != 0 or not os.path.exists(path):
                continue
        made.append((t, path))
    return made


def _engine():
    """加载 OCR 引擎（可选依赖）。返回 (callable(path)->(文本, 置信度), 名字)。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "没有 OCR 引擎：pip install 'vsum[ocr]'（rapidocr-onnxruntime）。"
            "装不上也没关系，vsum 的其他功能不依赖它。原始错误：%s" % exc)
    ocr = RapidOCR()

    def read(path):
        result, _ = ocr(path)
        if not result:
            return "", 0.0
        texts = [r[1] for r in result]
        scores = [float(r[2]) for r in result]
        return "\n".join(texts), sum(scores) / len(scores)

    return read, "rapidocr"


def run(path, out_dir=None, every=None, times=None, limit=24, quiet=False):
    """对某个输出目录跑抽帧 OCR。返回结果字典。"""
    from . import pipeline, transcribe

    seg_path = os.path.join(path, "segments.json")
    if not os.path.exists(seg_path):
        raise FileNotFoundError("没有 segments.json：%s" % seg_path)
    with open(seg_path, encoding="utf-8") as fh:
        segments = json.load(fh).get("segments") or []
    if not segments:
        raise ValueError("segments.json 是空的")

    man = {}
    man_path = os.path.join(path, "manifest.json")
    if os.path.exists(man_path):
        with open(man_path, encoding="utf-8") as fh:
            man = json.load(fh)
    video = (man.get("input") or {}).get("path")
    if not video or not os.path.exists(video):
        for name in ("video.mp4", "audio.wav"):
            cand = os.path.join(path, name)
            if os.path.exists(cand):
                video = cand
                break
    if not video:
        raise FileNotFoundError("找不到原视频（manifest.input.path / video.mp4 / audio.wav 都没有）")

    out_dir = out_dir or path
    os.makedirs(out_dir, exist_ok=True)

    from . import export as export_mod
    low_conf = export_mod.low_conf_indexes(segments)
    picks = pick_times(segments, low_conf, every=every, times=times, limit=limit)
    if not picks:
        raise ValueError("没有可抽的时间点")
    if not quiet:
        print("抽 %d 帧（低置信 + 含拉丁词 + 采样）" % len(picks))

    frames = extract_frames(video, picks, out_dir)
    if not frames:
        raise RuntimeError("一帧都没抽出来（ffmpeg 失败或时间点越界）")

    read, engine = _engine()
    vocab = {}
    for s in segments:
        for tok in latin_tokens(s.get("text") or ""):
            vocab[tok] = vocab.get(tok, 0) + 1

    pairs = []
    for t, frame in frames:
        text, score = read(frame)
        if not text:
            continue
        plain = " ".join(text.split())
        seg, r = best_match(plain, segments, t)
        if seg is not None and r < 0.6:
            seg = None
        tr_text = (seg or {}).get("text") or ""
        pairs.append({
            "sec": t,
            "frame": os.path.basename(frame),
            "ocr": plain,
            "score": round(score, 3),
            "match_sec": float(seg["start"]) if seg else None,
            "similarity": round(r, 3) if seg else 0.0,
            "transcript": tr_text,
            "diffs": char_diff(tr_text, plain) if (seg and r >= 0.75) else [],
            "suggestions": suggest(plain, vocab),
        })

    cands = {}
    for p in pairs:
        if not p["transcript"]:
            continue  # 画面和转写对不上时间，不当证据
        for c in p["suggestions"]:
            key = (c["surface"], c["canonical"].lower())
            item = cands.get(key)
            if item:
                item["hits"] += 1
                item["times"].append(p["sec"])
            else:
                item.update({"hits": 1, "times": [p["sec"]], "ratio": c["ratio"],
                             "why": c.get("why"), "kind": "latin", "applicable": True})

    # 中文：从逐字差异聚合出来，和英文候选并列
    for c in cjk_candidates(pairs):
        key = (c["surface"], c["canonical"])
        item = cands.get(key)
        if item:
            item["hits"] += c["hits"]
            item["times"].extend(c["times"])
        else:
            cands[key] = c

    doc = {
        "schema": 1,
        "engine": engine,
        "video": os.path.abspath(video),
        "frames": len(frames),
        "ocr_pairs": pairs,
        "candidates": sorted(cands.values(),
                             key=lambda c: (not c.get("applicable", True), -c["hits"], c["surface"])),
    }
    json_path = os.path.join(out_dir, "ocr.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    md_path = os.path.join(out_dir, "ocr.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_report(doc))
    if not quiet:
        print("画面与转写对照 %d 处，候选修正 %d 条 -> %s"
              % (len(pairs), len(doc["candidates"]), json_path))
        print("对照表 -> %s" % md_path)
    return doc


def _report(doc):
    lines = ["# 抽帧 OCR 对照\n",
             "- 引擎：%s" % doc["engine"],
             "- 帧数：%d" % doc["frames"],
             "- 候选修正：%d" % len(doc["candidates"]), ""]
    if doc["candidates"]:
        lines += ["## 一、可能该改的拼写\n",
                  "| 转写里的写法 | 画面上的写法 | 类型 | 相似度 | 出现 | 时间 |",
                  "|---|---|---|---|---|---|"]
        for c in doc["candidates"]:
            times = ", ".join(mmss(t) for t in c["times"][:3])
            kind = "英文" if c.get("kind", "latin") == "latin" else "中文"
            if c.get("applicable") is False:
                kind += "（单字，仅参考）"
            lines.append("| %s | %s | %s | %.2f | %d | %s |"
                         % (c["surface"], c["canonical"], kind, c["ratio"], c["hits"], times))
        lines.append("")
        lines.append("> 这些只是候选。`vsum fix out/ --from-ocr out/ocr.json` 会把它们并进本次修正："
                     "同一处在 ≥2 帧都成立才自动改，单次出现的只报不改。")
        lines.append("")
    lines += ["## 二、逐帧对照（画面上的拼写是权威的）\n"]
    for p in doc["ocr_pairs"]:
        head = "### %s  `%s`（OCR 置信 %.2f）" % (mmss(p["sec"]), p["frame"], p["score"])
        lines.append(head)
        lines.append("")
        if p.get("match_sec") is not None and p.get("similarity", 0) >= 0.6:
            lines.append("- 对应转写 [%s]（相似 %.2f）：%s"
                         % (mmss(p["match_sec"]), p.get("similarity", 0), p["transcript"]))
        else:
            lines.append("- 对应转写：没找到够像的一段（画面可能是标题卡/信息卡）")
        lines.append("- 画面：%s" % p["ocr"].replace("\n", " / "))
        if p.get("diffs"):
            lines.append("- 逐字差异：%s" % "；".join(
                "「%s」→「%s」" % (a or "∅", b or "∅") for a, b in p["diffs"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vsum ocr",
                                 description="抽帧 OCR：用画面上的拼写核对转写里的专名")
    ap.add_argument("path", help="vsum 的输出目录（要有 segments.json）")
    ap.add_argument("-o", "--out", default=None, help="产物目录，默认与输入相同")
    ap.add_argument("--every", type=float, default=None, help="均匀采样间隔（秒）")
    ap.add_argument("--times", default="", help="指定时间点，逗号分隔（秒）")
    ap.add_argument("--limit", type=int, default=24, help="最多抽几帧，默认 24")
    ap.add_argument("--json", action="store_true", help="stdout 只打一行 ocr.json")
    args = ap.parse_args(argv)
    times = [float(x) for x in re.split(r"[,\s]+", args.times) if x.strip()] if args.times else None

    doc = run(args.path, args.out, args.every, times, args.limit, quiet=args.json)
    if args.json:
        print(json.dumps(doc, ensure_ascii=False))
    return 0
