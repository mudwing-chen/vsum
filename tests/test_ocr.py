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

# 报错类型要能被 cli.py 接住：DependencyError -> 退出码 3，InputError -> 2。
# 裸的 RuntimeError 会一路冒到顶变成 traceback + 退出码 1。
import builtins  # noqa: E402
import json  # noqa: E402
import types  # noqa: E402

from _tmpdir import mkdtemp as _mkdtemp  # noqa: E402
from vsum import pipeline as pipeline_mod  # noqa: E402
from vsum import transcribe as transcribe_mod  # noqa: E402

check("ocr.DependencyError 就是 transcribe 的那个",
      ocr.DependencyError is transcribe_mod.DependencyError)
check("ocr.InputError 就是 pipeline 的那个", ocr.InputError is pipeline_mod.InputError)
check("两者都是 RuntimeError 子类（老代码还能捕到）",
      issubclass(ocr.DependencyError, RuntimeError) and issubclass(ocr.InputError, RuntimeError))

# 没装 ffmpeg：DependencyError，不是 RuntimeError
# 二进制查找走 vsum.toolpath：Windows 上 shutil.which 会把当前目录前置，
# 别人给的目录里放个 ffmpeg.EXE 就能抢先执行
_which = ocr.which
try:
    ocr.which = lambda name: None
    try:
        ocr.extract_frames("/nope/video.mp4", [0.0], _mkdtemp())
        check("没 ffmpeg 要抛 DependencyError", False)
    except ocr.DependencyError as exc:
        check("没 ffmpeg 要抛 DependencyError", "ffmpeg" in str(exc), str(exc))
    except RuntimeError as exc:
        check("没 ffmpeg 要抛 DependencyError", False, "抛的是 %s：%s" % (type(exc).__name__, exc))
finally:
    ocr.which = _which

# 没装 OCR 引擎：DependencyError，提示里带安装命令（import 拦掉，不依赖真环境）
_real_import = builtins.__import__


def _no_rapidocr(name, *args, **kwargs):
    if name.split(".")[0] == "rapidocr_onnxruntime":
        raise ImportError("No module named 'rapidocr_onnxruntime'")
    return _real_import(name, *args, **kwargs)


try:
    builtins.__import__ = _no_rapidocr
    try:
        ocr._engine()
        check("缺引擎要抛 DependencyError", False)
    except ocr.DependencyError as exc:
        check("缺引擎要抛 DependencyError", "vsum[ocr]" in str(exc), str(exc))
    except RuntimeError as exc:
        check("缺引擎要抛 DependencyError", False, "抛的是 %s：%s" % (type(exc).__name__, exc))
finally:
    builtins.__import__ = _real_import

# run() 里几处报错：一整帧都抽不出来 -> InputError
_engine = ocr._engine
_frames = ocr.extract_frames
ocr.extract_frames = lambda video, times, out, quality=3: [(t, "fake.jpg") for t in times]
ocr._engine = lambda: (_ for _ in ()).throw(
    ocr.DependencyError("没有 OCR 引擎：pip install 'vsum[ocr]'"))
try:
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
    except ocr.DependencyError as exc:
        check("缺引擎报错带安装提示", "vsum[ocr]" in str(exc), str(exc))
    except RuntimeError as exc:
        check("缺引擎报错带安装提示", False, "抛的是 %s：%s" % (type(exc).__name__, exc))

    ocr.extract_frames = lambda video, times, out, quality=3: []
    try:
        with redirect_stdout(io.StringIO()):
            ocr.run(d, limit=1)
        check("抽不出帧要抛 InputError", False)
    except ocr.InputError as exc:
        check("抽不出帧要抛 InputError", "一帧" in str(exc), str(exc))
    except RuntimeError as exc:
        check("抽不出帧要抛 InputError", False, "抛的是 %s：%s" % (type(exc).__name__, exc))
finally:
    ocr._engine = _engine
    ocr.extract_frames = _frames

# run() 成功路径：抽帧和识别都用假的，验的是「写盘 + 对照表」这一段
_d2 = _mkdtemp()
with open(os.path.join(_d2, "segments.json"), "w", encoding="utf-8") as fh:
    json.dump({"segments": [
        {"start": 0.0, "end": 3.0, "text": "今天我们聊聊 cloud code 这个工具", "avg_logprob": -0.9},
        {"start": 30.0, "end": 33.0, "text": "它叫 Grok", "avg_logprob": -0.2},
    ]}, fh, ensure_ascii=False)
with open(os.path.join(_d2, "video.mp4"), "wb") as fh:
    fh.write(b"x")
_eng, _fr = ocr._engine, ocr.extract_frames
ocr._engine = lambda: (lambda frame: ("Cloud Code", 0.9), "fake-engine")
ocr.extract_frames = lambda video, times, out, quality=3: [
    (t, os.path.join(out, "f%03d.jpg" % i)) for i, t in enumerate(times)]
try:
    with redirect_stdout(io.StringIO()):
        _doc = ocr.run(_d2)
    check("run 成功路径写出 ocr.json", os.path.exists(os.path.join(_d2, "ocr.json")))
    check("run 成功路径写出 ocr.md", os.path.exists(os.path.join(_d2, "ocr.md")))
    check("run 记录对照数", len(_doc["ocr_pairs"]) == _doc["frames"] and _doc["frames"] > 0,
          (_doc["frames"], len(_doc["ocr_pairs"])))
    _md = open(os.path.join(_d2, "ocr.md"), encoding="utf-8").read()
    check("ocr.md 里有画面文本", "Cloud Code" in _md, _md[:200])
finally:
    ocr._engine, ocr.extract_frames = _eng, _fr

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

# 只差标点/大小写的差异不是错字。
# 真实跑一遍 60 秒视频时，四条候选全是这种噪音，把真候选淹了：
# 「l。」→「l」「源sk」→「源Sk」「近,你」→「近，你」「频。」→「频」
noise = [{"sec": 2.0, "similarity": 0.9,
          "diffs": [("l。", "l"), ("源sk", "源Sk"), ("近,你", "近，你"), ("加奖", "嘉奖")]}]
kept = ocr.cjk_candidates(noise)
check("标点/大小写差异被过滤", [c["surface"] for c in kept] == ["加奖"], kept)

# 同长度的近邻拉丁词：Wipe / Vibe 这种形状(0.5)、读音(Soundex W100/V100)都够不到的
near = ocr.near_tokens("话题 # 站热门 Vibe 知识大赏", {"wipe": 1})
check("近邻拉丁词进候选", bool(near) and near[0]["surface"] == "wipe", near)
check("近邻候选标成 near", near and near[0]["why"] == "near", near)
check("近邻候选只报不改", ocr.near_tokens("Vibe", {"vibe": 1}) == [], "转写里已有的拼写不该报")

# 一帧里同时有标题卡和字幕时，逐行比也能把字幕那一行配上
segs_line = [{"start": 0.0, "end": 2.0, "text": "最近,你应该很难躲开这种视频。"}]
seg_line, r_line = ocr.best_match("# 站热门 知识大赏 Vibe\n最近，你应该很难躲开这种视频", segs_line, 1.0)
check("整帧不成句时逐行也能配上", seg_line is not None and r_line > 0.8, r_line)
check("逐行匹配取最像的那一行",
      ocr.best_line("标题卡\n最近，你应该很难躲开这种视频", "最近,你应该很难躲开这种视频")
      == "最近，你应该很难躲开这种视频")

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK ocr")
