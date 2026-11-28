# -*- coding: utf-8 -*-"""segment_record / fallback_count 的单元断言（不需要 faster-whisper 和音频）。"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vsum import transcribe as tr  # noqa: E402

checks = 0


def eq(a, b):
    global checks
    checks += 1
    assert a == b, "%r != %r" % (a, b)


def seg(text=" 文本 ", temp=0.0, words=None):
    return SimpleNamespace(start=1.25, end=3.5, text=text, avg_logprob=-0.5,
                           no_speech_prob=0.01, compression_ratio=1.2,
                           temperature=temp, words=words)


rec = tr.segment_record(seg())
eq(rec["text"], "文本")
eq(rec["start"], 1.25)
eq(rec["temperature"], 0.0)
eq("words" in rec, False)

rec2 = tr.segment_record(seg(temp=0.6))
eq(rec2["temperature"], 0.6)

rec3 = tr.segment_record(seg(temp=None))
eq(rec3["temperature"], None)

eq(tr.fallback_count([rec, rec2, rec3]), 1)
eq(tr.fallback_count([rec, rec3]), 0)
eq(tr.fallback_count([]), 0)

eq(tr.av_incompatible(TypeError("open() got an unexpected keyword argument 'metadata_errors'")), True)
eq(tr.av_incompatible(TypeError("别的错")), False)
eq(tr.av_incompatible(ValueError("metadata_errors")), False)

# 繁转简：确定性，且不改时间戳
simple, ok = tr.to_simplified("讀了九八五，討厭優越")
eq(ok, True)
eq(simple, "读了九八五，讨厌优越")
eq(tr.to_simplified("已经是简体")[0], "已经是简体")

# 上下文默认开；WHISPER_COND=0 可退回。实测开上下文后切句更细（句均 33.8→23.4 字）
args, recorded, _, _ = tr.decode_args("small", "zh", words=False)
eq(args["condition_on_previous_text"], True)
eq(recorded["condition_on_previous_text"], True)
os.environ["WHISPER_COND"] = "0"
try:
    eq(tr.decode_args("small", "zh", words=False)[0]["condition_on_previous_text"], False)
finally:
    del os.environ["WHISPER_COND"]
eq(tr.decode_args("small", "zh", words=False)[0]["condition_on_previous_text"], True)
eq(recorded["t2s"], True)

w = SimpleNamespace(start=1.3, end=1.8, word=" 文本", probability=0.9)
rec4 = tr.segment_record(seg(words=[w]), words=True)
eq(rec4["words"][0]["word"], " 文本")
eq(rec4["words"][0]["probability"], 0.9)



# load_audio：16k 单声道 PCM wav -> float32；立体声/非 16k 一律返回 None（交给模型自己解）
import wave, struct  # noqa: E402

from _tmpdir import mkdtemp as _mkdtemp  # noqa: E402

def _wav(path, frames, channels=1, rate=16000):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h', v) for v in frames))

d = _mkdtemp()
mono = os.path.join(d, 'a.wav'); _wav(mono, [0, 16384, -16384, 32767])
if tr.np is None:            # CI 用 --no-deps 装包时没有 numpy：要么给数组，要么明确回退
    eq(tr.load_audio(mono), None)
else:
    arr = tr.load_audio(mono)
    eq(arr is not None, True)
    eq(len(arr), 4)
    eq(round(float(arr[1]), 4), 0.5)
stereo = os.path.join(d, 'b.wav'); _wav(stereo, [0, 0, 1, 1], channels=2)
eq(tr.load_audio(stereo), None)
resampled = os.path.join(d, 'c.wav'); _wav(resampled, [0, 1], rate=44100)
eq(tr.load_audio(resampled), None)
eq(tr.load_audio(os.path.join(d, 'nope.wav')), None)


# --- 转写失败路径：迭代期异常、空结果、CPU 回退也加载失败，都要是 vsum 的错误类型 ---
import tempfile  # noqa: E402


def _fake_model(yields=(), boom=None):
    class Model(object):
        def transcribe(self, payload, **kwargs):
            def gen():
                for item in yields:
                    yield item
                if boom is not None:
                    raise boom
            return gen(), SimpleNamespace(language="zh", duration=3.0)
    return Model()


def _transcribe_error(model_impl, hw=None):
    """跑一次 transcribe_audio，返回 (异常类型名, 异常文本, 输出目录)。"""
    out = tempfile.mkdtemp(prefix="vs-tr-")
    old_get, old_resolve = tr.get_model, tr.hardware.resolve
    tr.get_model = model_impl
    if hw is not None:
        tr.hardware.resolve = lambda: hw
    try:
        tr.transcribe_audio(os.path.join(out, "nope.wav"), out, model_name="tiny")
    except BaseException as exc:
        return type(exc).__name__, str(exc), out
    finally:
        tr.get_model, tr.hardware.resolve = old_get, old_resolve
    return None, "", out


