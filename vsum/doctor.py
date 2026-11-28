# -*- coding: utf-8 -*-
"""vsum doctor：逐项检查依赖，缺的给安装命令。浏览器只报可用性，不检查登录态。"""
import importlib.util
import os
import platform
import shutil
import subprocess
import sys

from . import hardware
from .toolpath import which
from .transcribe import CACHE_HINTS, cache_state, hf_root, resolve_model

INSTALL = "pip install 'vsum[%s] @ git+https://github.com/mudwing-chen/vsum'"


def install_hint(extra):
    """PyPI 上的 vsum 是另一个项目：装 extras 必须指到本仓库。"""
    return INSTALL % extra


def ffmpeg_hint(system=None):
    """各平台的 ffmpeg 安装命令；Windows 不再给 apt 建议。"""
    system = sys.platform if system is None else system
    if system.startswith("darwin"):
        return "brew install ffmpeg"
    if system in ("win32", "cygwin", "msys"):
        return "winget install Gyan.FFmpeg（或把 ffmpeg.exe 放进 PATH）"
    return "apt install ffmpeg"


def _first_line(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0]
    except Exception as exc:
        return "error: %s" % exc


def _model_cached(model):
    """模型可用性：本地目录直接认；否则看 HF 缓存里有没有下过同名模型。"""
    if os.path.isdir(model):
        return model
    roots = [os.environ.get("HF_HUB_CACHE"),
             os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub"),
             os.path.expanduser("~/.cache/huggingface/hub")]
    name = "models--Systran--faster-whisper-" + model
    for root in roots:
        if root and os.path.isdir(os.path.join(root, name)):
            return os.path.join(root, name)
    return None


