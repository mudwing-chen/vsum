# -*- coding: utf-8 -*-
"""vsum bench：在本机真实音频上量 RTF，比较 compute_type。

不做「显存 → 模型」的静态承诺：同一张卡在不同驱动/模型上差别很大，
这张表是给你自己机器出数的。没音频时 macOS 可以用 --synth 合成一段。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import hardware

SYNTH_TEXT = "这是一段用来测试转写速度的中文样例。我们关心的是每秒音频要花多少秒转写。"


class BenchError(RuntimeError):
    """音频解码失败等。CLI 映射成退出码 4。"""


def synth_audio(seconds=30, text=None, out_path=None):
    """macOS 用 say 合成；返回 (路径, 是否临时)。其它平台直接抛 InputError。"""
    if sys.platform != "darwin" or not shutil.which("say"):
        from .pipeline import InputError

        raise InputError("--synth 只在 macOS 上可用；其它平台请用 --audio 指定音频")
    text = text or SYNTH_TEXT
    path = out_path or os.path.join(os.environ.get("TMPDIR") or tempfile.gettempdir(),
                                     "vsum-bench.aiff")
    rate = 180  # 中文默认语速；秒数只用来提示，不强行凑
    try:
        subprocess.run(["say", "-r", str(rate), "-o", path, text], check=True,
                       capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise BenchError("say 合成失败：%s" % (exc.stderr or exc))
    return path


def candidates(device):
    return ["float16", "int8_float16", "int8"] if device == "cuda" else ["int8", "int8_float32"]


def run_one(audio, model_name, device, compute, language="zh"):
    from faster_whisper import WhisperModel

    loaded = time.time()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:
        return {"compute_type": compute, "ok": False, "error": str(exc)}
    load_s = time.time() - loaded
    started = time.time()
    try:
        segs, info = model.transcribe(audio, language=language, vad_filter=True, beam_size=5,
                                      condition_on_previous_text=False,
                                      temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        segs = list(segs)
    except Exception as exc:
        return {"compute_type": compute, "ok": False, "error": str(exc)}
    elapsed = time.time() - started
    duration = float(getattr(info, "duration", 0) or 0)
    return {"compute_type": compute, "ok": True, "load_s": round(load_s, 2),
            "elapsed_s": round(elapsed, 2), "audio_s": round(duration, 2),
            "rtf": round(elapsed / duration, 3) if duration else None,
            "segments": len(segs), "chars": sum(len(s.text.strip()) for s in segs)}


def run(audio=None, synth=False, model_name="tiny", out_dir=None, computes=None,
        language="zh"):
    from .pipeline import InputError

    if not audio and not synth:
        raise InputError("要给 --audio 音频路径，或用 --synth 合成一段")
    if audio and not os.path.exists(audio):
        raise FileNotFoundError(audio)
    hw = hardware.resolve()
    if synth:
        audio = synth_audio()
    picks = [c.strip() for c in computes.split(",")] if computes else candidates(hw["device"])
    rows = [run_one(audio, model_name, hw["device"], c, language) for c in picks]
    result = {"schema": 1, "model": model_name, "device": hw["device"],
              "gpu": hw["name"], "compute_choice": hw["compute_type"],
              "reason": hw["reason"], "audio": audio, "runs": rows}
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "bench.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print("%-12s %7s %8s %8s %6s %6s %s" % ("compute", "load", "elapsed", "audio", "rtf", "segs", "note"))
    for r in rows:
        if r.get("ok"):
            print("%-12s %7.2f %8.2f %8.2f %6.2f %6d" % (r["compute_type"], r["load_s"],
                                                         r["elapsed_s"], r["audio_s"],
                                                         r["rtf"] or 0, r["segments"]))
        else:
            print("%-12s %8s %8s %6s %6s %s" % (r["compute_type"], "-", "-", "-", "-",
                                                (r.get("error") or "")[:60]))
    return result
