# -*- coding: utf-8 -*-
"""summary.md：抽取式摘要（默认，不调模型）+ 回查 + 可选 LLM 摘要。

回查是硬门：每条要点必须在 segments 里找得到出处（时间差 ≤1s，且归一化后是段文本子串）。
找不到就删掉并计数——宁可摘要短，也不留无法引用的句子。
"""
import json
import math
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import termrules
from .timefmt import mmss as _mmss

# 分钟位允许 >=2 位：超过 99 分钟的视频是 `100:00`；满一小时 timefmt 给 `H:MM:SS`。
# 两种都得认，否则长视频的要点一行都匹配不上，verify() 直接 continue，引用门静默失效。
BULLET = re.compile(r"^- (.+?) \[(\d{1,}):(\d{2})(?::(\d{2}))?\]\s*$")
SPLIT = re.compile(r"[。！？!?；\n]")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]{1,}")

PROMPTS = ("所以", "因此", "但是", "关键", "注意", "第一", "其次", "最后",
           "总结", "结论", "建议", "问题", "原因", "方法", "区别")

# LLM 摘要按字符预算切块：整份逐字稿塞一个 prompt，长视频必然超上下文。
CHUNK_CHARS = 6000
MAX_BULLETS = 10

# LLM 响应上限：正常回复几 KB，8MB 已经宽得离谱；再大就是端点出了问题，别把内存读光。
MAX_BYTES = 8 * 1024 * 1024
# 综述本来就要「一句话」，200 字还装不下说明模型在写小作文，不再是可核对的东西。
OVERVIEW_MAX = 200
LOOPBACK = ("127.0.0.1", "localhost", "::1")


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