def collect(model="small"):
    """收集检查项，返回 (rows, ok)。run() 只负责打印和退出码。"""
    rows, ok = [], True

    def add(name, good, detail, hint=""):
        nonlocal ok
        if not good:
            ok = False
        rows.append((name, "ok" if good else "MISSING", detail, hint))

    def warn(name, good, detail, hint=""):
        """建议项：上表可见，但不影响退出码（模型没缓存、磁盘紧都能继续跑）。"""
        rows.append((name, "ok" if good else "warn", detail, hint))

    add("python", sys.version_info >= (3, 9), "%d.%d.%d" % sys.version_info[:3],
        "需要 3.9+")

    ffmpeg = which("ffmpeg")
    add("ffmpeg", bool(ffmpeg), ffmpeg or "not found",
        ffmpeg_hint())
    if ffmpeg:
        rows[-1] = ("ffmpeg", "ok", "%s  %s" % (ffmpeg, _first_line([ffmpeg, "-version"])), "")

    # vsum 自己不用 ffprobe：时长探测会退回 PyAV，抽音轨/剪片只用 ffmpeg。
    # 缺了它只是 manifest 里少个 duration，不该把 doctor 判成失败。
    ffprobe = which("ffprobe")
    warn("ffprobe", bool(ffprobe), ffprobe or "not found（可选）",
         "随 ffmpeg 一起装更省事；没有就退回 PyAV 探测时长")

    spec = importlib.util.find_spec("faster_whisper")
    version = ""
    if spec:
        try:
            from importlib.metadata import version as v

            version = v("faster-whisper")
        except Exception:
            version = "installed"
    add("faster-whisper", bool(spec), version or "not found", "pip install faster-whisper")

    try:
        from importlib.metadata import version as _v

        av_ver = _v("av")
        major = int(av_ver.split(".")[0])
        # vsum 把 wav 读成数组再喂模型，不走 av 的解码路径，所以 av 大版本不再卡人。
        # 只是 faster-whisper 自己 import 时会用到它，能装上就够。
        add("av", major >= 11, av_ver,
            "faster-whisper 要 av>=11；vsum 自己不锁版本，装 faster-whisper 时会自动带入")
    except Exception:
        add("av", False, "not found",
            "随 faster-whisper 一起装（vsum 不锁 av 版本）：pip install faster-whisper")

    hw = hardware.resolve()
    device_detail = "%s / %s" % (hw["device"], hw["compute_type"])
    if hw.get("reason"):
        device_detail += "  " + hw["reason"]
    add("device", True, device_detail,
        "WHISPER_DEVICE / WHISPER_COMPUTE 可覆盖；实测 RTF 用 vsum bench")
    add("model_cap", True, hw["model_cap"], "按显存推的建议上限，不强制；真的快慢看 vsum bench")
    if (model or "").strip().lower() == "auto":
        chosen = resolve_model("auto")
        why = ("显存上限" if hw["device"] == "cuda" else "CPU 固定 small")
        add("plan", True, "auto → %s（%s）" % (chosen, why),
            "vsum bench --model auto 量一下值不值得")
    if hw["name"]:
        add("gpu", True, "%s %.1fGB cc%.1f" % (hw["name"], hw["vram_gb"], hw["cc"]), hw["reason"])

    from . import browser as _browser

    chrome = _browser.find_binary("chrome")
    edge = _browser.find_binary("edge")
    has_sel = importlib.util.find_spec("selenium") is not None
    detail = "selenium=%s chrome=%s edge=%s" % (
        "yes" if has_sel else "no",
        "yes" if chrome else "no",
        "yes" if edge else "no")
    if has_sel and (chrome or edge):
        rows.append(("browser", "ok", detail, ""))
    else:
        rows.append(("browser", "extra", detail,
                     "链接抓取不可用（本地文件不受影响）：%s 并装 Chrome 或 Edge"
                     % install_hint("browser")))

    # yt-dlp 只有 vsum fetch 抓 B站 / YouTube 才用；本地文件、抖音走浏览器，不依赖它
    try:
        has_ytdlp = importlib.util.find_spec("yt_dlp") is not None
    except Exception:
        has_ytdlp = False
    if has_ytdlp:
        try:
            from importlib.metadata import version as _yv

            ytdlp_ver = _yv("yt-dlp")
        except Exception:
            ytdlp_ver = "installed"
        rows.append(("yt-dlp", "ok", ytdlp_ver, ""))
    else:
        rows.append(("yt-dlp", "extra", "未装",
                     "B站 / YouTube 抓取不可用（本地文件与抖音不受影响）：%s"
                     % install_hint("yt")))

    # 缓存目录不可用是「模型明明下过却加载不了」的头号原因（换盘后软链断掉最常见），
    # 单独一行，别让它伪装成「未缓存」。
    root = hf_root()
    state = cache_state(root)
    if state == "ok":
        rows.append(("hf-cache", "ok", root, ""))
    else:
        # 记 warn 而不是 MISSING：缓存目录缺失会自己重建，缓存没下过也能自动下载，
        # 这两件事都不该让 doctor 退出 3（真加载失败时转写本身会报 3）。
        warn("hf-cache", False, "%s（%s）" % (root, state), CACHE_HINTS.get(state, ""))

    # --model auto 要按显存解析成具体名字再查缓存：拿 "auto" 去拼
    # models--Systran--faster-whisper-auto 永远不存在，small 明明缓存了也会报未缓存。
    target = resolve_model(model)
    cached = _model_cached(target)
    detail = cached or "未缓存"
    if target != model:
        detail = "→ %s（%s）" % (target, detail)
    warn("model:%s" % model, bool(cached), detail,
         "首次运行会自动下载（small 约 480 MB / medium 约 1.5 GB），或先跑一次 vsum 让它下载")

    # 抽帧 OCR 是可选依赖：只影响 vsum ocr，不影响转写
    try:
        has_ocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:
        has_ocr = False
    if has_ocr:
        rows.append(("ocr", "ok", "rapidocr 已装", ""))
    else:
        rows.append(("ocr", "extra", "未装",
                     "抽帧 OCR 不可用（转写不受影响）：%s" % install_hint("ocr")))

    free = shutil.disk_usage(os.getcwd()).free / (1 << 30)
    warn("disk", free > 2, "%.1f GB free" % free, "reserve >2 GB for audio + model")

    # 安装提示撞名：PyPI 上的 `vsum` 是另一个项目，只看发行版元数据里的作者/主页
    try:
        from importlib.metadata import metadata as _metadata
        dist_meta = _metadata("vsum")
    except Exception:
        dist_meta = None
    if dist_meta is None:
        rows.append(("dist", "extra", "未安装（源码运行）",
                     "安装请指到本仓库：%s" % install_hint("all")))
    else:
        blob = " ".join(str(x) for key in ("Author", "Author-email", "Home-page", "Project-URL")
                        for x in (dist_meta.get_all(key) or []))
        if "mudwing" in blob.lower():
            rows.append(("dist", "ok", "mudwing-chen/vsum", ""))
        else:
            warn("dist", False, "同名发行版不是本项目",
                 "PyPI 上的 vsum 是另一个项目；卸掉重装：pip uninstall vsum && %s"
                 % install_hint("all"))

    return rows, ok


def run(model="small"):
    rows, ok = collect(model)
    for name, state, detail, hint in rows:
        line = "%-14s %-8s %-46s" % (name, state, detail)
        if hint and state != "ok":
            line += "  " + hint
        print(line.rstrip())
    if sys.platform == "darwin" and platform.machine() == "arm64" and sys.version_info < (3, 10):
        print("note          macOS arm64 + Python 3.9 只能配旧版依赖："
              "onnxruntime<=1.16.3 / av<=13.1.0 / tokenizers<=0.21.4。"
              "上表 python 显示 ok 不代表 pip install faster-whisper 能装成；"
              "稳妥做法是 uv venv --python 3.12 再装。")
    return 0 if ok else 3
