# -*- coding: utf-8 -*-
"""抽帧 OCR 的纯函数：挑帧、相似度、候选生成、报告。不装 OCR 引擎也能跑。"""
import io
import os
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vsum import ocr  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


# latin_tokens
check("抽拉丁词并小写", ocr.latin_tokens("Hello World 2026") == ["hello", "world"],
      ocr.latin_tokens("Hello World 2026"))
check("丢常见词", "the" not in ocr.latin_tokens("the Claude Code"))
check("保留点与连字符", ocr.latin_tokens("CLAUDE.md") == ["claude.md"], ocr.latin_tokens("CLAUDE.md"))
check("太短的不算", ocr.latin_tokens("ab cd abc") == ["abc"], ocr.latin_tokens("ab cd abc"))

# ratio
check("相同为 1", ocr.ratio("claude", "claude") == 1.0)
check("对称", abs(ocr.ratio("claude", "cloud") - ocr.ratio("cloud", "claude")) < 1e-9)
check("差得远则低", ocr.ratio("claude", "javascript") < 0.5)

# suggest：画面上的拼写 vs 转写里的词
vocab = {"cloud": 3, "gpd": 1, "astra": 2}
got = ocr.suggest("Claude 5.2 和 GPT 6.2", vocab)
pairs = {(c["surface"], c["canonical"]) for c in got}
check("Cloud -> Claude", ("cloud", "Claude") in pairs, got)
check("GPD -> GPT（三字母也认）", ("gpd", "GPT") in pairs, got)
check("同音通道标出来", any(c["why"] == "sound" for c in got), got)
check("转写里已有的拼写不报", ocr.suggest("Astra 是旗舰", vocab) == [], ocr.suggest("Astra 是旗舰", vocab))
check("常见词不当候选", ocr.suggest("Code and Video", {"clod": 1}) == [],
      ocr.suggest("Code and Video", {"clod": 1}))
check("差得远的词不硬猜", ocr.suggest("JavaScript", {"claude": 1}) == [],
      ocr.suggest("JavaScript", {"claude": 1}))
check("候选带相似度和通道",
      all(0.6 <= c["ratio"] <= 1.0 and c["why"] in ("shape", "sound") for c in got), got)

# pick_times
segs = [{"start": 0.0, "end": 2.0, "text": "开场"},
        {"start": 10.0, "end": 12.0, "text": "这里提到 Claude 和 GPT"},
        {"start": 300.0, "end": 302.0, "text": "结尾"}]
times = ocr.pick_times(segs, low_conf={1}, limit=10)
check("低置信段被选", 0.0 in times, times)
check("含拉丁词的段被选", 10.0 in times, times)
check("结果有序", times == sorted(times), times)
check("1 秒内不重复", all(abs(a - b) > 1.0 for i, a in enumerate(times) for b in times[i + 1:]),
      times)
check("显式时间优先", ocr.pick_times(segs, times=[5.0], limit=10)[0] == 5.0,
      ocr.pick_times(segs, times=[5.0]))
sampled = ocr.pick_times(segs, every=100, limit=10)
check("均匀采样覆盖尾部", 300.0 in sampled, sampled)
check("limit 生效", len(ocr.pick_times(segs, every=1, limit=5)) == 5,
      ocr.pick_times(segs, every=1, limit=5))

# 报告
doc = {"engine": "rapidocr", "frames": 2, "candidates": [
    {"surface": "cloud", "canonical": "Claude", "ratio": 0.833, "hits": 2, "times": [10.0, 12.0]}],
    "ocr_pairs": [{"sec": 10.0, "frame": "t0010.0.jpg", "ocr": "Claude 5.2", "score": 0.91,
                   "transcript": "cloud 5.2", "match_sec": 10.0, "similarity": 0.9, "suggestions": []}]}
md = ocr._report(doc)
check("报告有候选表", "| cloud | Claude |" in md, md[:200])
check("报告有逐帧对照", "Claude 5.2" in md and "cloud 5.2" in md, md)
check("报告给出下一步命令", "vsum fix" in md, md)

# 字幕和语音不是严格对齐的：要在时间窗里找最像的那段，而不是「包含这一秒」的那段
window = [{"start": 0.0, "end": 2.5, "text": "甚至希特勒都对你大有加奖"},
          {"start": 2.5, "end": 5.0, "text": "但他们都是你编出来的人呐"}]
seg, r = ocr.best_match("甚至希特勒都对你大有加奖", window, 0.4)
check("窗口内匹配到最像的段", seg is not None and abs(r - 1.0) < 1e-9, (seg, r))
seg2, r2 = ocr.best_match("但他们都是你编出来的人呐", window, 0.4)   # 画面领先一句
check("跨段也能对上（字幕常领先/滞后）", seg2 is not None and seg2["start"] == 2.5, (seg2, r2))
seg3, r3 = ocr.best_match("完全无关的一句话在这里", window, 0.4)
check("不像就不硬配", seg3 is None or r3 < 0.6, (seg3, r3))

# 逐字差异：同音字替换要能指出来
check("中文字差带上下文", ocr.char_diff("甚至希特勒都对你大有加奖", "甚至希特勒都对你大有嘉奖") == [("有加奖", "有嘉奖")],
      ocr.char_diff("甚至希特勒都对你大有加奖", "甚至希特勒都对你大有嘉奖"))
