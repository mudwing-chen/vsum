# -*- coding: utf-8 -*-
"""faster-whisper 转写。模型是懒加载的：不跑转写就不会 import faster_whisper。

音频不走 av 解码：vsum 自己已经用 ffmpeg 抽了 16k 单声道 wav，这里直接用标准库
wave 读成 float32 数组喂给模型。这样 av 的版本变化（decode_audio 的 kvarg、
重采样行为）就完全影响不到转写结果——faster-whisper 只是把它当传递依赖装着。
"""
import hashlib
import json
import os
import time
import wave

try:  # numpy 是 faster-whisper 的依赖，正常装包一定在；CI 用 --no-deps 装包时可能没有
    import numpy as np
except ImportError:  # pragma: no cover - 只在无依赖的测试环境发生
    np = None

from . import export as export_mod, hardware, termrules, textnorm

DEFAULT_PROMPT = "以下是普通话视频内容。"
EN_PROMPT = "The following is an English video transcript."


def default_prompt(language):
    """按语言选默认提示词。

    中文提示词喂给英文音频，模型容易被往中文上带（连带制造静音段幻觉），
    而 `--lang en` 是 README 明确支持的用法。WHISPER_PROMPT 一旦设置就全局生效
    ——那是用户明确指定的；auto 不给提示词，因为此时还不知道是什么语言，
    硬塞一个就是纯偏置。
    """
    env = os.environ.get("WHISPER_PROMPT")
    if env is not None:
        return env
    lang = (language or "").strip().lower()
    if lang.startswith("zh"):
        return DEFAULT_PROMPT
    if lang.startswith("en"):
        return EN_PROMPT
    return ""
# 显式写死，进 manifest；温度回退会影响可复现性，但关掉它长音频更容易卡在复读上
TEMPERATURE = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
SAMPLE_RATE = 16000

_MODELS = {}

# OOM 降档阶梯：先降批大小，再降 compute，再降模型，最后退 CPU。
MODEL_LADDER = ("large-v3", "medium", "small", "base", "tiny")
OOM_HINTS = ("out of memory", "cuda_error_out_of_memory", "cublas_status_alloc_failed",
             "insufficient memory", "resource exhausted")


def resolve_model(model_name):
    """--model auto：CUDA 上取显存上限，CPU 上固定 small。

    model_cap 是「装得下」的上限，不是「跑得快」的建议；CPU 的 medium 慢到没法用。
    Pascal 的 cap 已由 hardware 压到 medium，这里不用再管。
    """
    if (model_name or "").strip().lower() != "auto":
        return model_name
    hw = hardware.resolve()
    if hw.get("device") != "cuda":
        return "small"
    return hw.get("model_cap") or "small"


def resolve_batch_size(batch_size, device, hw, quiet=False):
    """批推理大小：1 保持老行为；auto 按显存给 16/8/4；CPU 上禁用。"""
    if batch_size in (None, "", "auto"):
        if device != "cuda":
            return 1
        vram = hw.get("vram_gb") or 0
        return 16 if vram >= 16 else 8 if vram >= 8 else 4
    try:
        n = int(batch_size)
    except (TypeError, ValueError):
        raise ValueError("batch-size 只能是整数或 auto：%r" % (batch_size,))
    if n < 1:
        raise ValueError("batch-size 必须 >= 1")
    if device != "cuda" and n > 1:
        if not quiet:
            print("! CPU 上不支持批推理，batch-size=%d 视为 1" % n)
        return 1
    return n


def decode_options(raw=None):
    """归一化 --batch-size / VAD / chunk 参数，供复用比较与转写共用。"""
    raw = raw or {}
    hw = hardware.resolve()
    vad = {}
    for key in ("threshold", "min_speech_duration_ms", "min_silence_duration_ms", "speech_pad_ms"):
        if raw.get(key) is not None:
            vad[key] = raw[key]
    if raw.get("no_vad"):
        vad["no_vad"] = True
    return {"batch_size": resolve_batch_size(raw.get("batch_size", 1), hw.get("device"), hw),
            "vad_options": vad or None,
            "chunk_length": raw.get("chunk_length") or None}


