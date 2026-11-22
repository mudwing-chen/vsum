#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""summary.md：打分、回查硬门、确定性、LLM 失败回退。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import summary, termrules  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


RULES = termrules.load()
SEGS = [
    {"start": 0.0, "end": 3.0, "text": "今天我们聊聊 cloud code 这个工具", "avg_logprob": -0.2},
    {"start": 3.0, "end": 6.0, "text": "它叫 Grok，还有 DeepSeek 和 Sonnet", "avg_logprob": -0.9},
    {"start": 6.0, "end": 15.0, "text": "这一段故意写得长一些，用来验证超过七秒的 cue 会被记进 manifest", "avg_logprob": -0.5},
    {"start": 15.0, "end": 18.0, "text": "嗯，最后一句。", "avg_logprob": -0.1},
]
META = {"title": "demo", "duration_sec": 18.0, "low_conf": {2}}

# 打分
check("数字加权", summary.score("这里有 3 个", RULES) - summary.score("这里没有", RULES) == 3)
check("拉丁词加权", summary.score("用 Chrome 打开", RULES) > summary.score("用浏览器打开", RULES))
check("词表加权有上限", summary.score("Claude Code 和 Grok 和 DeepSeek", RULES) <= 3 + 2 + 4 + 1 + 1)
check("长度加分", summary.score("十个字到四十个字之间的一句话", RULES) >= 1)

# 候选切句与过滤
cands = summary.candidates(SEGS, RULES)
texts = [c["text"] for c in cands]
check("短句被丢", "好的。" not in summary.candidates(SEGS + [{"start": 20.0, "text": "好的。"}], RULES)[0]["text"], texts)
check("按分数排序", all(cands[i]["score"] >= cands[i + 1]["score"] for i in range(len(cands) - 1)), cands)
dup = summary.candidates(SEGS + [dict(SEGS[0], start=30.0)], RULES)
check("归一化去重", [c["text"] for c in dup].count("今天我们聊聊 cloud code 这个工具") == 1)

# 回查硬门
good = "- 今天我们聊聊 cloud code 这个工具 [00:00]"
bad_time = "- 今天我们聊聊 cloud code 这个工具 [00:12]"
bad_text = "- 这句话不在原文里 [00:00]"
md = "# 摘要\n\n" + good + "\n" + bad_time + "\n" + bad_text + "\n"
new_md, dropped, bad = summary.verify(md, SEGS)
check("回查保留有出处的", good in new_md)
check("回查删时间错位的", bad_time not in new_md)
check("回查删编造的", bad_text not in new_md)
check("回查计数", dropped == 2 and len(bad) == 2, (dropped, bad))
check("回查幂等", summary.verify(new_md, SEGS)[0] == new_md)

# 相邻段只差 1s 时，按时间取第一个会判错出处（真实 13:47 语料上误删 9 条）
DENSE = [{"start": 741.54, "text": "不烤烟的人"},
         {"start": 742.58, "text": "不知道4.3是多高的分数"}]
dense_md, dense_dropped, _ = summary.verify("- 不知道4.3是多高的分数 [12:22]\n", DENSE)
check("同段窗口按文本取出处", dense_dropped == 0, dense_md)
check("没有文本线索时取最近的", summary.find_segment(742.5, DENSE)["start"] == 742.58)

# 碎段先并成句：13:47 的真实稿平均 1.6 秒/段，要点会变成半句
FRAG = [{"start": 0.0, "end": 1.5, "text": "烤烟"},
        {"start": 1.5, "end": 3.0, "text": "烤烟烤烟烤烟"},
        {"start": 3.0, "end": 9.0, "text": "总分烤了4.3分，不知道4.3是多高的分数"}]
merged = summary.merge_short(FRAG)
check("碎段被并", len(merged) == 2, merged)
check("并段保留首段起time", merged[0]["start"] == 0.0)
check("并段拼接文本", merged[0]["text"] == "烤烟烤烟烤烟烤烟", merged)
frag_md, _, frag_bullets, frag_dropped, _ = summary.build(
    FRAG, {"title": "f", "duration_sec": 9.0, "low_conf": set()}, rules=RULES)
check("碎段上摘要不回查失败", frag_dropped == 0, frag_md)

# build：确定性 + 六节齐全
md1, mode1, bullets1, dropped1, warn1 = summary.build(SEGS, META, rules=RULES)
md2 = summary.build(SEGS, META, rules=RULES)[0]
check("两次构建字节一致", md1 == md2)
check("默认抽取式", mode1 == "extractive", mode1)
check("六节齐全", all(h in md1 for h in ("## 概览", "## 分章", "## 关键数字", "## 关键词", "## 存疑段")), md1)
bullets = [l for l in md1.split("## 关键词")[0].splitlines() if l.startswith("- ") and not l.startswith("- （")]
check("每条要点带时间", all(summary.BULLET.match(l) for l in bullets), md1)
check("存疑段来自 low_conf", "[00:03]" in md1, md1)
check("mode=none 返回空", summary.build(SEGS, META, "none", rules=RULES)[0] == "")

# LLM 失败必须退回抽取式，不能抛
md3, mode3, _, _, warn3 = summary.build(SEGS, META, "llm", llm_url="http://127.0.0.1:9/v1",
                                        llm_model="x", rules=RULES)
check("LLM 失败仍出版", mode3 == "extractive" and md3 == md1, mode3)
check("LLM 失败有 warning", any("退回抽取式" in w for w in warn3), warn3)

# 手工塞坏点必须被 build 删掉
orig = summary.extract
summary.extract = lambda *a, **k: "# 摘要\n\n- 编造的句子 [00:00]\n"
try:
    md4, _, b4, d4, w4 = summary.build(SEGS, META, rules=RULES)
finally:
    summary.extract = orig
check("build 删掉回查不过的", "编造的句子" not in md4 and d4 == 1, (md4, d4))
check("build 记 warning", any("未通过回查" in w for w in w4), w4)

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK summary")