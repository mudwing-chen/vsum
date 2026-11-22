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

# build：确定性 + 小节齐全
md1, mode1, bullets1, dropped1, warn1 = summary.build(SEGS, META, rules=RULES)
md2 = summary.build(SEGS, META, rules=RULES)[0]
check("两次构建字节一致", md1 == md2)
check("默认抽取式", mode1 == "extractive", mode1)
check("必需小节齐全", all(h in md1 for h in ("## 概览", "## 关键数字", "## 关键词", "## 存疑段")), md1)
check("不足两章不出分章", "## 分章" not in md1, md1)
long_meta = {"title": "long", "duration_sec": 900.0, "low_conf": set()}
long_segs = [{"start": float(i * 60), "end": float(i * 60 + 5),
              "text": "第%d分钟里说了一件有数字 2026 的事情" % i, "avg_logprob": -0.2}
             for i in range(15)]
long_md = summary.build(long_segs, long_meta, rules=RULES)[0]
check("跨章时出分章", "## 分章" in long_md and long_md.count("###") >= 2, long_md[:400])
bullets = [l for l in md1.split("## 关键词")[0].splitlines() if l.startswith("- ") and not l.startswith("- （")]
check("每条要点带时间", all(summary.BULLET.match(l) for l in bullets), md1)
check("全局不重复", len(bullets) == len(set(bullets)), bullets)
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

# 长视频：>99 分钟时分钟位是三位，回查门不能因此静默失效
long_segs = [{"start": 6000.0, "end": 6003.0, "text": "一小时四十分钟之后的这一段",
              "avg_logprob": -0.2}]
long_md = "- 一小时四十分钟之后的这一段 [100:00]"
kept, dropped_long, _ = summary.verify(long_md, long_segs)
check("三位分钟的有效引用保留", kept.strip() == long_md and dropped_long == 0, (kept, dropped_long))
fake_md = "- 这句原文里根本没有 [100:00]"
_, dropped_fake, bad_fake = summary.verify(fake_md, long_segs)
check("三位分钟的编造内容仍被删", dropped_fake == 1 and bad_fake == [fake_md], (dropped_fake, bad_fake))
check("mmss 超过 99 分钟不丢分钟位", summary._mmss(6000) == "100:00", summary._mmss(6000))
check("BULLET 认三位分钟", bool(summary.BULLET.match("- x [100:00]")))

# 细分段：前一段起点也落在 1 秒内时，不能取错段把真引用删掉
close_segs = [{"start": 208.44, "end": 209.76, "text": "叫阿拉塞利的女孩", "avg_logprob": -0.1},
              {"start": 209.76, "end": 211.0, "text": "你和她在1940年结婚", "avg_logprob": -0.1}]
kept2, dropped2, bad2 = summary.verify("- 你和她在1940年结婚 [03:29]", close_segs)
check("相邻分段不误删真引用", dropped2 == 0 and "结婚" in kept2, (dropped2, bad2))
_, dropped3, _ = summary.verify("- 编造的一句 [03:29]", close_segs)
check("相邻分段仍删编造", dropped3 == 1)

# LLM 分块：整份逐字稿不能塞一个 prompt
many = [{"start": float(i * 3), "end": float(i * 3 + 3), "text": "第%d段，随便说点什么内容。" % i}
        for i in range(400)]
parts = summary.chunks(many)
check("分块后块数 > 1", len(parts) > 1, len(parts))
check("分块不丢段", sum(len(p) for p in parts) == len(many))
check("分块保持顺序", [s for p in parts for s in p] == many)
check("每块不超预算太多",
      all(sum(len(s["text"]) + 9 for s in p) <= summary.CHUNK_CHARS + 200 for p in parts))
check("空输入返回空块", summary.chunks([]) == [])
check("单段超预算也单独成块",
      len(summary.chunks([{"start": 0.0, "text": "长" * (summary.CHUNK_CHARS * 2)}])) == 1)
check("llm_bullets 无 url 返回三元组", summary.llm_bullets(many, None, "x") == (None, None, 0, 0))

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK summary")
