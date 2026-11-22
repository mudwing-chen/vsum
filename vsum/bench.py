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


def candidates(device, preferred=None):
    """要试的 compute_type，本机实际会用的那个排第一。

    Pascal（P106 这类）上 float16 是软件模拟，先试它会白等好几分钟；
    preferred 给的是 hardware.resolve() 的选择，所以默认顺序 = 真实默认路径。
    """
    order = ["float16", "int8_float16", "int8"] if device == "cuda" else ["int8", "int8_float32"]
    if not preferred:
        return order
    return [preferred] + [c for c in order if c != preferred]


def to_wav(src, workdir):
    """转成 16k 单声道 wav，后面用数组喂模型。

    直接把文件路径交给 faster-whisper，它会走 av 解码；而 av 16+ 去掉了
    ``metadata_errors`` 这个关键字参数，faster_whisper 1.2.x 仍在传，于是
    ``open() got an unexpected keyword argument`` 直接炸。vsum 主路径本来就
    绕开 av（见 transcribe.load_audio），bench 必须走同一条路。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise BenchError("找不到 ffmpeg，没法把音频转成 16k 单声道 wav")
    wav = os.path.join(workdir, "bench-16k.wav")
    proc = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", src, "-vn",
                           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(wav):
        raise BenchError("ffmpeg 抽音频失败：%s" % (proc.stderr.strip()[:300] or "exit %d" % proc.returncode))
    return wav


def run_one(audio, model_name, device, compute, language="zh"):
    from .transcribe import get_model, load_audio

    payload = load_audio(audio)
    if payload is None:
        payload = audio
    loaded = time.time()
    try:
        # 走 get_model 而不是直接 WhisperModel：它负责把 nvidia-* 轮子里的
        # cublas/cudnn 挂进 DLL 搜索路径，bench 自己 new 会报 cublas64_12.dll 找不到。
        # cache=False：每换一个 compute_type 都要重新量加载耗时。
        model, _ = get_model(model_name, device, compute, cache=False)
    except Exception as exc:
        return {"compute_type": compute, "ok": False, "error": str(exc)}
    load_s = time.time() - loaded
    started = time.time()
    try:
        segs, info = model.transcribe(payload, language=language, vad_filter=True, beam_size=5,
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
    made_tmp = out_dir is None
    workdir = out_dir or tempfile.mkdtemp(prefix="vsum-bench-")
    os.makedirs(workdir, exist_ok=True)
    try:
        wav = to_wav(audio, workdir)
        picks = ([c.strip() for c in computes.split(",")] if computes
                 else candidates(hw["device"], hw["compute_type"]))
        rows = [run_one(wav, model_name, hw["device"], c, language) for c in picks]
    finally:
        if made_tmp:
            shutil.rmtree(workdir, ignore_errors=True)
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
