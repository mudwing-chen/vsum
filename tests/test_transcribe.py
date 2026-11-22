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

w = SimpleNamespace(start=1.3, end=1.8, word=" 文本", probability=0.9)
rec4 = tr.segment_record(seg(words=[w]), words=True)
eq(rec4["words"][0]["word"], " 文本")
eq(rec4["words"][0]["probability"], 0.9)



# load_audio：16k 单声道 PCM wav -> float32；立体声/非 16k 一律返回 None（交给模型自己解）
import tempfile, wave, struct  # noqa: E402

def _wav(path, frames, channels=1, rate=16000):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h', v) for v in frames))

d = tempfile.mkdtemp()
mono = os.path.join(d, 'a.wav'); _wav(mono, [0, 16384, -16384, 32767])
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

