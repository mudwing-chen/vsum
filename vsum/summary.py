# -*- coding: utf-8 -*-
"""summary.md：抽取式摘要（默认，不调模型）+ 回查 + 可选 LLM 摘要。

回查是硬门：每条要点必须在 segments 里找得到出处（时间差 ≤1s，且归一化后是段文本子串）。
找不到就删掉并计数——宁可摘要短，也不留无法引用的句子。
"""
import json
import re
import unicodedata
import urllib.request

from . import termrules
from .timefmt import mmss as _mmss

# 分钟位允许 >=2 位：超过 99 分钟的视频是 `100:00`，写死两位会让这些行匹配不上，
# verify() 直接 continue，等于长视频的引用门静默失效。
BULLET = re.compile(r"^- (.+?) \[(\d{2,}):(\d{2})\]\s*$")
SPLIT = re.compile(r"[。！？!?；\n]")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}")

PROMPTS = ("所以", "因此", "但是", "关键", "注意", "第一", "其次", "最后",
           "总结", "结论", "建议", "问题", "原因", "方法", "区别")

# LLM 摘要按字符预算切块：整份逐字稿塞一个 prompt，长视频必然超上下文。
CHUNK_CHARS = 6000
MAX_BULLETS = 10


def norm(text):
    """去空白/标点/大小写，用于回查比较。保留文字与数字。"""
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in text if not unicodedata.category(ch)[0] in "PZ").lower()


def score(text, rules):
    s = 0
    if any(ch.isdigit() for ch in text):
        s += 3
    if LATIN.search(text):
        s += 2
    hits = termrules.find_hits(text, rules)
    s += min(4, 2 * len(hits))
    if any(w in text for w in PROMPTS):
        s += 1
    if 10 <= len(text) <= 40:
        s += 1
    return s


def candidates(segments, rules):
    """切句、过滤、去重、打分。返回 [{text,start,score}]，按 (-score, start) 排序。"""
    seen = set()
    out = []
    for seg in segments:
        for raw in SPLIT.split(seg.get("text") or ""):
            text = raw.strip()
            if len(text) < 6 or len(text) > 90:
                continue
            key = norm(text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"text": text, "start": seg["start"], "score": score(text, rules)})
    out.sort(key=lambda c: (-c["score"], c["start"]))
    return out