def _is_oom(exc):
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    text = str(exc).lower()
    return any(h in text for h in OOM_HINTS)


def _next_attempt(state):
    """OOM 降档：batch → compute → 模型 → CPU。返回 (新状态, 说明) 或 (None, None)。"""
    model, device, compute, batch = state
    if device == "cuda" and batch > 1:
        nxt = 1 if batch <= 2 else batch // 2
        return (model, device, compute, nxt), "batch_size %d->%d" % (batch, nxt)
    if device == "cuda":
        if compute in ("float16", "float32", "int8_float32"):
            return (model, device, "int8_float16", batch), "compute_type %s->int8_float16" % compute
        if compute == "int8_float16":
            return (model, device, "int8", batch), "compute_type int8_float16->int8"
    if model in MODEL_LADDER:
        i = MODEL_LADDER.index(model)
        if i + 1 < len(MODEL_LADDER):
            comp = "int8_float16" if device == "cuda" else "int8"
            return (MODEL_LADDER[i + 1], device, comp, batch), "model %s->%s" % (
                model, MODEL_LADDER[i + 1])
    if device != "cpu":
        return (model, "cpu", "int8", 1), "device cuda->cpu"
    return None, None


def _empty_cuda_cache():
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


class DependencyError(RuntimeError):
    """缺 ffmpeg / faster-whisper / 模型。CLI 映射成退出码 3。"""


class TranscribeError(RuntimeError):
    """解码/转写失败（空音频、模型崩）。CLI 映射成退出码 4。"""


def av_incompatible(exc):
    """faster-whisper 旧版本 + av>=14 的已知崩法：metadata_errors 参数被删。

    vsum 现在不把路径交给 faster-whisper（见 load_audio），所以这条只作为
    用户手工调用 decode_audio 时的诊断提示保留。
    """
    return isinstance(exc, TypeError) and "metadata_errors" in str(exc)


def load_audio(path):
    """16k 单声道 PCM wav -> float32 [-1,1] 数组；读不了返回 None（退回到传路径）。"""
    if np is None:  # 没装 numpy：交给 faster-whisper 自己解码（它一定带 numpy）
        return None
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            raw = w.readframes(w.getnframes())
            rate = w.getframerate()
    except (wave.Error, OSError, EOFError):
        return None
    if not raw:
        return None
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE:
        return None  # 非 16k 交给 faster-whisper 自己重采样，别在这里做半套
    return audio


def to_simplified(text):
    """繁转简，返回 (文本, 是否可用)。规则见 vsum/textnorm.py；不影响时间戳。"""
    return textnorm.to_simplified(text), True


def ensure_cuda_libs():
    """把 site-packages/nvidia/*/bin 加进进程的 DLL 搜索路径。

    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 会把 dll 放在**包内的 bin/**，
    Windows 默认不去那儿找，于是 ctranslate2 报 `cublas64_12.dll is not found`——
    明明装了、明明有卡，就是跑不起来。这一步把那些目录挂上去。
    """
    roots = set()
    try:
        import site
        import sysconfig

        for key in ("purelib", "platlib"):
            path = sysconfig.get_paths().get(key)
            if path:
                roots.add(path)
        roots.update(site.getsitepackages() if hasattr(site, "getsitepackages") else [])
    except Exception:
        pass
    if np is not None:  # 顺便把当前解释器自己的 site-packages 也算进来
        roots.add(os.path.dirname(os.path.dirname(np.__file__)))
    added = []
    for root in sorted(r for r in roots if r):
        nv = os.path.join(root, "nvidia")
        if not os.path.isdir(nv):
            continue
        for sub in sorted(os.listdir(nv)):
            bindir = os.path.join(nv, sub, "bin")
            if not os.path.isdir(bindir):
                continue
            if bindir not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                added.append(bindir)
            try:
                os.add_dll_directory(bindir)
            except (AttributeError, OSError):
                pass
    return added


