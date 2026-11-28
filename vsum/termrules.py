# -*- coding: utf-8 -*-
"""词表加载与匹配（audit / fix / mine 共用这一份，避免两处规则漂移）。

置信度只有一档去路：high 可以被 fix 自动写盘，medium 默认只报不改
（要改必须显式 apply_medium）。escalate_if 那套「同行出现 code 就升级」已删除：
它等于拿「同一行有 code」当「这里说的是 Claude」，fixture 里就误改过。

匹配规则：
- 大小写不敏感，但必须是独立词：前后不能是字母、数字或下划线
- 在原文上匹配，只对 surface 开 (?i:)，边界仍按 ASCII 判；
  旧版先 text.lower() 再报偏移，遇到 lower() 变长的字符（İ）偏移会漂
- 命中片段与 canonical 完全相同就跳过：修过的 CLAUDE.md 仍会被 claude.md
  大小写不敏感地匹配到，跳过它才保得住两次 apply 输出一致
- 同一行内最长优先、不重叠
"""
import json
import os
import re

DEFAULT_TERMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "terms.json")


CJK = re.compile(r"[\u3400-\u9fff]")

def load(path=None):
    """返回 rules，已按 surface 长度降序排好。"""
    path = path or DEFAULT_TERMS
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = []
    for term in data.get("terms", []):
        for v in term.get("variants", []):
            rules.append({
                "canonical": term["canonical"],
                "surface": v["surface"].lower(),
                "confidence": v.get("confidence", "high"),
            })
    for r in rules:
        # 中文没有词边界，单字 variant 会在任意合成词里命中（"云" 命中 "云盘"），
        # 那不是修正而是污染；两字以上 + canonical 足够独特才靠得住。
        if CJK.search(r["surface"]) and len(r["surface"]) < 2:
            raise ValueError("中文 variant 至少两个字：%r（canonical %s）"
                             % (r["surface"], r["canonical"]))
    rules.sort(key=lambda r: (-len(r["surface"]), r["surface"]))
    return rules


def find_hits(text, rules):
    """返回不重叠的命中列表，按 start 升序。"""
    cands = []
    for r in rules:
        pattern = r"(?<![A-Za-z0-9_])(?i:" + re.escape(r["surface"]) + r")(?![A-Za-z0-9_])"
        for m in re.finditer(pattern, text):
            if m.group(0) == r["canonical"]:
                continue
            cands.append((m.start(), m.end(), r, r["confidence"]))

    # 最长优先：等长时再按出现位置（等长重叠是任意选择，不是语义判断）
    cands.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
    taken = []
    out = []
    for start, end, rule, conf in cands:
        if any(start < te and ts < end for ts, te in taken):
            continue
        taken.append((start, end))
        out.append({
            "start": start,
            "end": end,
            "surface": rule["surface"],
            "canonical": rule["canonical"],
            "confidence": conf,
        })
    out.sort(key=lambda h: h["start"])
    return out


def apply_hits(text, hits):
    parts = []
    pos = 0
    for h in hits:
        parts.append(text[pos:h["start"]])
        parts.append(h["canonical"])
        pos = h["end"]
    parts.append(text[pos:])
    return "".join(parts)
