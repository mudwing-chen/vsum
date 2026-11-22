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


print("OK %d checks" % checks)