def load_hotwords(terms_path=None):
    """WHISPER_HOTWORDS=1 时把词表的 canonical 拼成提示词；默认关。"""
    if os.environ.get("WHISPER_HOTWORDS", "0").strip() in ("", "0"):
        return None
    try:
        with open(terms_path or termrules.DEFAULT_TERMS, encoding="utf-8") as fh:
            words = [t["canonical"] for t in json.load(fh)["terms"]]
    except Exception:
        return None
    return " ".join(words) or None


def _words_enabled():
    return os.environ.get("WHISPER_WORDS", "0").strip() not in ("", "0")


def segment_record(s, words=False):
    """单段 → segments.json 的记录。temperature 用来解释温度回退带来的文本差异。"""
    rec = {
        "start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": s.text.strip(),
        "avg_logprob": None if s.avg_logprob is None else round(float(s.avg_logprob), 4),
        "no_speech_prob": None if s.no_speech_prob is None else round(float(s.no_speech_prob), 4),
        "compression_ratio": None if s.compression_ratio is None else round(float(s.compression_ratio), 3),
        "temperature": None if getattr(s, "temperature", None) is None else round(float(s.temperature), 3),
    }
    if words:
        rec["words"] = [
            {"start": round(float(w.start), 2), "end": round(float(w.end), 2),
             "word": w.word,
             "probability": None if w.probability is None else round(float(w.probability), 3)}
            for w in (s.words or [])
        ]
    return rec


def fallback_count(segments):
    """温度回退过的段数。0 是「两次跑文本一致」的必要条件（不是充分条件）。"""
    return sum(1 for s in segments if s.get("temperature"))


def infer_kwargs(language, terms_path=None, words=None, context=True,
                 vad_options=None, batch_size=1, chunk_length=None):
    """与硬件无关的那部分解码参数。

    复用判断也要用它，所以单独拎出来：那边不需要知道跑在 GPU 还是 CPU 上，
    只需要知道「设置变没变」。默认值刻意保持 0.6 的形状（不传 vad_parameters、
    不传 chunk_length），老产物的 config_sha256 才不会因为升级就失效。
    """
    vad = vad_options or {}
    if words is None:
        words = _words_enabled()
    prompt = default_prompt(language)
    hot = load_hotwords(terms_path)
    # 开上下文后切句更细、逗号更多，摘要才挑得出整句；WHISPER_COND=0 可全局退回。
    cond = bool(context) and os.environ.get("WHISPER_COND", "1").strip() not in ("", "0")
    if (batch_size or 1) > 1:
        # 批推理按窗口独立解码，condition_on_previous_text 实际不起作用；记 False，
        # 别让 manifest 写一个没生效的 True。
        cond = False
    # faster-whisper 只认 ISO 语言码：传 "auto" 会在 Tokenizer 里直接 ValueError。
    # 自动检测在它那边的写法是 language=None。
    lang_arg = None if str(language or "").strip().lower() == "auto" else language
    kwargs = dict(language=lang_arg, vad_filter=not vad.get("no_vad", False), beam_size=5,
                  condition_on_previous_text=cond,
                  temperature=TEMPERATURE, initial_prompt=prompt)
    params = {}
    for key in ("threshold", "min_speech_duration_ms", "min_silence_duration_ms", "speech_pad_ms"):
        if vad.get(key) is not None:
            params[key] = vad[key]
    if params and kwargs["vad_filter"]:
        kwargs["vad_parameters"] = params
    if chunk_length:
        kwargs["chunk_length"] = int(chunk_length)
    if hot:
        kwargs["hotwords"] = hot
    if words:
        kwargs["word_timestamps"] = True
    return kwargs