def _bucket(sec):
    return int(max(0.0, sec) // 300)


def _bullets(items):
    return ["- %s [%s]" % (c["text"], _mmss(c["start"])) for c in items]


def keywords(segments, rules, limit=8):
    counts = {}
    for seg in segments:
        text = seg.get("text") or ""
        for token in LATIN.findall(text):
            counts[token] = counts.get(token, 0) + 1
        for h in termrules.find_hits(text, rules):
            counts[h["canonical"]] = counts.get(h["canonical"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return ranked[:limit]


def extract(segments, meta, rules):
    """确定性抽取式摘要。返回 markdown 文本（未经回查）。"""
    cands = candidates(segments, rules)
    title = meta.get("title") or "（未命名）"
    duration = meta.get("duration_sec")
    low = meta.get("low_conf") or set()

    lines = ["# 摘要：%s" % title, ""]
    stamp = _mmss(duration) if duration else "--:--"
    lines.append("> 时长 %s · %d 段 · %d 字 · 生成方式：抽取式（不调用模型）"
                 % (stamp, len(segments), sum(len(s["text"]) for s in segments)))
    lines.append("")
    lines.append("## 概览")
    lines.extend(_bullets(cands[:3]) or ["- （没有可摘的句子）"])
    lines.append("")

    lines.append("## 分章")
    buckets = {}
    for c in cands:
        buckets.setdefault(_bucket(c["start"]), []).append(c)
    for b in sorted(buckets):
        top = buckets[b][:2]
        lines.append("### %s–%s" % (_mmss(b * 300), _mmss((b + 1) * 300)))
        lines.extend(_bullets(top))
        lines.append("")

    lines.append("## 关键数字")
    nums = [c for c in cands if any(ch.isdigit() for ch in c["text"])][:6]
    lines.extend(_bullets(nums) or ["- （没提到数字）"])
    lines.append("")

    lines.append("## 关键词")
    kws = keywords(segments, rules)
    lines.extend(["- %s ×%d" % (k, n) for k, n in kws] or ["- （无）"])
    lines.append("")

    lines.append("## 存疑段")
    dubious = [s for i, s in enumerate(segments, 1) if i in low][:5]
    dubious.sort(key=lambda s: s.get("avg_logprob") or 0)
    if dubious:
        for s in dubious:
            lines.append("- [%s] %s（avg_logprob %.2f）"
                         % (_mmss(s["start"]), s["text"], s.get("avg_logprob") or 0))
    else:
        lines.append("- 无")
    return "\n".join(lines).rstrip() + "\n"


def find_segment(sec, segments, tol=1.0):
    for s in segments:
        if abs(float(s["start"]) - sec) <= tol:
            return s
    return None


def verify(md, segments):
    """删掉回查不过的要点。返回 (新文本, 删除条数, 被删条目)。"""
    out, bad = [], []
    for line in md.splitlines():
        m = BULLET.match(line)
        if not m:
            out.append(line)
            continue
        text, mm, ss = m.group(1), m.group(2), m.group(3)
        want = int(mm) * 60 + int(ss)
        seg = find_segment(want, segments)
        if seg is None or norm(text) not in norm(seg.get("text") or ""):
            bad.append(line)
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n", len(bad), bad


def chunks(segments, limit=CHUNK_CHARS):
    """按字符预算把段切成若干块，块内顺序不变。

    单段超过预算时也单独成块（截断它等于丢内容，交给模型那边报错更诚实）。
    """
    out, cur, size = [], [], 0
    for seg in segments:
        n = len(seg.get("text") or "") + 9  # "[mm:ss] " 前缀
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += n
    if cur:
        out.append(cur)
    return out


def _llm_ask(chunk, base_url, model, timeout):
    """对一块逐字稿问一次；返回 [{text,sec}]，失败返回 None。"""
    lines = ["[%s] %s" % (_mmss(s["start"]), s["text"]) for s in chunk]
    prompt = ("下面是视频逐字稿，每行以 [mm:ss] 开头。挑最多 %d 句最值得读的，"
              "只返回 JSON 数组，元素形如 {\"text\": \"原句\", \"sec\": 12.3}；"
              "text 必须是某一行的原文片段，sec 是该行时间戳的秒数。不要解释。\n\n"
              % MAX_BULLETS + "\n".join(lines))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        start, end = content.find("["), content.rfind("]")
        items = json.loads(content[start:end + 1])
    except Exception:
        return None
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            out.append({"text": str(it["text"]).strip(), "sec": float(it["sec"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def llm_bullets(segments, base_url, model, timeout=90):
    """分块调 /chat/completions，返回 (bullets, 失败块数, 总块数)。

    单块失败不影响其他块；全部失败返回 (None, n, n)，由 build 退回抽取式。
    """
    if not base_url:
        return None, 0, 0
    parts = chunks(segments)
    got, failed = [], 0
    for chunk in parts:
        items = _llm_ask(chunk, base_url, model, timeout)
        if items:
            got.extend(items)
        else:
            failed += 1
    if not got:
        return None, failed, len(parts)
    seen, merged = set(), []
    for b in sorted(got, key=lambda x: x["sec"]):
        key = norm(b["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(b)
    return merged[:MAX_BULLETS] or None, failed, len(parts)


def render_llm(bullets, segments, meta, model):
    title = meta.get("title") or "（未命名）"
    duration = meta.get("duration_sec")
    lines = ["# 摘要：%s" % title, ""]
    stamp = _mmss(duration) if duration else "--:--"
    lines.append("> 时长 %s · %d 段 · %d 字 · 生成方式：LLM（%s）"
                 % (stamp, len(segments), sum(len(s["text"]) for s in segments), model or "unknown"))
    lines.append("")
    lines.append("## 概览")
    for b in bullets:
        seg = find_segment(b["sec"], segments)
        sec = seg["start"] if seg else b["sec"]
        lines.append("- %s [%s]" % (b["text"], _mmss(sec)))
    return "\n".join(lines).rstrip() + "\n"


def build(segments, meta, mode="extractive", llm_url=None, llm_model=None,
          rules=None):
    """返回 (markdown, mode, bullets, dropped, warnings)。mode='none' 返回空文本。"""
    if mode == "none":
        return "", "none", 0, 0, []
    rules = rules if rules is not None else termrules.load()
    warnings = []
    md = None
    if mode == "llm":
        items, failed, total = llm_bullets(segments, llm_url, llm_model or "unknown")
        if items:
            md = render_llm(items, segments, meta, llm_model)
            if failed:
                warnings.append("LLM 摘要 %d/%d 块失败（上下文过长/没网），只用成功块"
                                % (failed, total))
        else:
            warnings.append("LLM 摘要失败（没网/没模型/返回不合法），已退回抽取式")
            mode = "extractive"
    if md is None:
        md = extract(segments, meta, rules)
    md, dropped, bad = verify(md, segments)
    if dropped:
        warnings.append("摘要 %d 条未通过回查，已删除" % dropped)
    bullets = sum(1 for line in md.splitlines() if BULLET.match(line))
    return md, mode, bullets, dropped, warnings
