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


def _pick(pool, used, n):
    """从池子里取还没用过的前 n 条，取到就记进 used（全局去重用）。

    以前三个 section 各自取 top-N，同一条候选会被塞进「概览」「分章」「关键数字」
    各一次——4 分钟视频 23 条里 5 条是重复。这条线是摘要"看起来很水"的主因。
    """
    out = []
    for c in pool:
        if len(out) >= n:
            break
        key = norm(c["text"])
        if not key or key in used:
            continue
        used.add(key)
        out.append(c)
    return out


def extract(segments, meta, rules):
    """确定性抽取式摘要。返回 markdown 文本（未经回查）。"""
    cands = candidates(segments, rules)
    title = meta.get("title") or "（未命名）"
    duration = meta.get("duration_sec")
    low = meta.get("low_conf") or set()
    used = set()

    lines = ["# 摘要：%s" % title, ""]
    stamp = _mmss(duration) if duration else "--:--"
    lines.append("> 时长 %s · %d 段 · %d 字 · 生成方式：抽取式（不调用模型）"
                 % (stamp, len(segments), sum(len(s["text"]) for s in segments)))
    lines.append("> 每条要点都回查过原文；摘要是草稿，结论以 transcript.md 为准。")
    lines.append("")
    lines.append("## 概览")
    lines.extend(_bullets(_pick(cands, used, 3)) or ["- （没有可摘的句子）"])
    lines.append("")

    buckets = {}
    for c in cands:
        buckets.setdefault(_bucket(c["start"]), []).append(c)
    # 不足两章就不出这一节：4 分钟的视频只有一个 00:00–05:00，内容必然和概览重复。
    if len(buckets) >= 2:
        lines.append("## 分章")
        for b in sorted(buckets):
            top = _pick(buckets[b], used, 2)
            if not top:
                continue
            lines.append("### %s–%s" % (_mmss(b * 300), _mmss((b + 1) * 300)))
            lines.extend(_bullets(top))
            lines.append("")

    lines.append("## 关键数字")
    nums = _pick([c for c in cands if any(ch.isdigit() for ch in c["text"])], used, 6)
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


ASK_TEMPLATE = (
    "下面是视频逐字稿，每行以 [mm:ss] 开头。只返回一个 JSON 对象，形如\n"
    "{\"overview\": \"不超过 120 字的一句话综述\", "
    "\"bullets\": [{\"text\": \"原句片段\", \"sec\": 12.3}]}\n"
    "要求：\n"
    "1. overview 用你自己的话概括全片论点，不要照抄原句，不要编造原句里没有的数字；\n"
    "2. bullets 最多 %d 条，text 必须是上面某一行的原文片段（会被逐字回查，改了就会被删）；\n"
    "3. sec 是该行时间戳的秒数。不要解释，不要 markdown 代码块。\n\n"
)


def _parse_reply(content):
    """从模型回复里抠出 {"overview", "bullets"}；抠不出来返回 None。"""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(content[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    bullets = []
    for it in data.get("bullets") or []:
        try:
            bullets.append({"text": str(it["text"]).strip(), "sec": float(it["sec"])})
        except (KeyError, TypeError, ValueError):
            continue
    overview = data.get("overview")
    overview = str(overview).strip() if overview else None
    if not bullets and not overview:
        return None
    return {"overview": overview, "bullets": bullets}


def _llm_ask(chunk, base_url, model, timeout):
    """对一块逐字稿问一次；返回 {overview, bullets}，失败返回 None。"""
    lines = ["[%s] %s" % (_mmss(s["start"]), s["text"]) for s in chunk]
    prompt = (ASK_TEMPLATE % MAX_BULLETS) + "\n".join(lines)
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except Exception:
        return None
    return _parse_reply(content)


def llm_bullets(segments, base_url, model, timeout=90):
    """分块调 /chat/completions，返回 (bullets, overview, 失败块数, 总块数)。

    单块失败不影响其他块；全部失败返回 (None, None, n, n)，由 build 退回抽取式。
    """
    if not base_url:
        return None, None, 0, 0
    parts = chunks(segments)
    got, overviews, failed = [], [], 0
    for chunk in parts:
        reply = _llm_ask(chunk, base_url, model, timeout)
        if reply:
            got.extend(reply["bullets"])
            if reply.get("overview"):
                overviews.append(reply["overview"])
        else:
            failed += 1
    if not got and not overviews:
        return None, None, failed, len(parts)
    seen, merged = set(), []
    for b in sorted(got, key=lambda x: x["sec"]):
        key = norm(b["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(b)
    overview = " ".join(overviews) if overviews else None
    return (merged[:MAX_BULLETS] or None), overview, failed, len(parts)


def render_llm(bullets, overview, segments, meta, model):
    """LLM 摘要：综述（允许改写、不带时间戳）+ 要点（必须过回查门）。

    两级是刻意的：改写型结论一定不是原文子串，混在同一个格式里只会被回查门删光。
    分开之后，能引用的部分保持可核对，能读的部分不被删。
    """
    title = meta.get("title") or "（未命名）"
    duration = meta.get("duration_sec")
    lines = ["# 摘要：%s" % title, ""]
    stamp = _mmss(duration) if duration else "--:--"
    lines.append("> 时长 %s · %d 段 · %d 字 · 生成方式：LLM（%s）"
                 % (stamp, len(segments), sum(len(s["text"]) for s in segments), model or "unknown"))
    lines.append("")
    if overview:
        lines.append("## 一句话")
        lines.append(overview)
        lines.append("")
        lines.append("> 本段是模型改写，未经逐字校验；引用请以「要点」和 transcript.md 为准。")
        lines.append("")
    if bullets:
        lines.append("## 要点（每条都能回查到原文）")
        for b in bullets:
            seg = find_segment(b["sec"], segments)
            sec = seg["start"] if seg else b["sec"]
            lines.append("- %s [%s]" % (b["text"], _mmss(sec)))
        lines.append("")
    low = meta.get("low_conf") or set()
    dubious = [s for i, s in enumerate(segments, 1) if i in low]
    if dubious:
        dubious.sort(key=lambda s: s.get("avg_logprob") or 0)
        lines.append("## 存疑段")
        for s in dubious[:5]:
            lines.append("- [%s] %s（avg_logprob %.2f）"
                         % (_mmss(s["start"]), s["text"], s.get("avg_logprob") or 0))
        lines.append("")
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
        items, overview, failed, total = llm_bullets(segments, llm_url, llm_model or "unknown")
        if items or overview:
            md = render_llm(items or [], overview, segments, meta, llm_model)
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