check("完全一样没有差", ocr.char_diff("一觉醒来", "一觉醒来") == [])
check("差太多不列", ocr.char_diff("军事王牌中的王牌", "甚至希特勒都对你大有嘉奖") == [])

# 没有 OCR 引擎时要明确报错，而不是静默返回空
_engine = ocr._engine
_frames = ocr.extract_frames
ocr.extract_frames = lambda video, times, out, quality=3: [(t, "fake.jpg") for t in times]
ocr._engine = lambda: (_ for _ in ()).throw(RuntimeError("没有 OCR 引擎：pip install 'vsum[ocr]'"))
try:
    import json
    from _tmpdir import mkdtemp as _mkdtemp
    d = _mkdtemp()
    with open(os.path.join(d, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": segs}, fh, ensure_ascii=False)
    with open(os.path.join(d, "video.mp4"), "wb") as fh:
        fh.write(b"x")
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ocr.run(d, limit=1)
        check("缺引擎要抛错", False)
    except RuntimeError as exc:
        check("缺引擎报错带安装提示", "vsum[ocr]" in str(exc), str(exc))
finally:
    ocr._engine = _engine
    ocr.extract_frames = _frames

# OCR 候选接回 fix：两帧都看到 + 读音像，才算 high（可自动改）；只一帧的仍是 medium
import json as _json  # noqa: E402
from vsum import fix as fix_mod  # noqa: E402

d2 = _mkdtemp()
ocr_json = os.path.join(d2, "ocr.json")
with open(ocr_json, "w", encoding="utf-8") as fh:
    _json.dump({"candidates": [
        {"surface": "gpd", "canonical": "GPT", "ratio": 0.67, "hits": 2, "why": "sound"},
        {"surface": "clod", "canonical": "Claude", "ratio": 0.8, "hits": 1, "why": "shape"},
        {"surface": "cloud", "canonical": "Claude", "ratio": 0.83, "hits": 1, "why": "shape"},
    ]}, fh, ensure_ascii=False)
rules = fix_mod.rules_from_ocr(ocr_json)
got = {(r["surface"], r["confidence"]) for r in rules}
check("两帧的读音匹配 -> high", ("gpd", "high") in got, got)
check("只一帧 -> medium", ("clod", "medium") in got, got)

with open(os.path.join(d2, "transcript.txt"), "w", encoding="utf-8") as fh:
    fh.write("[   0.0 ->    3.0] GPD 6.2 花了 209 美元\n")
fix_mod.main([d2, "--from-ocr", ocr_json, "--apply"])
fixed = os.path.join(d2, "transcript.fixed.txt")
check("--from-ocr 把高置信候选写进 fixed", os.path.exists(fixed), fixed)
if os.path.exists(fixed):
    body = open(fixed, encoding="utf-8").read()
    check("替换成了画面上的写法", "GPT 6.2" in body, body)
    check("原词没留下", "GPD" not in body, body)

# 中文候选：逐字差异聚合；单字不采用（带上上下文之后就 >=2 字了）
pairs = [{"sec": 3.6, "similarity": 0.92, "diffs": [("有加奖", "有嘉奖")]},
         {"sec": 12.0, "similarity": 0.88, "diffs": [("罗大", "罗那"), ("大", "那")]}]
cjk = ocr.cjk_candidates(pairs)
by_key = {(c["surface"], c["canonical"]): c for c in cjk}
check("中文候选被聚合", ("有加奖", "有嘉奖") in by_key, cjk)
check("多字差异可采用", by_key[("有加奖", "有嘉奖")]["applicable"] is True, by_key)
check("单字差异只参考", by_key[("大", "那")]["applicable"] is False, by_key)
check("中文候选标了类型", all(c["kind"] == "cjk" for c in cjk), cjk)
agg2 = ocr.cjk_candidates([{"sec": 1.0, "similarity": 0.9, "diffs": [("罗大", "罗那")]},
                           {"sec": 2.0, "similarity": 0.9, "diffs": [("罗大", "罗那")]}])
check("同一处两帧 -> hits=2", agg2[0]["hits"] == 2 and agg2[0]["times"] == [1.0, 2.0], agg2)

# 中文候选进 fix：两帧一致才 high；单字和 applicable=False 一律跳过
d3 = _mkdtemp()
ocr_json2 = os.path.join(d3, "ocr.json")
with open(ocr_json2, "w", encoding="utf-8") as fh:
    _json.dump({"candidates": [
        {"surface": "罗大", "canonical": "罗那", "hits": 2, "ratio": 0.88, "why": "char",
         "kind": "cjk", "applicable": True},
        {"surface": "有加奖", "canonical": "有嘉奖", "hits": 1, "ratio": 0.92, "why": "char",
         "kind": "cjk", "applicable": True},
        {"surface": "大", "canonical": "那", "hits": 5, "ratio": 0.9, "why": "char",
         "kind": "cjk", "applicable": False},
    ]}, fh, ensure_ascii=False)
zh = {(r["surface"], r["confidence"]) for r in fix_mod.rules_from_ocr(ocr_json2)}
check("中文两帧 -> high", ("罗大", "high") in zh, zh)
check("中文一帧 -> medium", ("有加奖", "medium") in zh, zh)
check("单字被丢弃", all(s != "大" for s, _ in zh), zh)

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK ocr")