def config_digest(language, terms_path=None, words=None, context=True,
                  batch_size=1, vad_options=None, chunk_length=None):
    """解码参数指纹：任何一个会改变输出的参数变了，指纹就变。

    manifest 记一份，复用前对一下——比逐项列参数更不容易漏（--words、热词内容、
    WHISPER_PROMPT 都在里面）。批大小只在 >1 时进指纹：默认 1 与旧 manifest 兼容。
    """
    payload = infer_kwargs(language, terms_path, words, context, vad_options,
                           batch_size, chunk_length)
    if (batch_size or 1) > 1:
        payload["batch_size"] = int(batch_size)
    payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decode_args(model_name, language, terms_path=None, words=None, context=True,
                batch_size=1, vad_options=None, chunk_length=None):
    """返回 (构造参数, 记录进 manifest 的参数字典)。

    context = condition_on_previous_text：中文实测差别很大。True 会沿用上文，
    输出简体、带标点、句子完整；False 每段当全新的听，会漂成繁体、丢标点，
    连摘要都没法按标点切句。默认 True，要老行为用 --no-context。
    """
    hw = hardware.resolve()
    device = hw["device"]
    compute = hw["compute_type"]
    batch = resolve_batch_size(batch_size, device, hw)
    kwargs = infer_kwargs(language, terms_path, words, context, vad_options, batch, chunk_length)
    recorded = {
        "model": model_name, "device": device, "compute_type": compute,
        "language": language, "beam_size": kwargs["beam_size"],
        "vad_filter": kwargs["vad_filter"],
        "condition_on_previous_text": kwargs["condition_on_previous_text"],
        "temperature": list(kwargs["temperature"]), "t2s": True,
        "initial_prompt": kwargs["initial_prompt"],
        "hotwords": bool(kwargs.get("hotwords")),
        "word_timestamps": bool(kwargs.get("word_timestamps")),
        "batch_size": batch,
        "config_sha256": config_digest(language, terms_path, words, context, batch,
                                       vad_options, chunk_length),
        "device_source": hw["source"], "gpu": hw["name"], "model_cap": hw["model_cap"],
        "audio_input": "ndarray",
    }
    if kwargs.get("vad_parameters"):
        recorded["vad_parameters"] = dict(kwargs["vad_parameters"])
    if chunk_length:
        recorded["chunk_length"] = int(chunk_length)
    return kwargs, recorded, device, compute


def get_model(model_name, device, compute, cache=True):
    """模型缓存：同一进程里反复用同一个模型时不要重复加载。

    批量跑 N 条视频，每条重新 WhisperModel(...) 就要重付一次 CUDA 初始化
    （本机实测 20~40 秒）。batch 走的就是同一条进程，所以这里必须缓存。
    """
    key = (model_name, device, compute)
    if cache and key in _MODELS:
        return _MODELS[key], True
    from faster_whisper import WhisperModel

    if device == "cuda":
        ensure_cuda_libs()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    if cache:
        _MODELS[key] = model
    return model, False


CUDA_HINTS = ("cublas", "cudnn", "cuda", "no kernel image", "device is detected")

CACHE_HINTS = {
    "broken": "缓存目录的软链指向了不存在的目录（换盘后最常见）："
              "把 HF_HOME 指到可写目录，如 set HF_HOME=D:\\hf / export HF_HOME=~/hf",
    "denied": "缓存目录写不进去：把 HF_HOME 指到可写目录，"
              "如 set HF_HOME=D:\\hf / export HF_HOME=~/hf",
    "absent": "缓存目录还没建，上级目录不可写：把 HF_HOME 指到可写目录",
}


def hf_root():
    """huggingface_hub 实际会用的缓存根目录，优先级与它自己一致。"""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return os.path.join(xdg, "huggingface", "hub")
    return os.path.expanduser("~/.cache/huggingface/hub")