name, text, out = _transcribe_error(
    lambda *a, **k: (_fake_model([seg()], boom=RuntimeError("CUDA out of memory")), False))
eq(name, "TranscribeError")                       # 迭代里炸掉的异常不能穿透到 CLI
eq("转写失败" in text, True)
eq(os.path.exists(os.path.join(out, "transcript.txt")), False)

name, text, out = _transcribe_error(lambda *a, **k: (_fake_model([]), False))
eq(name, "TranscribeError")                       # 0 段算失败，不写空产物
eq("没有识别到任何语音" in text, True)
eq(os.path.exists(os.path.join(out, "segments.json")), False)

CUDA_HW = {"device": "cuda", "compute_type": "float16", "source": "gpu",
           "name": "RTX 4090", "model_cap": "large-v3", "cc": 8.9, "vram_gb": 24.0,
           "reason": "测试用"}
calls = []


def _cuda_then_fail(model_name, device, compute, cache=True):
    calls.append(device)
    raise RuntimeError("Could not load libcudart.so" if device == "cuda" else "cant load")


name, text, out = _transcribe_error(_cuda_then_fail, hw=CUDA_HW)
eq(name, "DependencyError")                       # CPU 回退也加载失败：说清楚，不裸抛
eq("CPU 也加载失败" in text, True)
eq(calls, ["cuda", "cpu"])


# --- 提示词按语言选：中文提示词喂给英文音频会把输出往中文上带 ---
eq(tr.infer_kwargs("zh")["initial_prompt"], tr.DEFAULT_PROMPT)
en_prompt = tr.infer_kwargs("en")["initial_prompt"]
eq("English" in en_prompt, True)
eq(any("\u4e00" <= ch <= "\u9fff" for ch in en_prompt), False)   # 英文音频不掺中文提示
eq(tr.infer_kwargs("auto")["initial_prompt"], "")                  # 不知道语言就别硬塞偏置
eq(tr.infer_kwargs("auto")["language"], None)                       # faster-whisper 不认 "auto"，None 才是自动检测
eq(tr.infer_kwargs("zh")["language"], "zh")
eq(tr.infer_kwargs("en")["initial_prompt"] != tr.infer_kwargs("zh")["initial_prompt"], True)
eq(tr.config_digest("en") != tr.config_digest("zh"), True)         # 指纹跟着变，换语言必然重跑
_old_prompt = os.environ.get("WHISPER_PROMPT")
os.environ["WHISPER_PROMPT"] = "自定义提示"
try:
    eq(tr.infer_kwargs("en")["initial_prompt"], "自定义提示")      # 显式设置全局生效
    eq(tr.infer_kwargs("auto")["initial_prompt"], "自定义提示")
finally:
    if _old_prompt is None:
        os.environ.pop("WHISPER_PROMPT", None)
    else:
        os.environ["WHISPER_PROMPT"] = _old_prompt


