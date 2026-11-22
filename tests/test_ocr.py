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
check("中文字差", ocr.char_diff("甚至希特勒都对你大有加奖", "甚至希特勒都对你大有嘉奖") == [("加", "嘉")],
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

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK ocr")