def cache_state(path=None):
    """缓存目录能不能用：ok / denied / broken / absent。

    只读判断，绝不在这里建文件：junction 指向已移除的磁盘时，
    在它下面建文件会卡到 I/O 超时（实测能把 doctor 挂死）。
    """
    path = hf_root() if path is None else path
    if os.path.isdir(path):
        return "ok" if os.access(path, os.W_OK) else "denied"
    if os.path.lexists(path):
        # 目录项在、但解析不到目录：软链/junction 指的地方没了
        return "broken"
    probe = path
    while probe and not os.path.isdir(probe):
        if os.path.lexists(probe):
            return "broken"
        parent = os.path.dirname(probe)
        if parent == probe:
            return "absent"
        probe = parent
    return "ok" if os.access(probe, os.W_OK) else "denied"


def cache_hint(exc):
    """模型加载失败时的缓存提示，没有这类迹象就返回空串。

    换盘/搬家后 ~/.cache/huggingface 指向已移除磁盘是最常见的一种：原始报错可能
    是 [WinError 5]，也可能是下载器内部的 TypeError，总之都看不出跟缓存有关。
    """
    text = str(exc).lower()
    looks_like_cache = isinstance(exc, PermissionError) or any(
        k in text for k in ("permission", "access is denied", "拒绝访问", "winerror 5",
                            "read-only file system", "metadata_errors"))
    if not looks_like_cache:
        return ""
    state = cache_state()
    if state == "ok":
        return ""
    return "\n  %s\n  当前缓存目录：%s" % (CACHE_HINTS[state], hf_root())


def _looks_like_cuda_problem(exc):
    text = str(exc).lower()
    return any(h in text for h in CUDA_HINTS)


def _collect(segs, recorded, started, on_progress):
    """把生成器吃干净：转字、记段、按需报进度。异常由调用方统一映射。"""
    lines, segments = [], []
    last_report = 0.0
    t2s_ok = False
    for s in segs:
        t = s.text.strip()
        if t:
            # Whisper 没有正字法约束：实测 13:47 的稿子里 2.5% 的字落成繁体。
            # 这里只转字，不动时间戳与标点。
            t, _ok = to_simplified(t)
            t2s_ok = True
            lines.append("[%6.1f -> %6.1f] %s" % (s.start, s.end, t))
            rec = segment_record(s, recorded["word_timestamps"])
            rec["text"] = t
            segments.append(rec)
        now = time.time()
        if now - last_report > 15:
            last_report = now
            if on_progress:
                on_progress(len(segments), s.end, now - started)
            else:
                print("PROGRESS segments=%d at=%.1fs elapsed=%.0fs" % (len(segments), s.end, now - started),
                      flush=True)
    return lines, segments, t2s_ok


def _drop_model(model_name, device, compute):
    """OOM 后把缓存里的模型丢掉：留着只会继续占显存，让降档更容易再 OOM。"""
    _MODELS.pop((model_name, device, compute), None)


def _next_load_state(state):
    """加载期 OOM 的降档：先降模型，实在没有了再退 CPU。"""
    m, dev, comp, bs = state
    if m in MODEL_LADDER:
        i = MODEL_LADDER.index(m)
        if i + 1 < len(MODEL_LADDER):
            nxt = MODEL_LADDER[i + 1]
            return (nxt, dev, "int8_float16" if dev == "cuda" else "int8", 1), \
                "模型 %s → %s" % (m, nxt)
    if dev == "cuda":
        return (m, "cpu", "int8", 1), "设备 %s → cpu" % dev
    return None, ""


