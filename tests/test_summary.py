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
kept1, dropped1, bad1 = summary.verify(md, SEGS)
check("真引用保留", good in kept1, kept1)
check("假时间/假文本被删", dropped1 == 2 and bad1 == [bad_time, bad_text], bad1)
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

# 合并块：引用落在块的后半截时，只比 [start±1s] 会把真引用当编造删掉
# （LLM 给的 sec 是那一行原文的时间，合并后常在块的中后段）
LATE = [{"start": 0.0, "end": 1.6, "text": "烤烟"},
        {"start": 1.6, "end": 3.2, "text": "烤烟烤烟烤烟"},
        {"start": 3.2, "end": 5.0, "text": "总分烤了4.3分"},
        {"start": 5.0, "end": 9.0, "text": "不知道4.3是多高的分数"}]
late_blocks = summary.merge_short(LATE)
check("并段带块尾", late_blocks[-1].get("end") == 9.0, late_blocks)
kept_late, dropped_late, _ = summary.verify("- 不知道4.3是多高的分数 [00:08]\n", late_blocks)
check("块内靠后引用保留", dropped_late == 0, (kept_late, dropped_late))
# 合并块池子里定位不到时，还要能对回原始段兜底
blocks = [{"start": 0.0, "end": 2.0, "text": "开场白"}]
raw = blocks + [{"start": 300.0, "end": 303.0, "text": "后面的原文在这里"}]
kept_fb, dropped_fb, _ = summary.verify("- 后面的原文在这里 [05:00]\n", blocks, fallback=raw)
check("原始段兜底可用", dropped_fb == 0, (kept_fb, dropped_fb))

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

# 存疑段取全局最差 5 段：以前先按时间取前 5 段再排序，视频后半段最差的进不了摘要
LOW = [{"start": float(i * 10), "end": float(i * 10 + 3), "text": "第%d段内容" % i,
        "avg_logprob": lp}
       for i, lp in enumerate([-0.1, -0.15, -0.2, -0.25, -0.3, -2.0, -1.5])]
low_md = summary.extract(LOW, {"title": "low", "duration_sec": 70.0,
                               "low_conf": set(range(1, 8))}, RULES)
dub = low_md.split("## 存疑段")[1]
check("存疑段取全局最差",
      "第5段内容" in dub and "第6段内容" in dub
      and "第0段内容" not in dub and "第1段内容" not in dub, dub)
check("存疑段按时间排列", dub.index("第2段内容") < dub.index("第6段内容"), dub)

# 分章一章内容都取不到时，不能只留个空标题
THIN = [{"start": 0.0, "end": 6.0,
         "text": "第一句有数字 2026 的话。第二句也有数字 2027 但很短。", "avg_logprob": -0.2},
        {"start": 400.0, "end": 406.0, "text": "后面一段也提了一句 2028 的事情。",
         "avg_logprob": -0.2}]
thin_md = summary.build(THIN, {"title": "thin", "duration_sec": 406.0,
                               "low_conf": set()}, rules=RULES)[0]
check("空分章不出版", "## 分章" not in thin_md, thin_md)

# 关键词不分大小写：Fable / fable / FABLE 是同一个词，显示最常出现的原始拼写
KW = [{"start": 0.0, "end": 3.0, "text": "Fable 是这本书的名字，Fable 又出现一次"},
      {"start": 3.0, "end": 6.0, "text": "fable 小写一次"},
      {"start": 6.0, "end": 9.0, "text": "还有 FABLE 全大写一次"}]
kw = summary.keywords(KW, RULES)
flat = dict((k.lower(), n) for k, n in kw)
check("拉丁词不分大小写计数", flat.get("fable") == 4, kw)
check("关键词显示最常出现的拼写", "Fable" in [k for k, _ in kw], kw)
check("关键词按次数排序", all(kw[i][1] >= kw[i + 1][1] for i in range(len(kw) - 1)), kw)

# 综述是模型改写，不受逐字回查管；但夹带的 [mm:ss] 必须对得上原文，对不上就整段不要
good_ov = summary.render_llm([], "结论在 [00:03] 处出现", SEGS, META, "m")
check("综述里对得上的时间引用保留", "## 一句话" in good_ov and "结论在" in good_ov, good_ov)
ov_warn = []
bad_ov = summary.render_llm([], "结论在 [59:59] 处出现", SEGS, META, "m", ov_warn)
check("综述里对不上的时间引用整段删掉",
      "## 一句话" not in bad_ov and "结论在" not in bad_ov, bad_ov)
check("综述被删有 warning", any("综述" in w for w in ov_warn), ov_warn)

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

# build 的回查池是喂给模型的合并块：块后半截的引用不能因为窗口不够被删（A 的核心）
summary.extract = lambda *a, **k: "# 摘要\n\n## 要点\n- 不知道4.3是多高的分数 [00:08]\n"
try:
    late_md, _, _, late_dropped, _ = summary.build(
        LATE, {"title": "l", "duration_sec": 9.0, "low_conf": set()}, rules=RULES)
finally:
    summary.extract = orig
check("build 接受合并块内的靠后引用",
      late_dropped == 0 and "不知道4.3是多高的分数" in late_md, (late_md, late_dropped))

# 要点全被删掉后不能只剩一个空标题
summary.extract = lambda *a, **k: "# 摘要\n\n## 要点\n- 编造的句子 [00:00]\n"
try:
    empty_md = summary.build(SEGS, META, rules=RULES)[0]
finally:
    summary.extract = orig
check("空要点标题不出版", "## 要点" not in empty_md and "编造的句子" not in empty_md, empty_md)

# 长视频：>99 分钟时分钟位是三位（`100:00`），满一小时 timefmt 给 `1:40:00`；
# 两种时间戳回查门都要认，否则这些行匹配不上，引用门等于静默失效
long_segs = [{"start": 6000.0, "end": 6003.0, "text": "一小时四十分钟之后的这一段",
              "avg_logprob": -0.2}]
for stamp in ("[100:00]", "[1:40:00]"):
    long_md = "- 一小时四十分钟之后的这一段 %s" % stamp
    kept, dropped_long, _ = summary.verify(long_md, long_segs)
    check("长视频有效引用保留 %s" % stamp,
          kept.strip() == long_md and dropped_long == 0, (kept, dropped_long))
    fake_md = "- 这句原文里根本没有 %s" % stamp
    _, dropped_fake, bad_fake = summary.verify(fake_md, long_segs)
    check("长视频编造内容仍被删 %s" % stamp,
          dropped_fake == 1 and bad_fake == [fake_md], (dropped_fake, bad_fake))
check("mmss 超过 99 分钟不丢分钟位", summary._mmss(6000) == "1:40:00", summary._mmss(6000))
check("BULLET 认三位分钟", bool(summary.BULLET.match("- x [100:00]")))
check("BULLET 认小时段", bool(summary.BULLET.match("- x [1:40:00]")))

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