# --- --lang auto：请求值进 decode（复用要比它），检测值单独记一份 ---
_auto_out = tempfile.mkdtemp(prefix="vs-auto-")
_auto_wav = os.path.join(_auto_out, "a.wav")
_wav(_auto_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_load = tr.get_model, tr.load_audio
tr.get_model = lambda *a, **k: (_fake_model([seg()]), False)
tr.load_audio = lambda path: None      # 让 payload 走路径分支，不依赖 numpy
try:
    _segs, _info, _rec = tr.transcribe_audio(_auto_wav, _auto_out, model_name="tiny", language="auto")
finally:
    tr.get_model, tr.load_audio = _old_get, _old_load
eq(len(_segs), 1)
eq(_rec.get("detected_language"), "zh")     # 实际跑出来的语言
eq(_rec.get("language"), "auto")            # 请求值保留，复用判断要靠它


# --- --model auto / batch-size auto / OOM 阶梯 / 批推理回退 ---
_CPU_HW = {"device": "cpu", "compute_type": "int8", "source": "cpu", "name": None,
           "model_cap": "medium", "cc": None, "vram_gb": None, "reason": ""}
_GPU_HW = {"device": "cuda", "compute_type": "float16", "source": "gpu", "name": "RTX",
           "model_cap": "large-v3", "cc": 8.9, "vram_gb": 24.0, "reason": ""}
_old_hw = tr.hardware.resolve
try:
    tr.hardware.resolve = lambda: _CPU_HW
    eq(tr.resolve_model("auto"), "small")                  # CPU 不跟 model_cap 的 medium
    tr.hardware.resolve = lambda: _GPU_HW
    eq(tr.resolve_model("auto"), "large-v3")               # CUDA 取显存上限
    eq(tr.resolve_model("medium"), "medium")               # 非 auto 原样返回
    eq(tr.resolve_batch_size("auto", "cuda", {"vram_gb": 24}), 16)
    eq(tr.resolve_batch_size("auto", "cuda", {"vram_gb": 8}), 8)
    eq(tr.resolve_batch_size("auto", "cuda", {"vram_gb": 4}), 4)
    eq(tr.resolve_batch_size("auto", "cpu", {}), 1)        # CPU 禁用批推理
    eq(tr.resolve_batch_size(8, "cpu", {}), 1)
finally:
    tr.hardware.resolve = _old_hw
eq(tr.resolve_model("auto") in ("small", "medium", "large-v3"), True)  # 真实硬件上也必须落在档位内)

# 降档顺序：batch → compute → 模型 → CPU；到底就返回 None
eq(tr._next_attempt(("large-v3", "cuda", "float16", 12)),
   (("large-v3", "cuda", "float16", 6), "batch_size 12->6"))
eq(tr._next_attempt(("large-v3", "cuda", "float16", 8)),
   (("large-v3", "cuda", "float16", 4), "batch_size 8->4"))
eq(tr._next_attempt(("large-v3", "cuda", "float16", 1))[0],
   ("large-v3", "cuda", "int8_float16", 1))
eq(tr._next_attempt(("large-v3", "cuda", "int8_float16", 1))[0],
   ("large-v3", "cuda", "int8", 1))
eq(tr._next_attempt(("large-v3", "cuda", "int8", 1))[0],
   ("medium", "cuda", "int8_float16", 1))
eq(tr._next_attempt(("tiny", "cpu", "int8", 1)), (None, None))

# 指纹：默认与旧版一致；批大小/VAD 一旦变了必须重跑
_base = tr.config_digest("zh")
eq(_base, tr.config_digest("zh", batch_size=1))
eq(_base != tr.config_digest("zh", batch_size=4), True)
eq(_base != tr.config_digest("zh", vad_options={"threshold": 0.7}), True)
eq(tr.infer_kwargs("zh", batch_size=4)["condition_on_previous_text"], False)  # 批推理没有上文
eq(tr.infer_kwargs("zh", vad_options={"threshold": 0.7})["vad_parameters"]["threshold"], 0.7)
eq("vad_parameters" in tr.infer_kwargs("zh"), False)   # 默认不传，老产物指纹不变

# OOM 阶梯：连续两次 OOM 后 batch 8→4→2 成功，记录两次降档
import types as _types
_oom_mod = _types.ModuleType("faster_whisper")

class _BatchBoom(object):
    calls = 0

    def __init__(self, model):
        pass

    def transcribe(self, payload, batch_size=None, **kwargs):
        _BatchBoom.calls += 1
        boom = _BatchBoom.calls <= 2

        def gen():
            if boom:
                raise RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")
            yield seg()
        return gen(), SimpleNamespace(language="zh", duration=3.0)

_oom_mod.BatchedInferencePipeline = _BatchBoom
_old_mod0 = sys.modules.get("faster_whisper")
sys.modules["faster_whisper"] = _oom_mod
_oom_out = tempfile.mkdtemp(prefix="vs-oom-")
_oom_wav = os.path.join(_oom_out, "a.wav")
_wav(_oom_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_hw2 = tr.get_model, tr.hardware.resolve
tr.get_model = lambda *a, **k: (_fake_model([seg()]), False)
tr.hardware.resolve = lambda: _GPU_HW
try:
    _segs, _info, _rec = tr.transcribe_audio(_oom_wav, _oom_out, model_name="small",
                                             language="zh", batch_size=8)
finally:
    tr.get_model, tr.hardware.resolve = _old_get, _old_hw2
    if _old_mod0 is None:
        sys.modules.pop("faster_whisper", None)
    else:
        sys.modules["faster_whisper"] = _old_mod0
eq(len(_segs), 1)
eq(_BatchBoom.calls, 3)
eq(_rec["batch_size"], 2)
eq(len(_rec.get("fallbacks") or []), 2)
eq(_rec["fallbacks"][0]["to"], "small/cuda/batch4")
eq(_rec["fallbacks"][1]["to"], "small/cuda/batch2")

# 批推理 API 不兼容：降级串行 + 记 batch_fallback，而不是报错
import types as _types
_fake_fw = _types.ModuleType("faster_whisper")

class _BadBatch(object):
    def __init__(self, model):
        pass

    def transcribe(self, payload, batch_size=None, **kwargs):
        raise TypeError("transcribe() got an unexpected keyword argument 'initial_prompt'")

_fake_fw.BatchedInferencePipeline = _BadBatch
_old_mod = sys.modules.get("faster_whisper")
sys.modules["faster_whisper"] = _fake_fw
_fb_out = tempfile.mkdtemp(prefix="vs-fb-")
_fb_wav = os.path.join(_fb_out, "a.wav")
_wav(_fb_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_hw3 = tr.get_model, tr.hardware.resolve
tr.get_model = lambda *a, **k: (_fake_model([seg()]), False)
tr.hardware.resolve = lambda: _GPU_HW
try:
    _segs, _info, _rec = tr.transcribe_audio(_fb_wav, _fb_out, model_name="small",
                                             language="zh", batch_size=4)
finally:
    tr.get_model, tr.hardware.resolve = _old_get, _old_hw3
    if _old_mod is None:
        sys.modules.pop("faster_whisper", None)
    else:
        sys.modules["faster_whisper"] = _old_mod
eq(len(_segs), 1)
eq(_rec["batch_size"], 1)
eq("initial_prompt" in _rec.get("batch_fallback", ""), True)


# --batch-size 0 不是 auto，是错误：0 在 GPU 上会静默变成 4/16，用户很难发现
try:
    tr.resolve_batch_size(0, "cuda", {"vram_gb": 24})
    eq("batch 0 应当报错", False)
except ValueError as exc:
    eq("batch-size 必须 >= 1" in str(exc), True)

# 加载期 OOM：小显存选大模型时先降模型，不要误判成「CUDA 装坏了」直接退 CPU
def _load_oom(model_name, device, compute, cache=True):
    if device == "cuda" and model_name == "large-v3":
        raise RuntimeError("CUDA out of memory. Tried to allocate 3 GiB")
    return _fake_model([seg()]), False

_lo_out = tempfile.mkdtemp(prefix="vs-lo-")
_lo_wav = os.path.join(_lo_out, "a.wav")
_wav(_lo_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_hw4 = tr.get_model, tr.hardware.resolve
tr.get_model = _load_oom
tr.hardware.resolve = lambda: CUDA_HW
try:
    _segs, _info, _rec = tr.transcribe_audio(_lo_wav, _lo_out, model_name="large-v3")
finally:
    tr.get_model, tr.hardware.resolve = _old_get, _old_hw4
eq(_rec["model"], "medium")
eq(_rec["fallbacks"][0]["reason"], "oom_load")
eq(_rec["fallbacks"][0]["to"], "medium/cuda")

# OOM 之后缓存里那个失败的模型要被丢掉，否则它继续占显存，降档反而更容易再 OOM
_seed = ("small", "cuda", "float16")
tr._MODELS[_seed] = object()
_ev_out = tempfile.mkdtemp(prefix="vs-ev-")
_ev_wav = os.path.join(_ev_out, "a.wav")
_wav(_ev_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_hw5 = tr.get_model, tr.hardware.resolve
tr.get_model = lambda *a, **k: (_fake_model([seg()], boom=RuntimeError("CUDA out of memory")), False)
tr.hardware.resolve = lambda: CUDA_HW
try:
    try:
        tr.transcribe_audio(_ev_wav, _ev_out, model_name="small")
    except Exception:
        pass
finally:
    tr.get_model, tr.hardware.resolve = _old_get, _old_hw5
eq(_seed in tr._MODELS, False)

# 终端 LOW_CONF 的 n 与 result.counts.low_conf 必须同源：25 段、值全不同 → 2 条
import contextlib  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402
from vsum import export as export_mod  # noqa: E402


def _low_seg(i):
    return SimpleNamespace(start=i * 4.0, end=i * 4.0 + 3.0, text="第%d段" % i,
                           avg_logprob=-0.25 - 0.01 * i, no_speech_prob=0.01,
                           compression_ratio=1.2, temperature=0.0, words=None)


_low_out = tempfile.mkdtemp(prefix="vs-low-")
_low_wav = os.path.join(_low_out, "a.wav")
_wav(_low_wav, [0, 16384, -16384, 32767] * 4000)
_old_get, _old_load = tr.get_model, tr.load_audio
tr.get_model = lambda *a, **k: (_fake_model([_low_seg(i) for i in range(25)]), False)
tr.load_audio = lambda path: None
_buf = io.StringIO()
try:
    with contextlib.redirect_stdout(_buf):
        _low_segs, _low_info, _low_rec = tr.transcribe_audio(_low_wav, _low_out, model_name="tiny")
finally:
    tr.get_model, tr.load_audio = _old_get, _old_load
_log = _buf.getvalue()
_m = re.search(r"LOW_CONF n=(\d+) times=", _log)
eq(bool(_m), True)
# 同一批 segments，同一个函数：终端报的条数就是 result.counts.low_conf
eq(int(_m.group(1)), len(export_mod.low_conf_indexes(_low_segs)))
eq(int(_m.group(1)), 2)

print("OK %d checks" % checks)