def merge_short(segments, min_chars=6, min_sec=1.5):
    """把碎段并成句，只用于挑摘要句子，不改 transcript。

    实测 13:47 的视频平均 1.6 秒/段、9.5 字/段，摘要要点全是「烤烟，烤烟」这种半句。
    合并后 start 取第一段的 time、end 取最后一段的 time：回查按整块窗口判，
    引用落在块的后半截也不会被当成编造删掉。
    """
    out, buf = [], None
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s["start"])
        end = float(s.get("end") or start)
        if buf is None:
            buf = {"start": start, "end": end, "text": text}
        else:
            buf["text"] += text
            buf["end"] = end
        if len(buf["text"]) >= min_chars and (buf["end"] - buf["start"]) >= min_sec:
            out.append(buf)
            buf = None
    if buf is not None:
        if out:  # 尾部碎句并进上一句，避免要点只剩半句
            out[-1]["text"] += buf["text"]
            out[-1]["end"] = buf["end"]
        else:
            out.append(buf)
    return out


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
    """拉丁词与词表命中计数；拉丁词不分大小写，显示最常出现的原始拼写。"""
    counts, spell = {}, {}
    for seg in segments:
        text = seg.get("text") or ""
        for token in LATIN.findall(text):
            key = token.lower()
            counts[key] = counts.get(key, 0) + 1
            spell.setdefault(key, {})
            spell[key][token] = spell[key].get(token, 0) + 1
        for h in termrules.find_hits(text, rules):
            key = h["canonical"].lower()
            counts[key] = counts.get(key, 0) + 1
            spell.setdefault(key, {})
            spell[key][h["canonical"]] = spell[key].get(h["canonical"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for key, n in ranked[:limit]:
        forms = spell.get(key) or {}
        # 并列时取字典序小的拼写，保证两次构建字节一致
        best = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if forms else key
        out.append((best, n))
    return out


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


def _dubious(segments, low, n=5):
    """全局最差 n 段（avg_logprob 最小），取完再按时间排好用于显示。

    以前先取时间序前 5 段再排序，等于只报了「视频开头最差的几句」，
    后半段真正听不清的地方反而进不了摘要。
    """
    picked = [s for i, s in enumerate(segments, 1) if i in low]
    picked.sort(key=lambda s: (s.get("avg_logprob") if s.get("avg_logprob") is not None else 0.0))
    picked = picked[:n]
    picked.sort(key=lambda s: float(s["start"]))
    return picked


def extract(segments, meta, rules):
    """确定性抽取式摘要。返回 markdown 文本（未经回查）。"""
    cands = candidates(merge_short(segments), rules)
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
    # 反过来说，一章都取不到内容时也不能只留个空标题——先把内容攒够再决定发不发标题。
    chapter = []
    if len(buckets) >= 2:
        for b in sorted(buckets):
            top = _pick(buckets[b], used, 2)
            if not top:
                continue
            chapter.append("### %s–%s" % (_mmss(b * 300), _mmss((b + 1) * 300)))
            chapter.extend(_bullets(top))
            chapter.append("")
    if chapter:
        lines.append("## 分章")
        lines.extend(chapter)

    lines.append("## 关键数字")
    nums = _pick([c for c in cands if any(ch.isdigit() for ch in c["text"])], used, 6)
    lines.extend(_bullets(nums) or ["- （没提到数字）"])
    lines.append("")

    lines.append("## 关键词")
    kws = keywords(segments, rules)
    lines.extend(["- %s ×%d" % (k, n) for k, n in kws] or ["- （无）"])
    lines.append("")

    lines.append("## 存疑段")
    dubious = _dubious(segments, low)
    if dubious:
        for s in dubious:
            lines.append("- [%s] %s（avg_logprob %.2f）"
                         % (_mmss(s["start"]), s["text"], s.get("avg_logprob") or 0))
    else:
        lines.append("- 无")
    return "\n".join(lines).rstrip() + "\n"


def find_segment(sec, segments, tol=1.0, text=None):
    """按 [mm:ss] 定位段；合并块按 [start - tol, end + tol] 整段窗口命中。

    三道保险：
    1. 先找「秒数取整后完全相等」的段（`[mm:ss]` 就是 floor(start) 来的，
       分段长 1.3 秒时，前一段起点也可能落在 1 秒内，只按浮点距离会取错段、
       让真引用被当成编造删掉）；
    2. sec 落在段的时间窗口内也算命中：merge_short 把好几段拼成一句之后，
       引用常指向块的后半截，只比 start 会把真引用删光；
    3. 给 text 时优先挑「文本装得下它」的那段，再用距离排序兜底
       （LLM 给的 sec 可能不准，文本对得上才是硬证据）。
    """
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(sec):
        # 模型回 1e999 时 float() 得到的是合法的 inf，int(inf) 却抛 OverflowError，
        # 它不是 ValueError，一路穿透到 CLI 变成 traceback。这里当成「定位不到」。
        return None
    if not segments:
        return None
    want = int(max(0.0, sec))
    near = []
    for s in segments:
        start = float(s["start"])
        end = max(start, float(s.get("end") or start))
        if abs(start - sec) <= tol or start - tol <= sec <= end + tol:
            near.append(s)
    if not near:
        return None
    exact = [s for s in near if int(max(0.0, float(s["start"]))) == want]
    if exact:
        near = exact
    if text:
        key = norm(text)
        hits = [s for s in near if key in norm(s.get("text") or "")]
        if hits:
            near = hits
    near.sort(key=lambda s: abs(float(s["start"]) - sec))
    return near[0]


def bullet_sec(match):
    """`[mm:ss]` / `[H:MM:SS]` 两种时间戳都折成秒。"""
    a, b, c = match.group(2), match.group(3), match.group(4)
    if c is None:
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def verify(md, segments, fallback=None):
    """删掉回查不过的要点。返回 (新文本, 删除条数, 被删条目)。

    segments 应与喂给模型的原文同粒度（build 传 merge_short 的合并块）；
    fallback 再给一份原始段，合并块里定位不到时兜底。
    """
    out, bad = [], []
    for line in md.splitlines():
        m = BULLET.match(line)
        if not m:
            out.append(line)
            continue
        text = m.group(1)
        want = bullet_sec(m)
        seg = find_segment(want, segments, text=text)
        if (seg is None or norm(text) not in norm(seg.get("text") or "")) and fallback:
            seg = find_segment(want, fallback, text=text)
        if seg is None or norm(text) not in norm(seg.get("text") or ""):
            bad.append(line)
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n", len(bad), bad


def _heading_level(line):
    m = re.match(r"^(#{2,6}) ", line)
    return len(m.group(1)) if m else 0


def prune_empty(md):
    """删掉没有任何内容的标题：空的 `## 分章`/`## 要点` 比没有更难看。

    标题只在同一级或更高级标题之前找内容，`### 小标题` 不算 `## 分章` 的内容；
    一轮下来子标题被删，下一轮父标题也就空了，所以要循环到稳定。
    """
    lines = md.splitlines()
    changed = True
    while changed:
        changed = False
        keep, i = [], 0
        while i < len(lines):
            lv = _heading_level(lines[i])
            if lv:
                j = i + 1
                while j < len(lines) and (_heading_level(lines[j]) == 0
                                          or _heading_level(lines[j]) > lv):
                    j += 1
                if not [x for x in lines[i + 1:j] if x.strip()]:
                    changed = True
                    i += 1
                    continue
            keep.append(lines[i])
            i += 1
        lines = keep
    out = []
    for line in "\n".join(lines).rstrip().splitlines():
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


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
        if not isinstance(it, dict):
            continue
        try:
            sec = float(it["sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(sec):
            # `"sec": 1e999` 是合法 JSON，json 会给出 inf；这种坐标不是时间戳，丢掉
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        bullets.append({"text": text, "sec": max(0.0, sec)})
    overview = data.get("overview")
    overview = str(overview).strip() if overview else None
    if not bullets and not overview:
        return None
    return {"overview": overview, "bullets": bullets}


def _origin(url):
    """(scheme, host, port)；解析不出来返回 None。"""
    try:
        parts = urllib.parse.urlsplit(url or "")
        return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port)
    except ValueError:
        return None


def check_url(url):
    """--llm-url 的门：https，或指向本机的 http。

    明文 http 打到别的机器，等于把 Authorization 放在链路上；这种配置要在发请求前
    就拒绝（cli 把 ValueError 映射成退出码 2），而不是发出去再指望对方不记日志。
    """
    origin = _origin(url)
    if origin and origin[1] and (origin[0] == "https" or
                                 (origin[0] == "http" and origin[1] in LOOPBACK)):
        return url
    raise ValueError("--llm-url 必须是 https://，或指向本机的 http://…"
                     "（明文会把 key 暴露在链路上）")


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """只放行同源重定向：换协议/主机/端口就拒。

    默认 handler 转 302 时只剥 Content-Type/Length，Authorization 会原样跟过去；
    一次网关跳转（或 DNS 被劫持的明文 http）就足够把长期 key 交给别人的域名。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = _origin(req.full_url), _origin(newurl)
        if new is None or old != new:
            raise urllib.error.HTTPError(
                newurl, code, "拒绝跨主机重定向（避免把 API key 带到别的域名）", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _llm_ask(chunk, base_url, model, timeout, key=None, warnings=None):
    """对一块逐字稿问一次；返回 (reply, error)，reply 为 None 时 error 说明原因。

    原因要留住：摘要退回抽取式时得能告诉用户到底是没开服务、模型名不对，
    还是服务端自己起不来（Ollama 装坏了就是这个样子）。
    warnings 收的是「不影响结果但值得说一句」的情况（比如 Content-Type 不对）。
    """
    lines = ["[%s] %s" % (_mmss(s["start"]), s["text"]) for s in chunk]
    prompt = (ASK_TEMPLATE % MAX_BULLETS) + "\n".join(lines)
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        # OpenAI / DeepSeek 这类云端模型没有 Authorization 直接 401；本地 Ollama 不校验它
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers=headers)
    opener = urllib.request.build_opener(SameHostRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None, "响应超过 %d MB，已放弃解析（端点可能配错了）" % (MAX_BYTES // (1024 * 1024))
        if ctype and "json" not in ctype and warnings is not None:
            warnings.append("LLM 端点返回的 Content-Type 是 %s（不是 JSON），仍然尝试解析" % ctype)
        payload = json.loads(raw.decode("utf-8", "replace"))
        content = payload["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        # 自己抛的重定向拒绝没有响应体，原因在 reason/msg 里，别把它丢了
        detail = detail or str(getattr(exc, "reason", "") or exc.msg or "")
        return None, ("HTTP %s %s" % (exc.code, detail)).strip()
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])
    reply = _parse_reply(content)
    if reply is None:
        return None, "模型回复里没有可解析的 JSON：%s" % content[:120].replace("\n", " ")
    return reply, None


def llm_bullets(segments, base_url, model, timeout=90, errors=None, key=None,
                warnings=None):
    """分块调 /chat/completions，返回 (bullets, overview, 失败块数, 总块数)。

    单块失败不影响其他块；全部失败返回 (None, None, n, n)，由 build 退回抽取式。
    errors 传一个 list 进来就会收到每次失败的原因，供上层写进 warnings。
    """
    if not base_url:
        return None, None, 0, 0
    check_url(base_url)
    parts = chunks(segments)
    got, overviews, failed = [], [], 0
    for chunk in parts:
        reply, err = _llm_ask(chunk, base_url, model, timeout, key, warnings)
        if reply:
            got.extend(reply["bullets"])
            if reply.get("overview"):
                overviews.append(reply["overview"])
        else:
            failed += 1
            if errors is not None and err:
                errors.append(err)
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


OVERVIEW_TS = re.compile(r"[\[【(（](\d{1,3}):(\d{2})[\]】)）]")


def overview_supported(overview, segments):
    """综述里的 [mm:ss] 必须对得上某一段原文。

    综述是模型改写，逐字回查管不到它；但「在 12:30 提到」这种引证一旦编造，
    读的人没法分辨。所以只留一条硬规矩：时间引用对不上原文，整段综述不要。
    """
    for m in OVERVIEW_TS.finditer(overview):
        sec = int(m.group(1)) * 60 + int(m.group(2))
        if find_segment(sec, segments) is None:
            return False
    return True


def render_llm(bullets, overview, segments, meta, model, warnings=None):
    """LLM 摘要：综述（允许改写、不带时间戳）+ 要点（必须过回查门）。

    两级是刻意的：改写型结论一定不是原文子串，混在同一个格式里只会被回查门删光。
    分开之后，能引用的部分保持可核对，能读的部分不被删。
    综述里对不上原文的 [mm:ss] 会让整段综述被丢掉，warnings 里记一条。
    """
    title = meta.get("title") or "（未命名）"
    duration = meta.get("duration_sec")
    lines = ["# 摘要：%s" % title, ""]
    stamp = _mmss(duration) if duration else "--:--"
    lines.append("> 时长 %s · %d 段 · %d 字 · 生成方式：LLM（%s）"
                 % (stamp, len(segments), sum(len(s["text"]) for s in segments), model or "unknown"))
    lines.append("")
    if overview:
        text = overview.strip()
        if len(text) > OVERVIEW_MAX:
            if warnings is not None:
                warnings.append("LLM 综述 %d 字，超过 %d 字上限，整段已删除"
                                % (len(text), OVERVIEW_MAX))
            text = ""
        elif not overview_supported(text, segments):
            if warnings is not None:
                warnings.append("LLM 综述里的时间引用在原文里找不到，整段已删除")
            text = ""
        if text:
            lines.append("## 一句话")
            lines.append(text)
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
    dubious = _dubious(segments, meta.get("low_conf") or set())
    if dubious:
        lines.append("## 存疑段")
        for s in dubious:
            lines.append("- [%s] %s（avg_logprob %.2f）"
                         % (_mmss(s["start"]), s["text"], s.get("avg_logprob") or 0))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build(segments, meta, mode="extractive", llm_url=None, llm_model=None,
          rules=None, llm_key=None):
    """返回 (markdown, mode, bullets, dropped, warnings)。mode='none' 返回空文本。"""
    if mode == "none":
        return "", "none", 0, 0, []
    rules = rules if rules is not None else termrules.load()
    warnings = []
    md = None
    if mode == "llm":
        errs = []
        items, overview, failed, total = llm_bullets(segments, llm_url, llm_model or "unknown",
                                                     errors=errs, key=llm_key,
                                                     warnings=warnings)
        if items or overview:
            md = render_llm(items or [], overview, segments, meta, llm_model, warnings)
            if failed:
                warnings.append("LLM 摘要 %d/%d 块失败（%s），只用成功块"
                                % (failed, total, errs[0] if errs else "原因未知"))
        else:
            why = errs[0] if errs else "没给 --llm-url"
            warnings.append("LLM 摘要失败（%s），已退回抽取式" % why)
            mode = "extractive"
    if md is None:
        md = extract(segments, meta, rules)
    # 喂给模型的是原始 segments，回查就得对着同粒度的合并块判，再拿原始段兜底
    md, dropped, _bad = verify(md, merge_short(segments), fallback=segments)
    md = prune_empty(md)
    if dropped:
        warnings.append("摘要 %d 条未通过回查，已删除" % dropped)
    bullets = sum(1 for line in md.splitlines() if BULLET.match(line))
    return md, mode, bullets, dropped, warnings