def _run_model(model, payload, kwargs, batch_size, recorded):
    """按 batch_size 决定走串行还是 BatchedInferencePipeline。

    faster-whisper 各版本的 BatchedInferencePipeline.transcribe 参数集不一样；
    参数不兼容就退回串行并记 warning——用户要的是产物，不是实现方式。
    """
    if not batch_size or batch_size <= 1:
        segs, info = model.transcribe(payload, **kwargs)
        return segs, info, "serial"
    reason = None
    try:
        from faster_whisper import BatchedInferencePipeline

        segs, info = BatchedInferencePipeline(model).transcribe(
            payload, batch_size=batch_size, **kwargs)
        return segs, info, "batched"
    except (ImportError, AttributeError) as exc:
        reason = str(exc)[:200]
        print("! 当前 faster-whisper 没有 BatchedInferencePipeline（%s），退回串行" % reason)
    except TypeError as exc:
        text = str(exc).lower()
        if "keyword" not in text and "argument" not in text:
            raise
        reason = str(exc)[:200]
        print("! 批推理不支持当前参数（%s），退回串行" % reason)
    recorded["batch_fallback"] = reason
    recorded["batch_size"] = 1
    segs, info = model.transcribe(payload, **kwargs)
    return segs, info, "serial"


def transcribe_audio(audio_path, out_dir, model_name="small", language="zh",
                     terms_path=None, words=None, on_progress=None, context=True,
                     batch_size=1, vad_options=None, chunk_length=None):
    """跑一次转写，写 transcript.txt + segments.json，返回 (segments, info, decode 记录)。

    OOM 按 batch → compute → 模型 → CPU 的阶梯降档重跑，最多 3 次；每次都从头
    解码，绝不写半截产物。降档过程记进 manifest 的 decode.fallbacks。
    """
    model_name = resolve_model(model_name)
    kwargs, recorded, device, compute = decode_args(
        model_name, language, terms_path, words, context,
        batch_size=batch_size, vad_options=vad_options, chunk_length=chunk_length)
    state = (model_name, device, compute, recorded["batch_size"])
    started = time.time()
    audio = load_audio(audio_path)
    payload = audio if audio is not None else audio_path
    retries = 0
    info = None
    while True:
        m, dev, comp, bs = state
        try:
            model, cached = get_model(m, dev, comp, cache=(retries == 0))
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise DependencyError("faster-whisper 未安装：pip install faster-whisper（%s）" % exc)
        except Exception as exc:
            if dev == "cuda" and _is_oom(exc):
                nxt, why = _next_load_state(state)
                if nxt is not None and retries < 3:
                    print("! 加载模型时显存不足（%s），降档：%s" % (str(exc)[:120], why))
                    recorded.setdefault("fallbacks", []).append(
                        {"reason": "oom_load", "from": "%s/%s" % (m, dev),
                         "to": "%s/%s" % (nxt[0], nxt[1]), "error": str(exc)[:200]})
                    state = nxt
                    retries += 1
                    _empty_cuda_cache()
                    continue
                print("! 加载模型时显存不足（%s），退回 CPU" % str(exc)[:120])
                recorded.setdefault("fallbacks", []).append(
                    {"reason": "oom_load", "from": "%s/%s" % (m, dev),
                     "to": "cpu/int8", "error": str(exc)[:200]})
                state = (m, "cpu", "int8", 1)
                retries += 1
                _empty_cuda_cache()
                continue
            if dev == "cuda" and _looks_like_cuda_problem(exc):
                print("! CUDA 起不来（%s），退回 CPU" % str(exc)[:120])
                print("! 想让 GPU 生效：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
                recorded.setdefault("fallbacks", []).append(
                    {"reason": "cuda_load", "from": "%s/%s" % (dev, comp),
                     "to": "cpu/int8", "error": str(exc)[:200]})
                if retries >= 3:
                    raise DependencyError(
                        "CUDA 起不来，退回 CPU 也加载失败（%s / cpu / int8）：%s%s" % (
                            m, exc, cache_hint(exc)))
                state = (m, "cpu", "int8", 1)
                retries += 1
                continue
            if dev == "cpu" and any(f.get("reason") == "cuda_load"
                                    for f in recorded.get("fallbacks") or []):
                raise DependencyError(
                    "CUDA 起不来，退回 CPU 也加载失败（%s / cpu / int8）：%s%s" % (
                        m, exc, cache_hint(exc)))
            raise DependencyError("模型加载失败（%s / %s / %s）：%s%s" % (
                m, dev, comp, exc, cache_hint(exc)))
        if cached:
            print("模型已在内存里（%s / %s），跳过加载" % (m, dev))
        try:
            segs, info, mode = _run_model(model, payload, kwargs, bs, recorded)
            if mode == "serial" and state[3] > 1:
                # 批推理已经退回串行：之后再 OOM 别再沿 4→2→1 空跑，串行没有 batch 可减
                state = (m, dev, comp, 1)
            # faster-whisper 返回的是生成器，推理发生在 for 迭代里：不把迭代包进来，
            # 长音频跑到一半遇到 CUDA OOM / 解码错误会直接穿到 CLI（traceback + 1），
            # 而不是文档承诺的 4。
            lines, segments, t2s_ok = _collect(segs, recorded, started, on_progress)
            break
        except Exception as exc:
            if av_incompatible(exc):
                raise DependencyError(
                    "av 版本过新：faster-whisper 的 decode_audio 与 av>=14 不兼容，"
                    "而 vsum 本不该走到解码这条路（传的是 wav 数组）。"
                    "请确认输入是 16k 单声道 wav，或 pip install 'av<14'") from exc
            if not _is_oom(exc):
                raise TranscribeError("转写失败：%s" % exc) from exc
            _drop_model(m, dev, comp)
            nxt, why = _next_attempt(state)
            if nxt is None or retries >= 3:
                raise TranscribeError(
                    "转写失败：显存不足，降档 %d 次仍失败（最后尝试 %s / %s / batch %d）：%s"
                    % (retries, m, dev, bs, exc)) from exc
            print("! 显存不足（%s），降档：%s" % (str(exc)[:120], why))
            recorded.setdefault("fallbacks", []).append(
                {"reason": "oom", "from": "%s/%s/batch%d" % (m, dev, bs),
                 "to": "%s/%s/batch%d" % (nxt[0], nxt[1], nxt[3]),
                 "error": str(exc)[:200]})
            state = nxt
            retries += 1
            _empty_cuda_cache()
            continue

    # 实际跑成的组合写回 manifest：降档后 model/device/compute 可能都变了。
    recorded["model"] = state[0]
    recorded["device"] = state[1]
    recorded["compute_type"] = state[2]
    recorded["batch_size"] = 1 if mode == "serial" else state[3]
    if state[3] > 1:
        print("批推理：batch_size=%d（窗口独立解码，切段与串行不完全一致）" % state[3])

    if (language or "").strip().lower() == "auto":
        # manifest 的 language 记的是请求值（复用要比这个），实际语言另记一份：
        # 否则 --lang auto 的产物头永远写着 auto，看稿的人不知道这是什么语言。
        recorded["detected_language"] = getattr(info, "language", None)

    # 空音频 / VAD 把整段滤光：产物全空但「成功」是最容易骗过脚本的结果，
    # 按 TranscribeError 的口径算失败，也别把空文件写成「这轮的产物」。
    if not segments:
        raise TranscribeError("没有识别到任何语音（VAD 把整段过滤掉了，或音频是静音）：%s"
                              % os.path.basename(audio_path))

    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(out_dir, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": segments}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    recorded["t2s"] = t2s_ok
    recorded["fallback_segments"] = fallback_count(segments)
    # 和 transcript.md 里的 ⚠ 同一口径：n 是标记总数，times 最多列 10 个时间点
    low = export_mod.low_conf_indexes(segments)
    low_times = [s["start"] for i, s in enumerate(segments, 1) if i in low]
    if low_times:
        print("LOW_CONF n=%d times=%s" % (len(low_times),
                                         ",".join("%.1f" % t for t in low_times[:10])), flush=True)
    print("OK_SEGMENTS=%d CHARS=%d" % (len(lines), sum(len(s["text"]) for s in segments)))
    print("LANG=%s DUR=%.1f ELAPSED=%.1fs" % (info.language, info.duration, time.time() - started))
    return segments, info, recorded
